"""Unit tests for SyncRecorder and SyncPlayer.

Tests the recording and playback functionality using a local UDP network loopback
to avoid needing a running RabbitMQ broker.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

# Ensure we can import otio_sync_core and sync_recorder
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.append(os.path.join(project_root, "python"))
sys.path.append(project_root)

from otio_sync_core.network import UDPNetwork
from sync_recorder import SyncRecorder, SyncPlayer


class TestSyncRecorderPlayer(unittest.TestCase):
    def setUp(self):
        # Create a temporary file path for recording logs
        self.temp_dir = tempfile.TemporaryDirectory()
        self.recording_path = os.path.join(self.temp_dir.name, "session_record.jsonl")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_and_play(self):
        # Port for UDP loopback test
        port = 9993

        # 1. Setup sender and recorder networks
        sender_net = UDPNetwork(port=port, self_guid="test_sender")
        recorder_net = UDPNetwork(port=port, self_guid="test_recorder")

        # Disable capture_initial_state for the simple playback test
        recorder = SyncRecorder(network=recorder_net, capture_initial_state=False)

        try:
            # Start recorder
            recorder.start(output_file=self.recording_path)

            # Wait for thread to spin up
            time.sleep(0.1)

            # Send some test payloads from sender
            payload1 = {
                "command": "PLAYBACK_SETTINGS",
                "event": "SET",
                "session_id": "test-session",
                "source_guid": "test_sender",
                "payload": {"playing": True, "sync_timestamp": 100.0},
            }
            payload2 = {
                "command": "DISPLAY_SETTINGS",
                "event": "SET",
                "session_id": "test-session",
                "source_guid": "test_sender",
                "payload": {"zoom": 2.5, "sync_timestamp": 105.0},
            }

            sender_net.send_payload(payload1)
            time.sleep(0.2)  # Give time to receive and write
            sender_net.send_payload(payload2)
            time.sleep(0.2)

        finally:
            # Stop recorder and close network sockets
            recorder.stop()
            sender_net.stop()
            recorder_net.stop()

        # Check recorded events in memory
        events = recorder.get_events()
        self.assertEqual(len(events), 2)

        self.assertEqual(events[0]["payload"]["command"], "PLAYBACK_SETTINGS")
        self.assertEqual(events[1]["payload"]["command"], "DISPLAY_SETTINGS")
        self.assertGreater(events[1]["time_offset"], events[0]["time_offset"])

        # Check recorded events in file
        self.assertTrue(os.path.exists(self.recording_path))
        with open(self.recording_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2)

        # Parse first line to verify file content
        file_event1 = json.loads(lines[0])
        self.assertEqual(file_event1["payload"]["command"], "PLAYBACK_SETTINGS")
        self.assertEqual(file_event1["payload"]["payload"]["playing"], True)

        # 2. Test Player playback
        player_net = UDPNetwork(port=port, self_guid="test_player")
        receiver_net = UDPNetwork(port=port, self_guid="test_receiver")

        player = SyncPlayer(network=player_net)
        player.load_recording(self.recording_path)

        self.assertEqual(len(player.events), 2)

        # We will test procedural non-blocking playback
        player.start_playback(speed=10.0, replace_source_guid=True)

        # Initially, tick should send the event immediately (offset ~ 0.0)
        active = player.tick()
        self.assertTrue(active)

        # Wait a bit for the second event's offset (at 10x speed, delay is small)
        time.sleep(0.1)
        active = player.tick()

        # Gather received payloads on receiver network
        time.sleep(0.1)
        received_payloads = receiver_net.receive_payloads()

        # Clean up sockets
        player.stop_playback()
        player_net.stop()
        receiver_net.stop()

        # We should have received 2 payloads
        self.assertEqual(len(received_payloads), 2)

        p1, p2 = received_payloads[0], received_payloads[1]
        self.assertEqual(p1["command"], "PLAYBACK_SETTINGS")
        self.assertEqual(p2["command"], "DISPLAY_SETTINGS")

        # Source GUIDs should be replaced with the player's self_guid
        self.assertEqual(p1["source_guid"], "test_player")
        self.assertEqual(p2["source_guid"], "test_player")

        # Timestamps should be updated to current epoch time (far greater than original 100.0/105.0)
        self.assertGreater(p1["payload"]["sync_timestamp"], 1000.0)
        self.assertGreater(p2["payload"]["sync_timestamp"], 1000.0)

    def test_drain_lingers_after_last_event(self):
        """With drain_seconds set, tick() keeps the player alive past the last
        event until the drain deadline, then stops. This is what gives a
        trailing checkpoint (and the apps) time after the final replayed event
        (e.g. a REMOVE_TIMELINE) before teardown."""
        port = 9991
        player_net = UDPNetwork(port=port, self_guid="drain_player")
        try:
            player = SyncPlayer(network=player_net)
            # Two events at offsets 0.0 and 0.05; the last is "the delete".
            player.events = [
                {"time_offset": 0.0, "payload": {"command": "A", "payload": {}}},
                {"time_offset": 0.05, "payload": {"command": "B", "payload": {}}},
            ]
            player.start_playback(speed=1.0, replace_source_guid=True, drain_seconds=0.5)

            # Drive past both events; player must NOT stop yet (still draining).
            time.sleep(0.1)
            self.assertTrue(player.tick(), "player stopped before drain elapsed")
            self.assertGreaterEqual(player._play_index, len(player.events))
            self.assertIsNotNone(player._drain_deadline)

            # Still draining a moment later.
            self.assertTrue(player.tick())

            # After the drain window elapses, tick() reports finished.
            time.sleep(0.55)
            self.assertFalse(player.tick(), "player did not stop after drain elapsed")
        finally:
            player.stop_playback()
            player_net.stop()

    def test_no_drain_stops_immediately(self):
        """Default drain_seconds=0.0 preserves the original behavior: the player
        stops as soon as its last event is sent."""
        port = 9990
        player_net = UDPNetwork(port=port, self_guid="nodrain_player")
        try:
            player = SyncPlayer(network=player_net)
            player.events = [
                {"time_offset": 0.0, "payload": {"command": "A", "payload": {}}},
            ]
            player.start_playback(speed=1.0, replace_source_guid=True)
            time.sleep(0.05)
            self.assertFalse(player.tick(), "player should stop immediately with no drain")
        finally:
            player.stop_playback()
            player_net.stop()

    def test_handshake_capture_and_master_simulation(self):
        port = 9992

        # Setup mock Master and Recorder
        master_net = UDPNetwork(port=port, self_guid="test_master")
        recorder_net = UDPNetwork(port=port, self_guid="test_recorder")

        recorder = SyncRecorder(network=recorder_net, capture_initial_state=True)
        recorder.start(output_file=self.recording_path)

        try:
            # 1. Wait for Recorder to send WHO_IS_MASTER
            time.sleep(0.25)
            master_recv = master_net.receive_payloads()
            
            # Find the WHO_IS_MASTER event
            discover_evt = next((p for p in master_recv if p.get("payload", {}).get("command", {}).get("event") == "WHO_IS_MASTER"), None)
            self.assertIsNotNone(discover_evt)
            
            # 2. Master sends I_AM_MASTER
            master_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "test_master",
                "schema": "SYNC_REVIEW_1.0",
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "I_AM_MASTER",
                        "payload": {"master_guid": "test_master"}
                    }
                }
            })

            # 3. Wait for Recorder to receive and send STATE_REQUEST
            time.sleep(0.25)
            # Call tick on recorder to process the I_AM_MASTER and trigger request
            recorder.tick()
            
            master_recv2 = master_net.receive_payloads()
            request_evt = next((p for p in master_recv2 if p.get("payload", {}).get("command", {}).get("event") == "STATE_REQUEST"), None)
            self.assertIsNotNone(request_evt)

            # 4. Master sends STATE_SNAPSHOT
            snapshot_timeline = {"guid": "timeline_123", "tracks": []}
            master_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "test_master",
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "STATE_SNAPSHOT",
                        "payload": {
                            "target_guid": "test_recorder",
                            "timelines": {"timeline_123": snapshot_timeline},
                            "active_timeline_guid": "timeline_123",
                            "snapshot_timestamp": 50.0
                        }
                    }
                }
            })
            
            # Send a non-session event so the recording has at least one playback event
            time.sleep(0.1)
            master_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "test_master",
                "payload": {
                    "command_schema": "PLAYBACK_SETTINGS_1.0",
                    "command": {
                        "event": "SET",
                        "payload": {"playing": True, "sync_timestamp": 100.0}
                    }
                }
            })
            

            # 5. Wait for Recorder to process the snapshot
            time.sleep(0.25)
            recorder.tick()

        finally:
            recorder.stop()
            master_net.stop()
            recorder_net.stop()

        # Check that the recorded snapshot was captured and saved
        events = recorder.get_events()
        snapshot_recorded = next((e for e in events if e["payload"].get("payload", {}).get("command", {}).get("event") == "STATE_SNAPSHOT"), None)
        self.assertIsNotNone(snapshot_recorded)

        # 6. Test Player master simulation
        player_net = UDPNetwork(port=port, self_guid="test_player")
        peer_net = UDPNetwork(port=port, self_guid="test_peer")

        player = SyncPlayer(network=player_net)
        player.load_recording(self.recording_path)
        self.assertIsNotNone(player._recorded_snapshot)

        # Start non-blocking playback (acting as master)
        player.start_playback()

        try:
            # Peer sends WHO_IS_MASTER
            peer_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "test_peer",
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "WHO_IS_MASTER",
                        "payload": {"requester_guid": "test_peer"}
                    }
                }
            })

            # Tick player to receive WHO_IS_MASTER and send I_AM_MASTER
            time.sleep(0.15)
            player.tick()

            time.sleep(0.15)
            peer_recv = peer_net.receive_payloads()
            iammaster_evt = next((p for p in peer_recv if p.get("payload", {}).get("command", {}).get("event") == "I_AM_MASTER"), None)
            self.assertIsNotNone(iammaster_evt)
            self.assertEqual(iammaster_evt["source_guid"], "test_player")

            # Peer sends STATE_REQUEST targeting player
            peer_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "test_peer",
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "STATE_REQUEST",
                        "payload": {
                            "target_guid": "test_player",
                            "requester_guid": "test_peer"
                        }
                    }
                }
            })

            # Tick player to send STATE_SNAPSHOT
            time.sleep(0.15)
            player.tick()

            time.sleep(0.15)
            peer_recv2 = peer_net.receive_payloads()
            snapshot_evt = next((p for p in peer_recv2 if p.get("payload", {}).get("command", {}).get("event") == "STATE_SNAPSHOT"), None)
            self.assertIsNotNone(snapshot_evt)
            
            # Target GUID in snapshot must match requester ("test_peer")
            self.assertEqual(snapshot_evt["payload"]["command"]["payload"]["target_guid"], "test_peer")
            # Snapshot timestamp should be updated
            self.assertGreater(snapshot_evt["payload"]["command"]["payload"]["snapshot_timestamp"], 1000.0)

        finally:
            player.stop_playback()
            player_net.stop()
            peer_net.stop()

    def test_pause_freezes_offset_and_resume_restores_it(self):
        """The reported playback offset must be the same immediately before a
        pause and immediately after the corresponding resume (proposal.md
        §Verify offset frozen)."""
        port = 9989
        player_net = UDPNetwork(port=port, self_guid="pause_offset_player")
        try:
            player = SyncPlayer(network=player_net)
            player.events = [
                {"time_offset": 0.0, "payload": {"command": "A", "payload": {}}},
                {"time_offset": 60.0, "payload": {"command": "B", "payload": {}}},
            ]
            player.start_playback(speed=1.0, replace_source_guid=True)
            player.tick()  # dispatch event A at offset 0.0

            offset_before = time.time() - player._play_start_time
            player.pause()
            time.sleep(0.3)
            for _ in range(3):
                self.assertTrue(player.tick())
            player.resume()
            offset_after = time.time() - player._play_start_time

            self.assertAlmostEqual(offset_before, offset_after, delta=0.05)
        finally:
            player.stop_playback()
            player_net.stop()

    def test_paused_playback_dispatches_no_events(self):
        """No event may be dispatched while paused, however long the pause
        lasts (spec: sync-recorder-state-capture, "Paused playback dispatches
        no events")."""
        port = 9988
        player_net = UDPNetwork(port=port, self_guid="pause_dispatch_player")
        receiver_net = UDPNetwork(port=port, self_guid="pause_dispatch_receiver")
        try:
            player = SyncPlayer(network=player_net)
            player.events = [
                {"time_offset": 0.0, "payload": {"command": "A", "payload": {}}},
                {"time_offset": 0.05, "payload": {"command": "B", "payload": {}}},
            ]
            player.start_playback(speed=1.0, replace_source_guid=True)
            player.tick()  # dispatch A
            player.pause()

            # Long enough that, unpaused, event B would have been dispatched
            # many times over.
            for _ in range(10):
                player.tick()
                time.sleep(0.03)

            received = receiver_net.receive_payloads()
            self.assertEqual(len(received), 1, "no event should dispatch while paused")
            self.assertEqual(received[0]["command"], "A")
        finally:
            player.stop_playback()
            player_net.stop()
            receiver_net.stop()

    def test_resume_preserves_event_spacing(self):
        """After resume, the next event dispatched is the one that was next
        before the pause, and inter-event spacing matches the recording,
        unaffected by how long the pause lasted."""
        port = 9987
        player_net = UDPNetwork(port=port, self_guid="pause_spacing_player")
        receiver_net = UDPNetwork(port=port, self_guid="pause_spacing_receiver")
        try:
            player = SyncPlayer(network=player_net)
            player.events = [
                {"time_offset": 0.0, "payload": {"command": "A", "payload": {}}},
                {"time_offset": 0.1, "payload": {"command": "B", "payload": {}}},
                {"time_offset": 0.2, "payload": {"command": "C", "payload": {}}},
            ]
            player.start_playback(speed=1.0, replace_source_guid=True)
            player.tick()  # dispatch A at offset 0.0
            player.pause()
            time.sleep(0.5)  # pause well past B and C's recorded offsets
            player.resume()

            # B should not be dispatched immediately on resume — its recorded
            # spacing (0.1s after A) must still be honored.
            player.tick()
            received = receiver_net.receive_payloads()
            self.assertEqual(
                [p["command"] for p in received], ["A"],
                "B dispatched before its recorded spacing elapsed post-resume",
            )

            time.sleep(0.15)
            player.tick()
            time.sleep(0.15)
            player.tick()
            received = receiver_net.receive_payloads()
            self.assertEqual([p["command"] for p in received], ["B", "C"])
        finally:
            player.stop_playback()
            player_net.stop()
            receiver_net.stop()

    def test_pause_idempotent(self):
        """Pausing already-paused playback, or resuming playback that is not
        paused, is a no-op."""
        port = 9986
        player_net = UDPNetwork(port=port, self_guid="pause_idempotent_player")
        try:
            player = SyncPlayer(network=player_net)
            player.events = [
                {"time_offset": 0.0, "payload": {"command": "A", "payload": {}}},
            ]
            player.start_playback(speed=1.0, replace_source_guid=True)

            player.resume()  # not paused: no-op
            self.assertFalse(player._paused)

            player.pause()
            paused_at_first = player._pause_started_at
            time.sleep(0.05)
            player.pause()  # already paused: no-op, must not reset the pause clock
            self.assertEqual(player._pause_started_at, paused_at_first)

            player.resume()
            self.assertFalse(player._paused)
            play_start_before = player._play_start_time

            player.resume()  # already resumed: no-op
            self.assertEqual(player._play_start_time, play_start_before)
        finally:
            player.stop_playback()
            player_net.stop()

    def test_paused_player_still_answers_peer_state_request(self):
        """A peer requesting state while playback is paused must still be
        answered, exactly as during normal playback."""
        port = 9985
        player_net = UDPNetwork(port=port, self_guid="pause_peer_player")
        peer_net = UDPNetwork(port=port, self_guid="pause_peer_peer")
        try:
            player = SyncPlayer(network=player_net)
            player.events = [
                {"time_offset": 0.0, "payload": {"command": "A", "payload": {}}},
            ]
            player._recorded_snapshot = {
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "STATE_SNAPSHOT",
                        "payload": {
                            "target_guid": None,
                            "timelines": {},
                            "snapshot_timestamp": 50.0,
                        },
                    },
                }
            }
            player.start_playback(speed=1.0, replace_source_guid=True)
            player.pause()

            peer_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "pause_peer_peer",
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "STATE_REQUEST",
                        "payload": {
                            "target_guid": "pause_peer_player",
                            "requester_guid": "pause_peer_peer",
                        },
                    },
                },
            })

            time.sleep(0.15)
            self.assertTrue(player.tick(), "paused player must keep ticking")
            time.sleep(0.15)

            received = peer_net.receive_payloads()
            snapshot_evt = next(
                (p for p in received
                 if p.get("payload", {}).get("command", {}).get("event") == "STATE_SNAPSHOT"),
                None,
            )
            self.assertIsNotNone(snapshot_evt, "peer must be answered even while paused")
        finally:
            player.stop_playback()
            player_net.stop()
            peer_net.stop()

    def test_delayed_master_startup_handshake_capture(self):
        port = 9991

        # Setup mock Master and Recorder
        master_net = UDPNetwork(port=port, self_guid="test_master")
        recorder_net = UDPNetwork(port=port, self_guid="test_recorder")

        recorder = SyncRecorder(network=recorder_net, capture_initial_state=True)
        recorder.start(output_file=self.recording_path)

        try:
            # 1. Wait for Recorder to send WHO_IS_MASTER
            discover_evt = None
            for _ in range(25):
                time.sleep(0.1)
                master_recv = master_net.receive_payloads()
                discover_evt = next((p for p in master_recv if p.get("payload", {}).get("command", {}).get("event") == "WHO_IS_MASTER"), None)
                if discover_evt:
                    break
            self.assertIsNotNone(discover_evt)

            # Clear received payloads on master
            master_net.receive_payloads()

            # 2. Master broadcasts I_AM_MASTER
            master_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "test_master",
                "schema": "SYNC_REVIEW_1.0",
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "I_AM_MASTER",
                        "payload": {"master_guid": "test_master"}
                    }
                }
            })

            # 3. Wait for Recorder to receive I_AM_MASTER and send STATE_REQUEST
            request_evt = None
            for _ in range(25):
                time.sleep(0.1)
                master_recv2 = master_net.receive_payloads()
                request_evt = next((p for p in master_recv2 if p.get("payload", {}).get("command", {}).get("event") == "STATE_REQUEST"), None)
                if request_evt:
                    break
            self.assertIsNotNone(request_evt)

            # 4. Master sends STATE_SNAPSHOT
            snapshot_timeline = {"guid": "timeline_123", "tracks": []}
            master_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "test_master",
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "STATE_SNAPSHOT",
                        "payload": {
                            "target_guid": "test_recorder",
                            "timelines": {"timeline_123": snapshot_timeline},
                            "active_timeline_guid": "timeline_123",
                            "snapshot_timestamp": 50.0
                        }
                    }
                }
            })
            
            # Send a non-session event so the recording has at least one playback event
            time.sleep(0.1)
            master_net.send_payload({
                "session": "otio-sync-demo",
                "source_guid": "test_master",
                "payload": {
                    "command_schema": "PLAYBACK_SETTINGS_1.0",
                    "command": {
                        "event": "SET",
                        "payload": {"playing": True, "sync_timestamp": 100.0}
                    }
                }
            })
            

            # 5. Wait for Recorder to process the snapshot
            for _ in range(25):
                time.sleep(0.1)
                if recorder._snapshot_captured:
                    break

        finally:
            recorder.stop()
            master_net.stop()
            recorder_net.stop()

        # Check that the recorded snapshot was captured and saved
        events = recorder.get_events()
        snapshot_recorded = next((e for e in events if e["payload"].get("payload", {}).get("command", {}).get("event") == "STATE_SNAPSHOT"), None)
        self.assertIsNotNone(snapshot_recorded)
        self.assertTrue(recorder._snapshot_captured)


