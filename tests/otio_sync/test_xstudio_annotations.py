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

from otio_sync_core import xs_annotation_codec
from otio_sync_core.xs_annotation_codec import (
    xs_captions_to_sync_events,
    sync_events_to_xs_captions,
)
from otio_sync_core import coords
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
        self.assertEqual(event.font_size, 16.8)
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

    def test_caption_spacing_uses_coords_default(self):
        # xStudio has no spacing concept; emitted spacing must be the shared
        # coords.DEFAULT_SPACING (0.8, RV-neutral), not the old hardcoded 0.0
        # (which collapsed letter spacing when rendered in RV).
        captions = [{
            "colour": ["colour", 1, 1.0, 1.0, 1.0], "opacity": 1.0,
            "position": ["vec2", 1, 0.0, 0.0], "font_name": "Arial",
            "font_size": 50.0, "text": "hi",
        }]
        events = xs_captions_to_sync_events(captions, 0.8889)
        self.assertEqual(events[0].spacing, coords.DEFAULT_SPACING)
        self.assertEqual(coords.DEFAULT_SPACING, 0.8)

    def test_contract_entry_points_roundtrip(self):
        # D9 common contract: HOST_ID/SUPPORTED_KINDS declared, and
        # to_sync_events/from_sync_events round-trip through the native
        # {"strokes", "captions"} shape matching Bookmark.set_annotation.
        self.assertEqual(xs_annotation_codec.HOST_ID, "xstudio")
        self.assertEqual(xs_annotation_codec.SUPPORTED_KINDS, frozenset({"pen", "erase", "text"}))

        native = {
            "strokes": [{"colour": [1.0, 0.0, 0.0], "points": [0.1, 0.1, 1.0, 1.0], "type": "Brush"}],
            "captions": [{"colour": ["colour", 1, 1.0, 1.0, 1.0], "opacity": 1.0,
                          "position": ["vec2", 1, 0.0, 0.0], "font_name": "Arial",
                          "font_size": 50.0, "text": "hi"}],
        }
        events = xs_annotation_codec.to_sync_events(native, {"aspect_half": 0.8889})
        schema_names = [e.schema_name() for e in events]
        self.assertIn("PaintStart", schema_names)
        self.assertIn("PaintPoint", schema_names)
        self.assertIn("TextAnnotation", schema_names)

        back = xs_annotation_codec.from_sync_events(events, {"aspect_half": 0.8889})
        self.assertEqual(len(back["strokes"]), 1)
        self.assertEqual(len(back["captions"]), 1)
        self.assertEqual(back["captions"][0]["text"], "hi")

if __name__ == "__main__":
    unittest.main()
