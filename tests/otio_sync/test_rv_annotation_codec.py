import sys
import os
import unittest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.append(os.path.join(repo_root, 'python'))

manifest_file = os.path.join(repo_root, "otio_event_plugin/plugin_manifest.json")
if os.path.exists(manifest_file):
    existing = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
    if manifest_file not in existing:
        os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = (
            existing + os.pathsep + manifest_file if existing else manifest_file
        )

import opentimelineio as otio
otio.schema.schemadef.module_from_name('SyncEvent')

from otio_sync_core import rv_annotation_codec as codec

se = otio.schema.schemadef.module_from_name('SyncEvent')


def _props(spec):
    """Return {name: value} from a spec's props list."""
    return {name: value for (name, _t, value, _d) in spec["props"]}


class TestRvCodecForward(unittest.TestCase):
    def test_pen_stroke_full_prop_set(self):
        events = [
            se.PaintStart(brush="gauss", rgba=[1.0, 0.0, 0.0, 1.0], friendly_name="rev:sam", uuid="u1"),
            se.PaintPoints(uuid="u1", points=se.PaintVertices([0.1, 0.2], [0.0, 0.1], [0.01, 0.02])),
        ]
        specs = codec.sync_events_to_rv_specs(events, {"frame": 5})
        self.assertEqual(len(specs), 1)
        spec = specs[0]
        self.assertEqual(spec["kind"], "pen")
        self.assertEqual(spec["user"], "sam")
        p = _props(spec)
        for key in ("brush", "color", "debug", "join", "cap", "splat", "width", "points"):
            self.assertIn(key, p)
        self.assertEqual(p["brush"], ["gauss"])
        self.assertEqual(p["splat"], [1])            # gauss → splat 1
        # width scaled by RV_WIDTH_SCALE (0.6)
        self.assertAlmostEqual(p["width"][0], 0.01 * codec.RV_WIDTH_SCALE)
        # points interleaved x,y unchanged (same coord space)
        self.assertEqual(p["points"], [0.1, 0.0, 0.2, 0.1])

    def test_erase_sets_mode(self):
        start = se.PaintStart(brush="oval", rgba=[1.0, 1.0, 1.0, 1.0], friendly_name="", uuid="e1")
        start.type = "erase"
        specs = codec.sync_events_to_rv_specs(
            [start, se.PaintPoints(uuid="e1", points=se.PaintVertices([0.0], [0.0], [0.01]))], {})
        self.assertEqual(specs[0]["kind"], "erase")
        self.assertIn("mode", _props(specs[0]))
        self.assertEqual(_props(specs[0])["mode"], [1])
        self.assertEqual(_props(specs[0])["splat"], [0])  # non-gauss → 0

    def test_text_prop_set_and_scale(self):
        ta = se.TextAnnotation(rgba=[1.0, 1.0, 1.0, 1.0], position=[0.1, -0.2], spacing=0.0,
                               friendly_name="bob", font_size=codec.RV_FONT_SCALE, font="", text="hi",
                               rotation=0.0, scale=1.5, uuid="t1")
        specs = codec.sync_events_to_rv_specs([ta], {"frame": 9})
        p = _props(specs[0])
        self.assertEqual(specs[0]["kind"], "text")
        self.assertEqual(p["scale"], [1.5])
        self.assertEqual(p["size"], [1.0])              # font_size / RV_FONT_SCALE
        self.assertEqual(p["spacing"], [0.8])           # 0.0 → DEFAULT_SPACING
        self.assertEqual(p["startFrame"], [9])
        self.assertEqual(p["position"], [0.1, -0.2])

    def test_shapes_native(self):
        el = se.EllipseAnnotation(min=[-0.1, 0.1], max=[0.1, -0.1], rgba=[0.0, 0.0, 1.0, 1.0],
                                  size=2.0, inner_rgba=[0.0, 0.0, 0.0, 0.0], uuid="el1")
        ar = se.ArrowAnnotation(start=[-0.2, -0.2], end=[0.2, 0.2], rgba=[1.0, 1.0, 1.0, 1.0],
                                size=3.0, uuid="ar1")
        specs = codec.sync_events_to_rv_specs([el, ar], {"frame": 1})
        kinds = [s["kind"] for s in specs]
        self.assertEqual(kinds, ["ellipse", "arrow"])
        self.assertEqual(_props(specs[0])["min"], [-1.1, -0.9])
        self.assertEqual(_props(specs[0])["max"], [1.1, 0.9])
        self.assertEqual(_props(specs[0])["borderWidth"], [2.0])      # borderWidth is size
        self.assertEqual(_props(specs[1])["thickness"], [1.5])        # size/2
        self.assertIn("startPos", _props(specs[1]))

    def test_double_loaded_schemadef_still_classifies(self):
        # Re-register the schemadef (simulates double-load). isinstance would break;
        # schema_name()-based dispatch must still classify the event.
        otio.schema.schemadef.module_from_name('SyncEvent')
        ps = se.PaintStart(brush="oval", rgba=[1.0, 1.0, 1.0, 1.0], friendly_name="", uuid="d1")
        specs = codec.sync_events_to_rv_specs(
            [ps, se.PaintPoints(uuid="d1", points=se.PaintVertices([0.0], [0.0], [0.01]))], {})
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["kind"], "pen")

    def test_degrade_when_kind_unsupported(self):
        # Simulate a host that lacks native ellipse rendering.
        orig = codec.SUPPORTED_KINDS
        try:
            codec.SUPPORTED_KINDS = frozenset(orig - {"ellipse"})
            el = se.EllipseAnnotation(min=[-0.1, 0.1], max=[0.1, -0.1], rgba=[1.0, 0.0, 0.0, 1.0],
                                      size=2.0, inner_rgba=[0.0, 0.0, 0.0, 0.0], uuid="el2")
            specs = codec.sync_events_to_rv_specs([el], {"frame": 1})
            self.assertEqual(specs[0]["kind"], "pen")          # tessellated to a stroke
            self.assertGreater(len(_props(specs[0])["points"]), 4)
        finally:
            codec.SUPPORTED_KINDS = orig