class TestExplicitClockArming(unittest.TestCase):
    """The peer-join gate tracks peer-snapshot delivery; it no longer starts
    the recording's logical clock by itself. The clock arms once the gate's
    conditions AND arm_clock() have both happened, whichever comes last
    (spec: sync-recorder-state-capture, "Explicit Clock Arming Decoupled From
    Peer-Join Gate").
    """

    def _make_player(self, net, guid_prefix="arm"):
        player = SyncPlayer(network=net)
        player.events = [
            {"time_offset": 0.0, "payload": {"command": "A", "payload": {}}},
            {"time_offset": 0.05, "payload": {"command": "B", "payload": {}}},
        ]
        return player

    def _satisfy_gate(self, player):
        """Drive the gate's own conditions to satisfied, as a joining peer
        receiving its snapshot would. Backdates the snapshot instant so the
        post-snapshot cooling-off delay is already elapsed, keeping the test
        about arming rather than about waiting out a timer."""
        player._peers_snapshotted.add("peer-1")
        player._peer_snapshot_sent_at = time.time() - 10.0

    def test_gate_cleared_but_never_armed_dispatches_nothing(self):
        """Gate conditions satisfied but arm_clock() never called: no event
        dispatched however long we tick, and the network is still serviced
        (spec: "A caller that never arms the clock sees no dispatch")."""
        port = 9984
        player_net = UDPNetwork(port=port, self_guid="arm_none_player")
        receiver_net = UDPNetwork(port=port, self_guid="arm_none_receiver")
        try:
            player = self._make_player(player_net)
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=0.0)
            self._satisfy_gate(player)

            for _ in range(15):
                self.assertTrue(player.tick(), "unarmed player must keep ticking")
                time.sleep(0.02)

            self.assertEqual(
                len(receiver_net.receive_payloads()), 0,
                "no event may dispatch before the clock is armed",
            )
            self.assertIsNone(player._play_start_time, "clock must remain unarmed")
            self.assertTrue(player._wait_for_peer, "gate must stay engaged until armed")
        finally:
            player.stop_playback()
            player_net.stop()
            receiver_net.stop()

    def test_arming_before_gate_clears_does_not_start_clock_early(self):
        """arm_clock() ahead of the gate must not bypass it — the clock starts
        when the gate's conditions are later satisfied, not at the moment of
        the arm request (spec: "Arming before the gate clears does not start
        the clock early")."""
        port = 9983
        player_net = UDPNetwork(port=port, self_guid="arm_early_player")
        receiver_net = UDPNetwork(port=port, self_guid="arm_early_receiver")
        try:
            player = self._make_player(player_net)
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=0.0)
            player.arm_clock()

            # No peer has been snapshotted yet: arming alone must not dispatch.
            for _ in range(5):
                self.assertTrue(player.tick())
                time.sleep(0.02)
            self.assertIsNone(
                player._play_start_time,
                "arming must not bypass the peer-join gate",
            )
            self.assertEqual(len(receiver_net.receive_payloads()), 0)

            # Now satisfy the gate; the clock arms on the next tick.
            self._satisfy_gate(player)
            player.tick()
            self.assertIsNotNone(player._play_start_time)
            self.assertAlmostEqual(player._play_start_time, time.time(), delta=0.2)
        finally:
            player.stop_playback()
            player_net.stop()
            receiver_net.stop()

    def test_arming_after_gate_cleared_arms_on_next_tick(self):
        """arm_clock() when the gate is already satisfied arms the clock on the
        very next tick (spec: "Explicit arming starts the clock")."""
        port = 9982
        player_net = UDPNetwork(port=port, self_guid="arm_late_player")
        try:
            player = self._make_player(player_net)
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=0.0)
            self._satisfy_gate(player)
            player.tick()
            self.assertIsNone(player._play_start_time)

            time.sleep(0.2)
            player.arm_clock()
            player.tick()

            self.assertIsNotNone(player._play_start_time, "clock must arm on the next tick")
            self.assertFalse(player._wait_for_peer, "gate must release once armed")
            # t=0 is the arm, not the earlier gate-clear: a recording event at
            # offset 0.05 must not already be considered overdue by 0.2s.
            self.assertLess(time.time() - player._play_start_time, 0.1)
        finally:
            player.stop_playback()
            player_net.stop()

    def test_wait_for_peer_false_is_unchanged(self):
        """The default mode still arms synchronously in start_playback(),
        whether or not arm_clock() is ever called."""
        port = 9981
        player_net = UDPNetwork(port=port, self_guid="arm_default_player")
        try:
            player = self._make_player(player_net)
            player.start_playback(speed=1.0)
            self.assertIsNotNone(
                player._play_start_time,
                "wait_for_peer=False must arm synchronously, as before",
            )
            armed_at = player._play_start_time

            # arm_clock() is a harmless no-op in this mode.
            player.arm_clock()
            player.tick()
            self.assertEqual(player._play_start_time, armed_at)
        finally:
            player.stop_playback()
            player_net.stop()

    def test_pause_while_unarmed_then_arm_and_resume(self):
        """Pausing before the clock has ever been armed must not corrupt
        _play_start_time or cause a premature or duplicate arm."""
        port = 9980
        player_net = UDPNetwork(port=port, self_guid="arm_pause_player")
        try:
            player = self._make_player(player_net)
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=0.0)
            self._satisfy_gate(player)

            player.pause()
            for _ in range(5):
                self.assertTrue(player.tick(), "paused unarmed player keeps ticking")
                time.sleep(0.02)
            self.assertIsNone(player._play_start_time, "pause must not arm the clock")

            player.arm_clock()
            player.tick()  # still paused: the paused check precedes the gate
            self.assertIsNone(
                player._play_start_time,
                "arming while paused must not arm until resumed",
            )

            player.resume()  # must not corrupt a None anchor
            self.assertIsNone(player._play_start_time)
            player.tick()
            self.assertIsNotNone(player._play_start_time, "clock arms after resume")
            self.assertAlmostEqual(player._play_start_time, time.time(), delta=0.2)
        finally:
            player.stop_playback()
            player_net.stop()

    def test_gate_to_arm_interval_is_logged(self):
        """Both ends of the gate-to-arm interval are logged, and the reported
        interval matches the delay actually injected — this is the measurement
        the change is justified by, so it must be trustworthy."""
        port = 9979
        player_net = UDPNetwork(port=port, self_guid="arm_log_player")
        try:
            player = self._make_player(player_net)
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=0.0)
            self._satisfy_gate(player)

            with self.assertLogs(level="INFO") as captured:
                player.tick()  # gate clears, unarmed -> "holding" line
                time.sleep(0.5)
                player.arm_clock()
                player.tick()  # arms -> interval line

            messages = "\n".join(captured.output)
            self.assertIn("gate cleared", messages.lower())
            self.assertIn("clock armed", messages.lower())
            armed_line = next(l for l in captured.output if "Clock armed" in l)
            reported = float(armed_line.split("Clock armed ")[1].split("s after")[0])
            self.assertAlmostEqual(
                reported, 0.5, delta=0.2,
                msg=f"reported interval {reported}s should match the ~0.5s injected",
            )
        finally:
            player.stop_playback()
            player_net.stop()

    def test_arm_from_previous_run_is_not_inherited(self):
        """start_playback() clears the arm flag, so reusing a player instance
        for a second run cannot inherit a stale arm from the first."""
        port = 9978
        player_net = UDPNetwork(port=port, self_guid="arm_reuse_player")
        try:
            player = self._make_player(player_net)
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=0.0)
            self._satisfy_gate(player)
            player.arm_clock()
            player.tick()
            self.assertIsNotNone(player._play_start_time, "first run arms normally")

            # Second run on the same instance: the previous arm must not carry.
            player.start_playback(speed=1.0, wait_for_peer=True, post_snapshot_delay=0.0)
            self.assertFalse(player._clock_arm_requested)
            self._satisfy_gate(player)
            for _ in range(5):
                player.tick()
                time.sleep(0.02)
            self.assertIsNone(
                player._play_start_time,
                "second run must wait for its own arm_clock()",
            )
        finally:
            player.stop_playback()
            player_net.stop()


if __name__ == "__main__":
    unittest.main()
