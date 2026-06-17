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


if __name__ == "__main__":
    unittest.main()