class TestRvCodecReverse(unittest.TestCase):
    def test_text_scale_survives_readback(self):
        # RV text node read-back dict → TextAnnotation preserves scale.
        strokes = [{"kind": "text", "color": [1.0, 1.0, 1.0, 1.0], "position": [0.0, 0.0],
                    "spacing": 0.8, "size": 0.002, "font": "", "text": "hi",
                    "scale": 1.5, "rotation": 0.0, "uuid": "t1", "user": "sam"}]
        events = codec.rv_strokes_to_sync_events(strokes)
        self.assertEqual(events[0].schema_name(), "TextAnnotation")
        self.assertEqual(events[0].scale, 1.5)
        self.assertAlmostEqual(events[0].font_size, 0.002 * codec.RV_FONT_SCALE)

    def test_pen_readback_roundtrips_points(self):
        strokes = [{"kind": "pen", "brush": "circle", "color": [1.0, 0.0, 0.0, 1.0],
                    "points": [0.1, 0.0, 0.2, 0.1], "width": [0.01, 0.02], "uuid": "u1", "user": "sam"}]
        events = codec.rv_strokes_to_sync_events(strokes)
        self.assertEqual([e.schema_name() for e in events], ["PaintStart", "PaintPoint"])
        pts = events[1].points
        self.assertEqual(list(pts.x), [0.1, 0.2])
        self.assertEqual(list(pts.y), [0.0, 0.1])

    def test_pen_width_forward_reverse_roundtrip(self):
        # Forward (SyncEvent size → RV width) then reverse (RV width read-back
        # → SyncEvent size) should reproduce the original size through the
        # `* RV_WIDTH_SCALE` / `/ RV_WIDTH_SCALE` transforms, mirroring
        # test_shape_forward_reverse_roundtrip for pen strokes.
        ps = se.PaintStart(brush="circle", rgba=[1.0, 0.0, 0.0, 1.0], uuid="w1")
        pts = se.PaintPoints(uuid="w1", points=se.PaintVertices([0.1], [0.0], [0.02]))
        specs = codec.sync_events_to_rv_specs([ps, pts], {})
        rv_width = _props(specs[0])["width"][0]
        readback = {"kind": "pen", "brush": "circle", "color": [1.0, 0.0, 0.0, 1.0],
                    "points": [0.1, 0.0], "width": [rv_width], "uuid": "w1", "user": "user"}
        events = codec.rv_strokes_to_sync_events([readback])
        self.assertAlmostEqual(list(events[1].points.size)[0], 0.02)

    def test_ellipse_readback(self):
        strokes = [{"kind": "ellipse", "min": [-0.1, 0.1], "max": [0.1, -0.1],
                    "rgba": [0.0, 0.0, 1.0, 1.0], "inner_rgba": [0.0, 0.0, 0.0, 0.0],
                    "size": 2.0, "uuid": "el1", "user": "sam"}]
        events = codec.rv_strokes_to_sync_events(strokes)
        self.assertEqual(events[0].schema_name(), "EllipseAnnotation")
        self.assertEqual(list(events[0].min), [-0.1, 0.1])
        self.assertEqual(events[0].size, 2.0)
        self.assertEqual(events[0].uuid, "el1")

    def test_rect_readback(self):
        strokes = [{"kind": "rect", "min": [-0.2, 0.2], "max": [0.2, -0.1],
                    "rgba": [1.0, 0.0, 0.0, 1.0], "inner_rgba": [0.0, 1.0, 0.0, 0.5],
                    "size": 2.0, "uuid": "r1", "user": "sam"}]
        events = codec.rv_strokes_to_sync_events(strokes)
        self.assertEqual(events[0].schema_name(), "RectangleAnnotation")
        self.assertEqual(list(events[0].max), [0.2, -0.1])

    def test_arrow_readback(self):
        strokes = [{"kind": "arrow", "start": [-0.3, -0.3], "end": [0.3, 0.3],
                    "rgba": [1.0, 1.0, 1.0, 1.0], "size": 3.0, "uuid": "a1", "user": "sam"}]
        events = codec.rv_strokes_to_sync_events(strokes)
        self.assertEqual(events[0].schema_name(), "ArrowAnnotation")
        self.assertEqual(list(events[0].start), [-0.3, -0.3])
        self.assertEqual(list(events[0].end), [0.3, 0.3])
        self.assertEqual(events[0].size, 3.0)

    def test_shape_forward_reverse_roundtrip(self):
        # Forward (SyncEvent → specs) then reverse (read-back dict → SyncEvent)
        # should reproduce the same geometry through the borderWidth/thickness
        # and min/max offset transforms.
        el = se.EllipseAnnotation(min=[-0.15, 0.05], max=[0.35, -0.25], rgba=[0.0, 0.0, 1.0, 1.0],
                                  size=1.5, inner_rgba=[1.0, 1.0, 0.0, 0.8], uuid="rt1")
        specs = codec.sync_events_to_rv_specs([el], {"frame": 0})
        border_width = dict((n, v) for (n, _t, v, _d) in specs[0]["props"])["borderWidth"][0]
        r_min = dict((n, v) for (n, _t, v, _d) in specs[0]["props"])["min"]
        r_max = dict((n, v) for (n, _t, v, _d) in specs[0]["props"])["max"]
        
        # Simulate the applier's read side: size = borderWidth directly, contract min/max
        half = border_width / 2.0
        c_min = [r_min[0] + half, r_min[1] + half]
        c_max = [r_max[0] - half, r_max[1] - half]
        readback = {"kind": "ellipse", "min": c_min, "max": c_max, "rgba": el.rgba,
                    "inner_rgba": el.inner_rgba, "size": border_width, "uuid": "rt1", "user": "user"}
        events = codec.rv_strokes_to_sync_events([readback])
        self.assertAlmostEqual(events[0].size, el.size)
        self.assertAlmostEqual(events[0].min[0], el.min[0])
        self.assertAlmostEqual(events[0].min[1], el.min[1])
        self.assertAlmostEqual(events[0].max[0], el.max[0])
        self.assertAlmostEqual(events[0].max[1], el.max[1])


if __name__ == "__main__":
    unittest.main()
