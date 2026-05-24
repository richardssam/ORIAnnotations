"""Sync session event player.

Allows playing back recorded messages onto a sync session exchange.
"""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys
import time
from typing import Any

# Ensure we can import otio_sync_core
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "python"))

from otio_sync_core import RabbitMQNetwork


class SyncPlayer:
    """Plays back recorded sync session events to a network exchange with accurate delays.

    :param session_id: Logical session identifier to play back to.
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
    ) -> None:
        self.session_id = session_id
        self.host = host
        self.port = port
        self.network = network
        self.events: list[dict[str, Any]] = []
        self._recorded_snapshot: dict[str, Any] | None = None

        # Procedural playback tracking state
        self._playing = False
        self._play_start_time: float | None = None
        self._play_index = 0
        self._play_speed = 1.0
        self._play_loop = False
        self._play_replace_source_guid = True
        self._own_network = False

    def load_recording(self, filepath: str) -> None:
        """Load recorded events from a JSON Lines file.

        :param filepath: Path to the JSON Lines recording file.
        :raises ValueError: If the file is empty or malformed.
        """
        self.events.clear()
        self._recorded_snapshot = None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if "time_offset" not in event or "payload" not in event:
                            raise ValueError(
                                f"Event on line {line_num} is missing required fields"
                            )
                        self.events.append(event)
                        p = event.get("payload", {})
                        if p.get("command") == "SESSION" and p.get("event") == "STATE_SNAPSHOT":
                            self._recorded_snapshot = p
                    except json.JSONDecodeError as e:
                        raise ValueError(f"Failed to parse line {line_num}: {e}")
        except FileNotFoundError:
            raise ValueError(f"File not found: {filepath}")

        if not self.events:
            raise ValueError(f"No events found in {filepath}")

    def play(
        self,
        speed: float = 1.0,
        loop: bool = False,
        replace_source_guid: bool = True,
    ) -> None:
        """Synchronously play back the loaded events, blocking until finished.

        :param speed: Playback speed multiplier (e.g. 2.0 plays twice as fast).
        :param loop: If True, loops playback indefinitely until interrupted.
        :param replace_source_guid: If True, replaces payload source GUIDs with
            the player's own guid.
        """
        if not self.events:
            raise ValueError("No recording loaded. Call load_recording() first.")

        self._ensure_network()

        try:
            while True:
                start_time = time.time()
                for event in self.events:
                    offset = event["time_offset"]
                    target_time = start_time + (offset / speed)

                    # Real-time sleep loop
                    while time.time() < target_time:
                        self._process_network_requests()
                        sleep_time = min(0.01, target_time - time.time())
                        if sleep_time > 0:
                            time.sleep(sleep_time)

                    self._send_event(event, replace_source_guid)

                if not loop:
                    break
        finally:
            self._close_own_network()

    def start_playback(
        self,
        speed: float = 1.0,
        loop: bool = False,
        replace_source_guid: bool = True,
    ) -> None:
        """Initialize non-blocking procedural playback.

        Subsequent calls to :meth:`tick` will send messages at the correct times.

        :param speed: Playback speed multiplier.
        :param loop: If True, loops playback indefinitely.
        :param replace_source_guid: If True, replaces payload source GUIDs with
            the player's own guid.
        """
        if not self.events:
            raise ValueError("No recording loaded. Call load_recording() first.")

        self._ensure_network()

        self._play_start_time = time.time()
        self._play_index = 0
        self._play_speed = speed
        self._play_loop = loop
        self._play_replace_source_guid = replace_source_guid
        self._playing = True

    def tick(self) -> bool:
        """Advance procedural playback.

        Should be called repeatedly in the application's idle loop. Sends any
        events whose scheduled time has passed.

        :returns: True if playback is still active, False if finished.
        :rtype: bool
        """
        if not self._playing:
            return False

        if not self.events or self._play_start_time is None:
            self._playing = False
            self._close_own_network()
            return False

        self._process_network_requests()

        now = time.time()
        current_offset = (now - self._play_start_time) * self._play_speed

        while self._play_index < len(self.events):
            event = self.events[self._play_index]
            if event["time_offset"] <= current_offset:
                self._send_event(event, self._play_replace_source_guid)
                self._play_index += 1
            else:
                break

        if self._play_index >= len(self.events):
            if self._play_loop:
                self._play_start_time = time.time()
                self._play_index = 0
            else:
                self._playing = False
                self._close_own_network()
                return False

        return True

    def stop_playback(self) -> None:
        """Stop non-blocking procedural playback and clean up network resources."""
        self._playing = False
        self._close_own_network()

    def _ensure_network(self) -> None:
        """Create network client if not already provided."""
        if self.network is None:
            self.network = RabbitMQNetwork(
                host=self.host,
                port=self.port,
                session_id=self.session_id,
            )
            self._own_network = True

    def _close_own_network(self) -> None:
        """Stop network backend if we created it."""
        if self._own_network and self.network is not None:
            try:
                self.network.stop()
            except Exception as e:
                print(f"[player] Error stopping network: {e}", file=sys.stderr)
            self.network = None
            self._own_network = False

    def _process_network_requests(self) -> None:
        """Check for and handle incoming session requests (acting as Master)."""
        if self.network is None:
            return

        payloads = self.network.receive_payloads()
        for p in payloads:
            cmd = p.get("command")
            evt = p.get("event")
            data = p.get("payload", {})
            source = p.get("source_guid")

            if cmd == "SESSION":
                if evt == "WHO_IS_MASTER":
                    self.network.send_payload({
                        "command": "SESSION",
                        "event": "I_AM_MASTER",
                        "session_id": self.session_id,
                        "payload": {"master_guid": self.network.self_guid}
                    })
                elif evt == "STATE_REQUEST" and data.get("target_guid") == self.network.self_guid:
                    requester = data.get("requester_guid") or source
                    if requester and self._recorded_snapshot:
                        snapshot = copy.deepcopy(self._recorded_snapshot)
                        snapshot["payload"]["target_guid"] = requester
                        current_now = time.time()
                        snapshot = self._update_timestamps(snapshot, current_now)
                        snapshot["source_guid"] = self.network.self_guid
                        self.network.send_payload(snapshot)

    def _send_event(self, event: dict[str, Any], replace_source_guid: bool) -> None:
        """Prepare, update timestamps, and send the event's payload."""
        if self.network is None:
            return

        payload = copy.deepcopy(event["payload"])
        current_now = time.time()

        # Update all internal timestamp fields in payload
        payload = self._update_timestamps(payload, current_now)

        if replace_source_guid:
            payload["source_guid"] = self.network.self_guid

        self.network.send_payload(payload)

    def _update_timestamps(self, payload: Any, current_time: float) -> Any:
        """Recursively update any key containing 'timestamp' to the current_time.

        :param payload: Dict or list representing the message payload.
        :param current_time: The float timestamp to set.
        :returns: The updated payload copy.
        """
        if isinstance(payload, dict):
            new_dict = {}
            for k, v in payload.items():
                if "timestamp" in k.lower():
                    new_dict[k] = current_time
                else:
                    new_dict[k] = self._update_timestamps(v, current_time)
            return new_dict
        elif isinstance(payload, list):
            return [self._update_timestamps(x, current_time) for x in payload]
        return payload


