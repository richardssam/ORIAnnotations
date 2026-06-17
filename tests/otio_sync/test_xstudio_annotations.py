import sys
import os
import unittest

# Setup paths
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.append(os.path.join(repo_root, 'python'))
sys.path.append(os.path.join(repo_root, 'xstudio_plugin/ori_sync'))

# Setup OTIO plugin manifest path
manifest_file = os.path.join(repo_root, "otio_event_plugin/plugin_manifest.json")
if os.path.exists(manifest_file):
    existing = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
    if manifest_file not in existing:
        os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = (
            existing + os.pathsep + manifest_file if existing else manifest_file
        )

from otio_sync_core.xs_annotation_codec import (
    xs_captions_to_sync_events,
    sync_events_to_xs_captions,
)
import opentimelineio as otio

# Ensure SyncEvent schema is registered
try:
    otio.schema.schemadef.module_from_name('SyncEvent')
except Exception:
    pass

class TestXStudioAnnotations(unittest.TestCase):
    def test_caption_roundtrip(self):
        aspect_half = 0.8889

        # 1. Define sample xStudio captions
        original_captions = [
            {
                "colour": ["colour", 1, 0.5, 0.6, 0.7],
                "opacity": 0.8,
                "position": ["vec2", 1, 0.2, -0.4],
                "font_name": "Arial",
                "font_size": 42.0,
                "text": "Hello World",
            }
        ]

        # 2. Convert to OTIO SyncEvents
        events = xs_captions_to_sync_events(original_captions, aspect_half)
        self.assertEqual(len(events), 1)
        event = events[0]

        # Verify event properties
        self.assertEqual(list(event.rgba), [0.5, 0.6, 0.7, 0.8])
        # x_otio = x_xs * aspect_half = 0.2 * 0.8889 = 0.17778
        # y_otio = -y_xs * aspect_half = -(-0.4) * 0.8889 = 0.35556
        self.assertAlmostEqual(event.position[0], 0.17778, places=5)
        self.assertAlmostEqual(event.position[1], 0.35556, places=5)
        self.assertEqual(event.font, "Arial")
        self.assertEqual(event.font_size, 42.0)
        self.assertEqual(event.text, "Hello World")

        # 3. Convert back to xStudio captions
        reconverted_captions = sync_events_to_xs_captions(events, aspect_half)
        self.assertEqual(len(reconverted_captions), 1)
        recon = reconverted_captions[0]

        # Verify reconverted matches original
        self.assertEqual(recon["colour"], ["colour", 1, 0.5, 0.6, 0.7])
        self.assertEqual(recon["opacity"], 0.8)
        self.assertAlmostEqual(recon["position"][2], 0.2, places=5)
        self.assertAlmostEqual(recon["position"][3], -0.4, places=5)
        self.assertEqual(recon["font_name"], "Arial")
        self.assertEqual(recon["font_size"], 42.0)
        self.assertEqual(recon["text"], "Hello World")
        self.assertEqual(recon["wrap_width"], 1.5)
        self.assertEqual(recon["justification"], 0)
        print("✓ xStudio caption translation round-trip test passed!")

if __name__ == "__main__":
    unittest.main()
