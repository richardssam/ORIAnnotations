import json
import os
import sys
import tempfile
import unittest

import opentimelineio as otio

# Ensure we can import sync_recorder
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.append(project_root)

from sync_recorder.convert_recording_to_timeline import convert_recording


class TestConvertRecording(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.recording_path = os.path.join(self.temp_dir.name, "session_record.jsonl")
        self.output_path = os.path.join(self.temp_dir.name, "output_timeline.otio")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_convert_recording(self):
        # Create a mock timeline with a clip
        clip_guid = "test_clip_123"
        template_clip = otio.schema.Clip(
            name="test_media.mov",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///path/to/test_media.mov",
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 24.0),
                    duration=otio.opentime.RationalTime(240, 24.0)
                )
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24.0),
                duration=otio.opentime.RationalTime(240, 24.0)
            )
        )
        template_clip.metadata["sync"] = {"guid": clip_guid}

        timeline = otio.schema.Timeline("Default Timeline")
        track = otio.schema.Track("Media")
        track.append(template_clip)
        timeline.tracks.append(track)

        # Build mock events
        snapshot_timeline_dict = json.loads(
            otio.adapters.write_to_string(timeline, "otio_json")
        )

        events = [
            # 1. State Snapshot (start at t=0)
            {
                "time_offset": 0.0,
                "payload": {
                    "command_schema": "LiveSession.1",
                    "command": {
                        "event": "STATE_SNAPSHOT",
                        "payload": {
                            "active_timeline_guid": "timeline_abc",
                            "timelines": {"timeline_abc": snapshot_timeline_dict},
                            "playback_state": {
                                "playing": False,
                                "current_time": {"value": 0.0, "rate": 24.0}
                            }
                        }
                    }
                }
            },
            # 2. Start playing at t=2.0 (duration 2.0s paused at frame 0)
            {
                "time_offset": 2.0,
                "payload": {
                    "command_schema": "PLAYBACK_SETTINGS_1.0",
                    "command": {
                        "event": "SET",
                        "payload": {
                            "playing": True,
                            "current_time": {"value": 0.0, "rate": 24.0}
                        }
                    }
                }
            },
            # 3. Pause at t=12.0 (duration 10.0s playing -> 240 frames)
            {
                "time_offset": 12.0,
                "payload": {
                    "command_schema": "PLAYBACK_SETTINGS_1.0",
                    "command": {
                        "event": "SET",
                        "payload": {
                            "playing": False,
                            "current_time": {"value": 240.0, "rate": 24.0}
                        }
                    }
                }
            },
            # 4. End recording at t=15.0 (duration 3.0s paused at frame 240)
            {
                "time_offset": 15.0,
                "payload": {
                    "command_schema": "PLAYBACK_SETTINGS_1.0",
                    "command": {
                        "event": "SET",
                        "payload": {
                            "playing": False,
                            "current_time": {"value": 240.0, "rate": 24.0}
                        }
                    }
                }
            }
        ]

        # Write to JSONL
        with open(self.recording_path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

        # Convert
        convert_recording(self.recording_path, self.output_path, target_fps=24.0)

        # Read back and assert
        self.assertTrue(os.path.exists(self.output_path))
        converted_timeline = otio.adapters.read_from_file(self.output_path)

        self.assertEqual(len(converted_timeline.tracks), 1)
        bg_track = converted_timeline.tracks[0]
        self.assertEqual(bg_track.name, "Background Media")

        # Visual segments should be:
        # Segment 1 (t=0.0 to t=2.0, duration 2.0s): Paused at frame 0 -> 48 frames duration, time warp
        # Segment 2 (t=2.0 to t=12.0, duration 10.0s): Playing from frame 0 -> 240 frames duration, no time warp
        # Segment 3 (t=12.0 to t=15.0, duration 3.0s): Paused at frame 240 -> 72 frames duration, time warp
        self.assertEqual(len(bg_track), 3)

        clip1 = bg_track[0]
        self.assertEqual(clip1.name, "test_media.mov")
        self.assertEqual(clip1.source_range.start_time.value, 0.0)
        self.assertEqual(clip1.source_range.duration.value, 48.0)
        self.assertEqual(len(clip1.effects), 1)
        self.assertTrue(isinstance(clip1.effects[0], otio.schema.LinearTimeWarp))
        self.assertEqual(clip1.effects[0].time_scalar, 0.0)

        clip2 = bg_track[1]
        self.assertEqual(clip2.name, "test_media.mov")
        self.assertEqual(clip2.source_range.start_time.value, 0.0)
        self.assertEqual(clip2.source_range.duration.value, 240.0)
        self.assertEqual(len(clip2.effects), 0)

        clip3 = bg_track[2]
        self.assertEqual(clip3.name, "test_media.mov")
        self.assertEqual(clip3.source_range.start_time.value, 240.0)
        self.assertEqual(clip3.source_range.duration.value, 72.0)
        self.assertEqual(len(clip3.effects), 1)
        self.assertTrue(isinstance(clip3.effects[0], otio.schema.LinearTimeWarp))
        self.assertEqual(clip3.effects[0].time_scalar, 0.0)


if __name__ == "__main__":
    unittest.main()
