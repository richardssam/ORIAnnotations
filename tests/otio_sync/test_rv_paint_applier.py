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

from otio_sync_core import rv_paint_applier as applier
from otio_sync_core.rv_annotation_codec import TYPE_STRING, TYPE_FLOAT, TYPE_INT


class FakeCommands:
    """Minimal in-memory stand-in for RV's commands module."""
    StringType = "S"
    FloatType = "F"
    IntType = "I"

    def __init__(self):
        self.props = {}   # name -> {"type", "dim", "value"}

    def propertyExists(self, name):
        return name in self.props

    def newProperty(self, name, ptype, dim):
        self.props[name] = {"type": ptype, "dim": dim, "value": []}

    def _set(self, name, value):
        self.props[name]["value"] = list(value)

    def setStringProperty(self, name, value, allowResize=True):
        self._set(name, value)

    def setFloatProperty(self, name, value, allowResize=True):
        self._set(name, value)

    def setIntProperty(self, name, value, allowResize=True):
        self._set(name, value)

    def getStringProperty(self, name):
        return self.props[name]["value"]

    def getIntProperty(self, name):
        return self.props[name]["value"]

    def getFloatProperty(self, name):
        return self.props[name]["value"]


def _pen_spec(uuid, user="sam", color=(1.0, 0.0, 0.0, 1.0)):
    return {"kind": "pen", "uuid": uuid, "user": user, "props": [
        ("brush", TYPE_STRING, ["circle"], 1),
        ("color", TYPE_FLOAT, list(color), 4),
        ("points", TYPE_FLOAT, [0.0, 0.0, 0.1, 0.1], 2),
        ("uuid", TYPE_STRING, [uuid], 1),
    ]}


class TestApplierAppend(unittest.TestCase):
    def test_append_creates_nodes_and_order(self):
        c = FakeCommands()
        nxt = applier.apply_specs([_pen_spec("u1"), _pen_spec("u2")], c,
                                  rv_node="G", frame=7, mode="append")
        order = c.getStringProperty("G.frame:7.order")
        self.assertEqual(order, ["pen:1:7:sam", "pen:2:7:sam"])
        self.assertEqual(c.getFloatProperty("G.pen:1:7:sam.color"), [1.0, 0.0, 0.0, 1.0])
        self.assertEqual(nxt, 3)
        # paint tags ensured
        self.assertTrue(c.propertyExists("G.tag.annotate"))
        self.assertTrue(c.propertyExists("G.internal.creationContext"))

    def test_append_preserves_existing_order(self):
        c = FakeCommands()
        applier.apply_specs([_pen_spec("u1")], c, rv_node="G", frame=7, mode="append")
        applier.apply_specs([_pen_spec("u2")], c, rv_node="G", frame=7, mode="append", start_id=2)
        self.assertEqual(c.getStringProperty("G.frame:7.order"),
                         ["pen:1:7:sam", "pen:2:7:sam"])


class TestApplierReconcile(unittest.TestCase):
    def _seed(self, c):
        applier.apply_specs([_pen_spec("u1"), _pen_spec("u2")], c,
                            rv_node="G", frame=7, mode="append")

    def test_reconcile_updates_in_place(self):
        c = FakeCommands()
        self._seed(c)
        applier.apply_specs([_pen_spec("u1", color=(0.0, 1.0, 0.0, 1.0)),
                             _pen_spec("u2")], c, rv_node="G", frame=7, mode="reconcile")
        # No new nodes; u1 colour updated in place.
        self.assertEqual(c.getStringProperty("G.frame:7.order"),
                         ["pen:1:7:sam", "pen:2:7:sam"])
        self.assertEqual(c.getFloatProperty("G.pen:1:7:sam.color"), [0.0, 1.0, 0.0, 1.0])

    def test_reconcile_adds_new(self):
        c = FakeCommands()
        self._seed(c)
        applier.apply_specs([_pen_spec("u1"), _pen_spec("u2"), _pen_spec("u3")], c,
                            rv_node="G", frame=7, mode="reconcile")
        order = c.getStringProperty("G.frame:7.order")
        self.assertEqual(len(order), 3)
        self.assertIn("pen:3:7:sam", order)

    def test_reconcile_prunes_deleted(self):
        c = FakeCommands()
        self._seed(c)
        # Only u1 present now → u2 pruned.
        applier.apply_specs([_pen_spec("u1")], c, rv_node="G", frame=7, mode="reconcile")
        self.assertEqual(c.getStringProperty("G.frame:7.order"), ["pen:1:7:sam"])

    def test_reconcile_of_other_kind_does_not_prune_pen(self):
        # Regression: annotation_sync.py reconciles text/shape kinds one at a
        # time (or excludes strokes entirely on a "replace"), never mentioning
        # pen uuids in that call's specs. A reconcile batch that says nothing
        # about a kind must leave that kind's existing nodes alone -- it was
        # wiping out already-drawn pen strokes on every unrelated text/shape
        # update.
        c = FakeCommands()
        self._seed(c)
        applier.apply_specs([_text_spec("t1")], c, rv_node="G", frame=7, mode="reconcile")
        order = c.getStringProperty("G.frame:7.order")
        self.assertIn("pen:1:7:sam", order)
        self.assertIn("pen:2:7:sam", order)
        self.assertIn("text:3:7:sam", order)


class TestApplierGuards(unittest.TestCase):
    def test_unknown_kind_raises(self):
        c = FakeCommands()
        bad = {"kind": "squiggle", "uuid": "x", "user": "sam", "props": []}
        with self.assertRaises(ValueError):
            applier.apply_specs([bad], c, rv_node="G", frame=1, mode="append")

    def test_unknown_mode_raises(self):
        c = FakeCommands()
        with self.assertRaises(ValueError):
            applier.apply_specs([_pen_spec("u1")], c, rv_node="G", frame=1, mode="bogus")