def main() -> None:
    """Entry point for running the player from the command line."""
    p = argparse.ArgumentParser(description="OTIO Sync Session Player")
    p.add_argument("--session", default="otio-sync-demo", help="Session ID to replay to")
    p.add_argument("--host", default="localhost", help="RabbitMQ host")
    p.add_argument("--port", type=int, default=5672, help="RabbitMQ port")
    p.add_argument("-i", "--input", required=True, help="Input recording file path")
    p.add_argument(
        "--speed", type=float, default=1.0, help="Playback speed multiplier (default: 1.0)"
    )
    p.add_argument("--loop", action="store_true", help="Loop playback indefinitely")
    p.add_argument(
        "--keep-guids",
        action="store_true",
        help="Keep original source GUIDs instead of replacing them",
    )
    args = p.parse_args()

    player = SyncPlayer(session_id=args.session, host=args.host, port=args.port)
    try:
        player.load_recording(args.input)
    except Exception as e:
        print(f"[Error] Failed to load recording: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Loaded {len(player.events)} events from: {args.input}")
    print(f"[*] Playing to session '{args.session}' (speed={args.speed}, loop={args.loop})...")
    print(" [!] Press Ctrl+C to stop playback")

    try:
        player.play(
            speed=args.speed,
            loop=args.loop,
            replace_source_guid=not args.keep_guids,
        )
    except KeyboardInterrupt:
        print("\n[*] Playback stopped.")
    except Exception as e:
        print(f"\n[Error] Playback failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
