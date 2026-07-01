"""Sync session event player.

Allows playing back recorded messages onto a sync session exchange.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
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
        host: str = "127.0.0.1",
        port: int = 5672,
        network: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self.host = host
        self.port = port
        self.network = network
        self.events: list[dict[str, Any]] = []
        # The first recorded snapshot answers joiners' STATE_REQUEST during
        # replay; the full time-ordered list is the validation expectation.
        self._recorded_snapshot: dict[str, Any] | None = None
        self._recorded_snapshots: list[tuple[float, dict[str, Any]]] = []

        # Procedural playback tracking state
        self._playing = False
        self._play_start_time: float | None = None
        self._play_index = 0
        self._play_speed = 1.0
        self._play_loop = False
        self._play_replace_source_guid = True
        self._own_network = False

        # Post-playback drain: after the last event is sent, linger (servicing
        # the network but sending nothing) so trailing checkpoints can validate
        # and peers can apply the final events — e.g. a REMOVE_TIMELINE that is
        # the last recorded event — before the harness tears the apps down.
        self._drain_seconds: float = 0.0
        self._drain_deadline: float | None = None

        # Peer-join gate: hold playback until a STATE_SNAPSHOT has been sent
        # and a configurable delay has elapsed.
        self._wait_for_peer = False
        self._min_peer_count: int = 1
        self._post_snapshot_delay: float = 3.0
        self._peer_snapshot_sent_at: float | None = None
        self._peer_active_received: bool = False
        self._peers_snapshotted: set[str] = set()

    def load_recording(self, filepath: str) -> None:
        """Load recorded events from a JSON Lines file.

        :param filepath: Path to the JSON Lines recording file.
        :raises ValueError: If the file is empty or malformed.
        """
        self.events.clear()
        self._recorded_snapshot = None
        self._recorded_snapshots = []
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
                        envelope = event.get("payload", {})
                        p = envelope.get("payload", {})
                        if p.get("command_schema") == "LiveSession.1":
                            if p.get("command", {}).get("event") == "STATE_SNAPSHOT":
                                # Retain every snapshot for validation; the first
                                # also seeds joiners during replay.  Snapshots are
                                # never appended to self.events, so they are never
                                # replayed as playback traffic.
                                self._recorded_snapshots.append(
                                    (event.get("time_offset", 0.0), envelope)
                                )
                                if self._recorded_snapshot is None:
                                    self._recorded_snapshot = envelope
                            continue

                        self.events.append(event)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"Failed to parse line {line_num}: {e}")
        except FileNotFoundError:
            raise ValueError(f"File not found: {filepath}")

        # Keep snapshots in chronological order for time-keyed validation lookup.
        self._recorded_snapshots.sort(key=lambda item: item[0])

        if not self.events:
            raise ValueError(f"No events found in {filepath}")

    def play(
        self,
        speed: float = 1.0,
        loop: bool = False,
        replace_source_guid: bool = True,
        wait_for_peer: bool = False,
        post_snapshot_delay: float = 3.0,
    ) -> None:
        """Synchronously play back the loaded events, blocking until finished.

        :param speed: Playback speed multiplier (e.g. 2.0 plays twice as fast).
        :param loop: If True, loops playback indefinitely until interrupted.
        :param replace_source_guid: If True, replaces payload source GUIDs with
            the player's own guid.
        :param wait_for_peer: If True, hold playback until a peer has requested
            and received a ``STATE_SNAPSHOT``, then wait *post_snapshot_delay*
            seconds before sending the first recorded event.
        :param post_snapshot_delay: Seconds to wait after the snapshot is
            delivered before playback begins.  Gives the joining peer time to
            apply the snapshot before events start arriving.  Default 1.0 s.
        """
        if not self.events:
            raise ValueError("No recording loaded. Call load_recording() first.")

        self._ensure_network()
        self._peer_snapshot_sent_at = None

        if wait_for_peer:
            print("[*] Waiting for a peer to join and request state…")
            while self._peer_snapshot_sent_at is None:
                self._process_network_requests()
                time.sleep(0.01)
            print(
                f"[*] State snapshot sent. Waiting up to {post_snapshot_delay:.1f}s "
                "for peer to become ready…"
            )
            self._peer_active_received = False
            start_wait = time.time()
            while (time.time() - start_wait) < post_snapshot_delay:
                self._process_network_requests()
                if self._peer_active_received:
                    print("[*] Peer activity detected! Starting playback early.")
                    break
                time.sleep(0.01)

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
        wait_for_peer: bool = False,
        min_peer_count: int = 1,
        post_snapshot_delay: float = 3.0,
        drain_seconds: float = 0.0,
    ) -> None:
        """Initialize non-blocking procedural playback.

        Subsequent calls to :meth:`tick` will send messages at the correct times.

        When *wait_for_peer* is ``True``, :meth:`tick` enters a waiting state
        and does not send any events until *min_peer_count* peers have each
        requested and received a ``STATE_SNAPSHOT`` **and** *post_snapshot_delay*
        seconds have elapsed after the last snapshot was sent.  During this phase
        :meth:`tick` still returns ``True`` and continues to service incoming
        network requests.

        :param speed: Playback speed multiplier.
        :param loop: If True, loops playback indefinitely.
        :param replace_source_guid: If True, replaces payload source GUIDs with
            the player's own guid.
        :param wait_for_peer: If True, hold event dispatch until peers have
            loaded the current state.
        :param min_peer_count: Number of distinct peers that must each receive
            a STATE_SNAPSHOT before the gate clears.  Default 1.
        :param post_snapshot_delay: Seconds to wait after the last snapshot is
            delivered before playback begins.  Default 1.0 s.
        """
        if not self.events:
            raise ValueError("No recording loaded. Call load_recording() first.")

        self._ensure_network()

        self._peer_snapshot_sent_at = None
        self._peers_snapshotted = set()
        self._min_peer_count = min_peer_count
        self._wait_for_peer = wait_for_peer
        self._post_snapshot_delay = post_snapshot_delay
        # When waiting for a peer, defer setting _play_start_time until the
        # gate clears; tick() will set it then.
        self._play_start_time = None if wait_for_peer else time.time()
        self._play_index = 0
        self._play_speed = speed
        self._play_loop = loop
        self._play_replace_source_guid = replace_source_guid
        self._drain_seconds = drain_seconds
        self._drain_deadline = None
        self._playing = True

    def tick(self) -> bool:
        """Advance procedural playback.

        Should be called repeatedly in the application's idle loop. Sends any
        events whose scheduled time has passed.

        While the peer-join gate is active (``wait_for_peer=True`` was passed
        to :meth:`start_playback` and no peer has loaded state yet) this
        method services incoming network requests but does not send recorded
        events; it still returns ``True`` so callers keep ticking.

        :returns: True if playback is still active, False if finished.
        :rtype: bool
        """
        if not self._playing:
            return False

        if not self.events:
            self._playing = False
            self._close_own_network()
            return False

        self._process_network_requests()

        # Peer-join gate: wait until min_peer_count peers have each received a
        # snapshot and the post-snapshot delay has elapsed.
        if self._wait_for_peer:
            if len(self._peers_snapshotted) < self._min_peer_count:
                # Not enough peers have joined yet; keep servicing the network.
                return True
            elapsed = time.time() - self._peer_snapshot_sent_at
            if not self._peer_active_received and elapsed < self._post_snapshot_delay:
                # All peers snapshotted but cooling-off delay not yet elapsed.
                return True
            # Gate cleared — arm the event clock and drop into normal playback.
            self._wait_for_peer = False
            self._play_start_time = time.time()

        if self._play_start_time is None:
            self._playing = False
            self._close_own_network()
            return False

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
                return True
            # All events sent. Linger for the drain window so trailing
            # checkpoints can validate and peers can apply the final events
            # (e.g. a REMOVE_TIMELINE that is the last recorded event) before
            # the harness tears them down. _process_network_requests (above)
            # keeps servicing joiners during the drain; the wall clock keeps
            # advancing current_offset so the runner reaches post-event
            # checkpoints.
            if self._drain_seconds > 0.0:
                if self._drain_deadline is None:
                    self._drain_deadline = time.time() + self._drain_seconds
                if time.time() < self._drain_deadline:
                    return True
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
            inner = p.get("payload", {})
            schema = inner.get("command_schema")
            cmd = inner.get("command", {})
            evt = cmd.get("event")
            data = cmd.get("payload", {})
            source = p.get("source_guid")

            if schema == "LiveSession.1":
                if evt == "WHO_IS_MASTER":
                    self.network.send_payload({
                        "session": self.session_id,
                        "source_guid": self.network.self_guid,
                        "schema": "SYNC_REVIEW_1.0",
                        "payload": {
                            "command_schema": "LiveSession.1",
                            "command": {
                                "event": "I_AM_MASTER",
                                "payload": {"master_guid": self.network.self_guid}
                            }
                        }
                    })
                elif evt == "STATE_REQUEST" and data.get("target_guid") == self.network.self_guid:
                    requester = data.get("requester_guid") or source
                    if requester and self._recorded_snapshot:
                        snapshot = copy.deepcopy(self._recorded_snapshot)
                        snapshot["payload"]["command"]["payload"]["target_guid"] = requester
                        current_now = time.time()
                        snapshot = self._update_timestamps(snapshot, current_now)
                        snapshot = self._resolve_target_urls(snapshot)
                        snapshot["source_guid"] = self.network.self_guid
                        snapshot["session"] = self.session_id
                        self.network.send_payload(snapshot)
                        # Track each distinct peer that has received a snapshot.
                        # The gate in tick() waits until min_peer_count are done.
                        self._peers_snapshotted.add(requester)
                        self._peer_snapshot_sent_at = time.time()
                        self._peer_active_received = False

            if source and source != self.network.self_guid:
                if self._peer_snapshot_sent_at is not None:
                    self._peer_active_received = True

    def _send_event(self, event: dict[str, Any], replace_source_guid: bool) -> None:
        """Prepare, update timestamps, and send the event's payload."""
        if self.network is None:
            return

        payload = copy.deepcopy(event["payload"])
        current_now = time.time()

        # Update all internal timestamp fields in payload
        payload = self._update_timestamps(payload, current_now)
        payload = self._resolve_target_urls(payload)

        if replace_source_guid:
            payload["source_guid"] = self.network.self_guid
            
        payload["session"] = self.session_id

        self.network.send_payload(payload)

    def _resolve_target_urls(self, payload: Any) -> Any:
        """Recursively find 'target_url' and resolve to absolute paths if they are relative and exist.

        :param payload: Dict or list representing the message payload.
        :returns: The updated payload copy.
        """
        if isinstance(payload, dict):
            new_dict = {}
            for k, v in payload.items():
                if k == "target_url" and isinstance(v, str):
                    if v.startswith("file:///"):
                        # Fully-qualified absolute URI — pass through unchanged.
                        new_dict[k] = v
                    elif v.startswith("file:/"):
                        # file:/ prefix signals a project-relative path.
                        rel = v[len("file:/"):]
                        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        abs_path = os.path.normpath(os.path.join(project_root, rel))
                        if os.path.exists(abs_path):
                            new_dict[k] = "file://" + abs_path
                        else:
                            print(f"[Warning] target_url '{v}' not found relative to project root {project_root}")
                            new_dict[k] = v
                    elif os.path.isabs(v):
                        new_dict[k] = v
                    else:
                        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                        abs_path = os.path.normpath(os.path.join(project_root, v))
                        if os.path.exists(abs_path):
                            new_dict[k] = "file://" + abs_path
                        else:
                            print(f"[Warning] target_url '{v}' not found relative to project root {project_root}")
                            new_dict[k] = v
                else:
                    new_dict[k] = self._resolve_target_urls(v)
            return new_dict
        elif isinstance(payload, list):
            return [self._resolve_target_urls(x) for x in payload]
        return payload

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
    p.add_argument("--host", default="127.0.0.1", help="RabbitMQ host")
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
    p.add_argument(
        "--wait-for-peer",
        action="store_true",
        help=(
            "Hold playback until a peer has joined and received the state "
            "snapshot, then wait --post-snapshot-delay seconds before "
            "sending the first event."
        ),
    )
    p.add_argument(
        "--post-snapshot-delay",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help=(
            "Seconds to wait after sending the state snapshot before "
            "playback begins (only used with --wait-for-peer, default: 3.0)."
        ),
    )
    args = p.parse_args()

    player = SyncPlayer(session_id=args.session, host=args.host, port=args.port)
    try:
        player.load_recording(args.input)
    except Exception as e:
        print(f"[Error] Failed to load recording: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Loaded {len(player.events)} events from: {args.input}")
    print(
        f"[*] Playing to session '{args.session}' "
        f"(speed={args.speed}, loop={args.loop}, "
        f"wait_for_peer={args.wait_for_peer})..."
    )
    print(" [!] Press Ctrl+C to stop playback")

    try:
        player.play(
            speed=args.speed,
            loop=args.loop,
            replace_source_guid=not args.keep_guids,
            wait_for_peer=args.wait_for_peer,
            post_snapshot_delay=args.post_snapshot_delay,
        )
    except KeyboardInterrupt:
        print("\n[*] Playback stopped.")
    except Exception as e:
        print(f"\n[Error] Playback failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