def _text_spec(uuid, user="sam"):
    return {"kind": "text", "uuid": uuid, "user": user, "props": [
        ("position", TYPE_FLOAT, [0.1, -0.2], 2),
        ("color", TYPE_FLOAT, [1.0, 1.0, 1.0, 1.0], 4),
        ("spacing", TYPE_FLOAT, [0.8], 1),
        ("size", TYPE_FLOAT, [1.0], 1),
        ("font", TYPE_STRING, [""], 1),
        ("text", TYPE_STRING, ["hi"], 1),
        ("scale", TYPE_FLOAT, [1.5], 1),
        ("rotation", TYPE_FLOAT, [0.0], 1),
        ("origin", TYPE_STRING, [""], 1),
        ("debug", TYPE_INT, [0], 1),
        ("startFrame", TYPE_INT, [7], 1),
        ("duration", TYPE_INT, [1], 1),
        ("mode", TYPE_INT, [0], 1),
        ("uuid", TYPE_STRING, [uuid], 1),
        ("softDeleted", TYPE_INT, [0], 1),
    ]}


def _shape_spec(kind, uuid, user="sam"):
    return {"kind": kind, "uuid": uuid, "user": user, "props": [
        ("min", TYPE_FLOAT, [-0.1, 0.1], 2),
        ("max", TYPE_FLOAT, [0.1, -0.1], 2),
        ("borderColor", TYPE_FLOAT, [0.0, 0.0, 1.0, 1.0], 4),
        ("innerColor", TYPE_FLOAT, [0.0, 0.0, 0.0, 0.0], 4),
        ("borderWidth", TYPE_FLOAT, [1.0], 1),
        ("startFrame", TYPE_INT, [7], 1),
        ("duration", TYPE_INT, [1], 1),
        ("eye", TYPE_INT, [2], 1),
        ("uuid", TYPE_STRING, [uuid], 1),
        ("softDeleted", TYPE_INT, [0], 1),
    ]}


class TestApplierReadFrameStrokes(unittest.TestCase):
    def test_reads_pen_stroke(self):
        c = FakeCommands()
        applier.apply_specs([_pen_spec("u1")], c, rv_node="G", frame=7, mode="append")
        strokes = applier.read_frame_strokes(c, "G", 7)
        self.assertEqual(len(strokes), 1)
        self.assertEqual(strokes[0]["kind"], "pen")
        self.assertEqual(strokes[0]["user"], "sam")
        self.assertEqual(strokes[0]["color"], [1.0, 0.0, 0.0, 1.0])
        self.assertEqual(strokes[0]["points"], [0.0, 0.0, 0.1, 0.1])

    def test_read_stroke_single_item(self):
        # Single-item reader (used by the live-sync broadcaster) matches what
        # read_frame_strokes returns for the same item.
        c = FakeCommands()
        applier.apply_specs([_pen_spec("u1")], c, rv_node="G", frame=7, mode="append")
        order = c.getStringProperty("G.frame:7.order")
        stroke = applier.read_stroke(c, "G", order[0])
        self.assertEqual(stroke["kind"], "pen")
        self.assertEqual(stroke["color"], [1.0, 0.0, 0.0, 1.0])

    def test_read_stroke_unknown_prefix_returns_none(self):
        c = FakeCommands()
        self.assertIsNone(applier.read_stroke(c, "G", "squiggle:1:7:sam"))

    def test_reads_erase_from_mode(self):
        c = FakeCommands()
        spec = _pen_spec("u1")
        spec["props"].append(("mode", TYPE_INT, [1], 1))
        applier.apply_specs([spec], c, rv_node="G", frame=7, mode="append")
        strokes = applier.read_frame_strokes(c, "G", 7)
        self.assertEqual(strokes[0]["kind"], "erase")

    def test_reads_text_node(self):
        c = FakeCommands()
        applier.apply_specs([_text_spec("t1")], c, rv_node="G", frame=7, mode="append")
        strokes = applier.read_frame_strokes(c, "G", 7)
        self.assertEqual(strokes[0]["kind"], "text")
        self.assertEqual(strokes[0]["text"], "hi")
        self.assertEqual(strokes[0]["scale"], 1.5)
        self.assertEqual(strokes[0]["uuid"], "t1")

    def test_reads_shape_and_inverts_border_width(self):
        c = FakeCommands()
        applier.apply_specs([_shape_spec("ellipse", "e1")], c, rv_node="G", frame=7, mode="append")
        strokes = applier.read_frame_strokes(c, "G", 7)
        self.assertEqual(strokes[0]["kind"], "ellipse")
        # borderWidth was written as size/2.0 (=1.0); read back should recover size=2.0.
        self.assertEqual(strokes[0]["size"], 2.0)
        self.assertEqual(strokes[0]["min"], [-0.1, 0.1])

    def test_write_then_read_then_codec_roundtrip(self):
        # Full loop: apply_specs writes → read_frame_strokes reads →
        # rv_strokes_to_sync_events converts back to a SyncEvent.
        from otio_sync_core import rv_annotation_codec as codec
        c = FakeCommands()
        applier.apply_specs([_text_spec("t2")], c, rv_node="G", frame=7, mode="append")
        strokes = applier.read_frame_strokes(c, "G", 7)
        events = codec.rv_strokes_to_sync_events(strokes)
        self.assertEqual(events[0].schema_name(), "TextAnnotation")
        self.assertEqual(events[0].scale, 1.5)
        self.assertEqual(events[0].text, "hi")


if __name__ == "__main__":
    unittest.main()
