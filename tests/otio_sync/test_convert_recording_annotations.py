# SPDX-License-Identifier: Apache-2.0
"""Integration tests for converting session recordings to timelines with annotations."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

import opentimelineio as otio
from PIL import Image

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if project_root not in sys.path:
    sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "python"))

# Setup OTIO plugin manifest path
manifest_file = os.path.join(project_root, "otio_event_plugin/plugin_manifest.json")
if os.path.exists(manifest_file):
    existing = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
    if manifest_file not in existing:
        os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = (
            existing + os.pathsep + manifest_file if existing else manifest_file
        )

# Force manifest reload to register SyncEvent schema plugin only if not already registered
try:
    otio.schema.schemadef.module_from_name('SyncEvent')
except Exception:
    try:
        import opentimelineio.plugins.manifest as otio_manifest
        otio_manifest._MANIFEST = None
        otio.schema.schemadef.module_from_name('SyncEvent')
    except Exception as e:
        print(f"Warning: failed to force load SyncEvent: {e}")

from sync_recorder.convert_recording_to_timeline import convert_recording
from otio_sync_core.annotation_builder import make_stroke, make_text


class TestConvertRecordingWithAnnotations(unittest.TestCase):
    """Integration test suite for the recording-to-timeline converter with annotations."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.recording_path = os.path.join(self.temp_dir.name, "session_record.jsonl")
        self.output_path = os.path.join(self.temp_dir.name, "output_timeline.otio")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_convert_recording_with_annotations(self) -> None:
        """Verify full pipeline conversion of a recording with drawing events."""
        # 1. Define media clip details
        clip_guid = "test_clip_123"
        template_clip = otio.schema.Clip(
            name="test_media.mov",
            media_reference=otio.schema.ExternalReference(
                target_url="file:///path/to/test_media.mov",
                available_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 24.0),
                    duration=otio.opentime.RationalTime(100, 24.0)
                )
            ),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24.0),
                duration=otio.opentime.RationalTime(100, 24.0)
            )
        )
        template_clip.metadata["sync"] = {"guid": clip_guid}

        timeline = otio.schema.Timeline("Default Timeline")
        track = otio.schema.Track("Media", kind="Video")
        track.append(template_clip)
        timeline.tracks.append(track)

        # Create annotation track
        ann_track = otio.schema.Track("Annotations", kind="Video")
        timeline.tracks.append(ann_track)

        # Traverse and map so they get GUIDs
        from otio_sync_core.patcher import OTIOPatcher
        patcher = OTIOPatcher()
        patcher.traverse_and_map(timeline)

        timeline_dict = json.loads(
            otio.adapters.write_to_string(timeline, "otio_json")
        )

        # 2. Build mock events
        events = []

        # Event 1: STATE_SNAPSHOT (starts at frame 9, no drawings)
        events.append({
            "time_offset": 0.0,
            "payload": {
                "command_schema": "LiveSession.1",
                "command": {
                    "event": "STATE_SNAPSHOT",
                    "payload": {
                        "active_timeline_guid": timeline.metadata["sync"]["guid"],
                        "timelines": {timeline.metadata["sync"]["guid"]: timeline_dict},
                        "playback_state": {
                            "playing": False,
                            "current_time": {"value": 9.0, "rate": 24.0}
                        }
                    }
                }
            }
        })

        # Event 2: User adds drawing stroke on frame 10 (local frame 10)
        stroke_cmds = make_stroke(
            points_px=[(50, 50), (100, 100)],
            width=1920,
            height=1080,
            rgba=[1.0, 0.0, 0.0, 1.0],
            brush_size=0.01,
            brush="circle"
        )
        
        # We simulate the insert child of the annotation clip containing the commands
        ann_clip = otio.schema.Clip(name="Annotation_10")
        ann_clip.source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(10.0, 24.0),
            duration=otio.opentime.RationalTime(1.0, 24.0)
        )
        ann_clip.metadata["clip_guid"] = clip_guid
        ann_clip.metadata["annotation_commands"] = [
            json.loads(otio.adapters.write_to_string(cmd, "otio_json")) for cmd in stroke_cmds
        ]
        patcher.ensure_guid_and_map(ann_clip)

        ann_clip_dict = json.loads(
            otio.adapters.write_to_string(ann_clip, "otio_json")
        )

        events.append({
            "time_offset": 0.5,
            "payload": {
                "command_schema": "OTIO_SESSION_1.0",
                "command": {
                    "event": "INSERT_CHILD",
                    "payload": {
                        "parent_uuid": ann_track.metadata["sync"]["guid"],
                        "index": -1,
                        "child_data": ann_clip_dict
                    }
                }
            }
        })

        # Event 3: User changes frame to frame 10 at t=1.0
        events.append({
            "time_offset": 1.0,
            "payload": {
                "command_schema": "PLAYBACK_SETTINGS_1.0",
                "command": {
                    "event": "SET",
                    "payload": {
                        "playing": False,
                        "current_time": {"value": 10.0, "rate": 24.0}
                    }
                }
            }
        })

        # Event 4: End recording at t=5.0
        events.append({
            "time_offset": 5.0,
            "payload": {
                "command_schema": "PLAYBACK_SETTINGS_1.0",
                "command": {
                    "event": "SET",
                    "payload": {
                        "playing": False,
                        "current_time": {"value": 10.0, "rate": 24.0}
                    }
                }
            }
        })

        # Write mock jsonl recording
        with open(self.recording_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")

        # 3. Perform conversion
        convert_recording(self.recording_path, self.output_path, target_fps=24.0)

        # 4. Verify outputs
        self.assertTrue(os.path.exists(self.output_path))
        
        # Check subfolder for PNG
        output_dir = os.path.dirname(self.output_path)
        output_stem = os.path.splitext(os.path.basename(self.output_path))[0]
        annotations_dir = os.path.join(output_dir, f"{output_stem}_annotations")
        self.assertTrue(os.path.isdir(annotations_dir))

        png_files = [f for f in os.listdir(annotations_dir) if f.startswith(f"{clip_guid}_10_") and f.endswith(".png")]
        self.assertEqual(len(png_files), 1)
        expected_png_name = png_files[0]
        expected_png_path = os.path.join(annotations_dir, expected_png_name)
        self.assertTrue(os.path.exists(expected_png_path))

        # Check rendered image (size should be default 1920x1080)
        with Image.open(expected_png_path) as img:
            self.assertEqual(img.size, (1920, 1080))
            self.assertEqual(img.mode, "RGBA")

        # Read back OTIO
        converted_timeline = otio.adapters.read_from_file(self.output_path)
        self.assertEqual(len(converted_timeline.tracks), 2)
        
        bg_track = converted_timeline.tracks[0]
        overlay_track = converted_timeline.tracks[1]
        
        self.assertEqual(bg_track.name, "Background Media")
        self.assertEqual(overlay_track.name, "Annotations Overlay")

        # Since recording lasts 5 seconds, segments are:
        # Segment 1 (t=0.0 to t=1.0, 1.0s = 24 frames): Paused on frame 10 (without drawing yet!)
        # Segment 2 (t=1.0 to t=5.0, 4.0s = 96 frames): Paused on frame 10 (with drawing!)
        self.assertEqual(len(bg_track), 2)
        self.assertEqual(len(overlay_track), 2)

        # Overlay Clip 1 should be a Gap (no drawing during the first 1s)
        self.assertTrue(isinstance(overlay_track[0], otio.schema.Gap))
        self.assertEqual(overlay_track[0].duration().value, 24.0)

        # Overlay Clip 2 should be a Clip pointing to the PNG
        overlay_clip = overlay_track[1]
        self.assertTrue(isinstance(overlay_clip, otio.schema.Clip))
        self.assertEqual(overlay_clip.duration().value, 96.0)
        self.assertTrue(isinstance(overlay_clip.media_reference, otio.schema.ExternalReference))
        self.assertEqual(overlay_clip.media_reference.target_url, f"output_timeline_annotations/{expected_png_name}")
        
        # Verify LinearTimeWarp effect is present
        self.assertEqual(len(overlay_clip.effects), 1)
        self.assertTrue(isinstance(overlay_clip.effects[0], otio.schema.LinearTimeWarp))
        self.assertEqual(overlay_clip.effects[0].time_scalar, 0.0)


if __name__ == "__main__":
    unittest.main()
