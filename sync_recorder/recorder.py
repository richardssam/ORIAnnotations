"""Sync session event recorder.

Allows recording all messages broadcast on a sync session exchange.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import time
from typing import Any

# Ensure we can import otio_sync_core
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "python"))

from otio_sync_core import RabbitMQNetwork


class SyncRecorder:
    """Records all network payloads of an OTIO sync session to a file or in-memory list.

    :param session_id: Logical session identifier to record.
    :param host: RabbitMQ broker hostname.
    :param port: RabbitMQ broker AMQP port.
    :param network: Optional pre-configured network backend. If provided,
        *host* and *port* are ignored.
    """

    def __init__(
        self,
        session_id: str = "otio-sync-demo",
        host: str = "localhost",
        port: int = 5672,
        network: Any | None = None,
        capture_initial_state: bool = True,
    ) -> None:
        self.session_id = session_id
        self.host = host
        self.port = port
        self.network = network
        self.capture_initial_state = capture_initial_state
        self.events: list[dict[str, Any]] = []
        self.output_file: str | None = None
        self._start_time: float | None = None
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._own_network = False

        # Handshake state variables
        self._handshake_state = "RECORDING"
        self._handshake_master = None
        self._handshake_sent_discover = 0.0
        self._handshake_sent_request = 0.0
        self._handshake_start_time = 0.0
        self._snapshot_captured = False
        self._handshake_state_request_start = 0.0

    def start(self, output_file: str | None = None) -> None:
        """Start recording events in a background thread.

        If *output_file* is provided, events are written immediately to that
        file path in JSON Lines format, truncating any existing file at that path.

        :param output_file: Optional file path to write recorded events to.
        """
        with self._lock:
            if self._poll_thread is not None and self._poll_thread.is_alive():
                return

            self.output_file = output_file
            self._file_handle = None
            if self.output_file:
                # Open the file for writing and keep it open
                self._file_handle = open(self.output_file, "w", encoding="utf-8")

            if self.network is None:
                # We generate a unique GUID so we don't drop messages from any peer
                self.network = RabbitMQNetwork(
                    host=self.host,
                    port=self.port,
                    session_id=self.session_id,
                )
                self._own_network = True

            if hasattr(self.network, "wait_until_ready"):
                self.network.wait_until_ready(timeout=5.0)

            self.events.clear()
            self._start_time = time.time()
            self._stop_event.clear()

            # Initialize handshake state if capture is enabled
            if self.capture_initial_state:
                self._handshake_state = "DISCOVERING"
                self._handshake_master = None
                self._handshake_sent_discover = 0.0
                self._handshake_sent_request = 0.0
                self._handshake_start_time = time.time()
                self._snapshot_captured = False
                self._handshake_state_request_start = 0.0
            else:
                self._handshake_state = "RECORDING"
                self._snapshot_captured = True

            self._poll_thread = threading.Thread(
                target=self._run_poll, daemon=True, name="recorder_poll"
            )
            self._poll_thread.start()

    def stop(self) -> None:
        """Stop the background recording thread and close the network backend."""
        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None

        with self._lock:
            if self._file_handle:
                try:
                    self._file_handle.flush()
                    self._file_handle.close()
                except Exception as e:
                    print(f"[recorder] Error closing file: {e}", file=sys.stderr)
                self._file_handle = None

            if self._own_network and self.network is not None:
                try:
                    self.network.stop()
                except Exception as e:
                    print(f"[recorder] Error stopping network: {e}", file=sys.stderr)
                self.network = None
                self._own_network = False

    def tick(self) -> list[dict[str, Any]]:
        """Check for new incoming payloads, record them, and return the new events.

        This can be called procedurally within an application's own main/idle loop.

        :returns: List of new events recorded during this tick.
        :rtype: list[dict[str, Any]]
        """
        if self.network is None:
            return []

        with self._lock:
            if self._start_time is None:
                self._start_time = time.time()

            now = time.time()

            # Drive state-capture handshake if enabled
            if self.capture_initial_state and not self._snapshot_captured:
                if self._handshake_state == "DISCOVERING":
                    if now - self._handshake_sent_discover > 1.0:
                        self.network.send_payload({
                            "command": "SESSION",
                            "event": "WHO_IS_MASTER",
                            "session_id": self.session_id,
                            "payload": {"requester_guid": self.network.self_guid}
                        })
                        self._handshake_sent_discover = now
                elif self._handshake_state == "REQUESTING_STATE":
                    # Time out requesting state after 4.0s of request attempts
                    if now - self._handshake_state_request_start > 4.0:
                        self._handshake_state = "DISCOVERING"
                        self._handshake_master = None
                    elif now - self._handshake_sent_request > 2.0:
                        self.network.send_payload({
                            "command": "SESSION",
                            "event": "STATE_REQUEST",
                            "session_id": self.session_id,
                            "payload": {
                                "target_guid": self._handshake_master,
                                "requester_guid": self.network.self_guid
                            }
                        })
                        self._handshake_sent_request = now

            payloads = self.network.receive_payloads()
            new_events = []
            for p in payloads:
                cmd = p.get("command")
                evt = p.get("event")
                payload_data = p.get("payload", {})

                # Update handshake state machine based on received payloads
                if self.capture_initial_state and not self._snapshot_captured:
                    if (self._handshake_state == "DISCOVERING" or self._handshake_state == "RECORDING") and cmd == "SESSION" and evt == "I_AM_MASTER":
                        self._handshake_master = payload_data.get("master_guid")
                        if self._handshake_master:
                            self._handshake_state = "REQUESTING_STATE"
                            self._handshake_sent_request = 0.0  # Force immediate request
                            self._handshake_state_request_start = now
                    elif self._handshake_state == "REQUESTING_STATE" and cmd == "SESSION" and evt == "STATE_SNAPSHOT":
                        if payload_data.get("target_guid") == self.network.self_guid:
                            self._handshake_state = "RECORDING"
                            self._snapshot_captured = True

                offset = now - self._start_time
                event = {
                    "time_offset": offset,
                    "absolute_time": now,
                    "payload": p,
                }
                self.events.append(event)
                if self._file_handle:
                    self._write_event_to_file(event)
                new_events.append(event)
            return new_events

    def get_events(self) -> list[dict[str, Any]]:
        """Return a copy of the list of recorded events.

        :returns: List of event dicts.
        :rtype: list[dict[str, Any]]
        """
        with self._lock:
            return list(self.events)

    def write_to_file(self, filepath: str) -> None:
        """Write all recorded events in memory to a JSON Lines file.

        :param filepath: Path to save the events to.
        """
        with self._lock:
            with open(filepath, "w", encoding="utf-8") as f:
                for event in self.events:
                    f.write(json.dumps(event) + "\n")

    def _run_poll(self) -> None:
        """Internal polling loop run in a background thread."""
        while not self._stop_event.is_set():
            self.tick()
            time.sleep(0.05)

    def _write_event_to_file(self, event: dict[str, Any]) -> None:
        """Internal helper to append an event to the output file."""
        if not self._file_handle:
            return
        try:
            self._file_handle.write(json.dumps(event) + "\n")
            self._file_handle.flush()
        except Exception as e:
            print(f"[recorder] Failed to write event to file: {e}", file=sys.stderr)


def main() -> None:
    """Entry point for running the recorder from the command line."""
    p = argparse.ArgumentParser(description="OTIO Sync Session Recorder")
    p.add_argument("--session", default="otio-sync-demo", help="Session ID to record")
    p.add_argument("--host", default="localhost", help="RabbitMQ host")
    p.add_argument("--port", type=int, default=5672, help="RabbitMQ port")
    p.add_argument("-o", "--output", required=True, help="Output file path (.json or .jsonl)")
    p.add_argument(
        "--no-handshake",
        action="store_true",
        help="Do not capture the initial state snapshot on start",
    )
    args = p.parse_args()

    recorder = SyncRecorder(
        session_id=args.session,
        host=args.host,
        port=args.port,
        capture_initial_state=not args.no_handshake,
    )
    print(f"[*] Starting recording on session '{args.session}'...")
    print(f"[*] Writing events to: {args.output}")
    print(" [!] Press Ctrl+C to stop recording")

    try:
        recorder.start(output_file=args.output)
        print("Started")
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[*] Stopping recorder...")
    finally:
        recorder.stop()
        print(f"[*] Finished. Recorded {len(recorder.get_events())} events.")


if __name__ == "__main__":
    main()
