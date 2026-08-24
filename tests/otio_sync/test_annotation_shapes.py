import os
import sys

# Setup manifest path before importing opentimelineio
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
manifest_path = os.path.join(project_root, "otio_event_plugin", "plugin_manifest.json")
if "OTIO_PLUGIN_MANIFEST_PATH" in os.environ:
    os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = manifest_path + os.pathsep + os.environ["OTIO_PLUGIN_MANIFEST_PATH"]
else:
    os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = manifest_path

import opentimelineio as otio
import unittest
import json

class TestAnnotationShapes(unittest.TestCase):
    def setUp(self):
        # Retrieve the SyncEvent module where our custom schemas are registered
        self.se = otio.schema.schemadef.module_from_name('SyncEvent')

    def test_ellipse_annotation_roundtrip(self):
        ell = self.se.EllipseAnnotation(
            min=[-0.25, 0.25],
            max=[0.25, -0.25],
            rgba=[1.0, 0.0, 0.0, 1.0],
            size=2.0,
            inner_rgba=[0.0, 0.0, 1.0, 0.5],
            uuid="test-ellipse-uuid",
            timestamp="2026-07-02T12:00:00Z"
        )
        self.assertEqual(list(ell.min), [-0.25, 0.25])
        self.assertEqual(list(ell.max), [0.25, -0.25])
        self.assertEqual(list(ell.rgba), [1.0, 0.0, 0.0, 1.0])
        self.assertEqual(ell.size, 2.0)
        self.assertEqual(list(ell.inner_rgba), [0.0, 0.0, 1.0, 0.5])
        self.assertEqual(ell.uuid, "test-ellipse-uuid")
        self.assertEqual(ell.timestamp, "2026-07-02T12:00:00Z")

        # Serialize
        serialized = otio.adapters.write_to_string(ell, "otio_json")
        data = json.loads(serialized)
        self.assertEqual(data["OTIO_SCHEMA"], "EllipseAnnotation.1")
        self.assertEqual(data["min"], [-0.25, 0.25])
        self.assertEqual(data["max"], [0.25, -0.25])
        self.assertEqual(data["size"], 2.0)

        # Deserialize
        deserialized = otio.adapters.read_from_string(serialized, "otio_json")
        self.assertIsInstance(deserialized, self.se.EllipseAnnotation)
        self.assertEqual(list(deserialized.min), [-0.25, 0.25])
        self.assertEqual(list(deserialized.max), [0.25, -0.25])
        self.assertEqual(list(deserialized.rgba), [1.0, 0.0, 0.0, 1.0])
        self.assertEqual(list(deserialized.inner_rgba), [0.0, 0.0, 1.0, 0.5])
        self.assertEqual(deserialized.uuid, "test-ellipse-uuid")

    def test_rectangle_annotation_roundtrip(self):
        rect = self.se.RectangleAnnotation(
            min=[-0.2, 0.2],
            max=[0.2, -0.1],
            rgba=[0.0, 1.0, 0.0, 1.0],
            size=2.5,
            inner_rgba=[1.0, 0.0, 0.0, 0.7],
            uuid="test-rectangle-uuid",
            timestamp="2026-07-02T12:05:00Z"
        )
        self.assertEqual(list(rect.min), [-0.2, 0.2])
        self.assertEqual(list(rect.max), [0.2, -0.1])
        self.assertEqual(list(rect.rgba), [0.0, 1.0, 0.0, 1.0])
        self.assertEqual(rect.size, 2.5)
        self.assertEqual(list(rect.inner_rgba), [1.0, 0.0, 0.0, 0.7])
        self.assertEqual(rect.uuid, "test-rectangle-uuid")
        self.assertEqual(rect.timestamp, "2026-07-02T12:05:00Z")

        # Serialize
        serialized = otio.adapters.write_to_string(rect, "otio_json")
        data = json.loads(serialized)
        self.assertEqual(data["OTIO_SCHEMA"], "RectangleAnnotation.1")
        self.assertEqual(data["min"], [-0.2, 0.2])
        self.assertEqual(data["max"], [0.2, -0.1])
        self.assertEqual(data["size"], 2.5)

        # Deserialize
        deserialized = otio.adapters.read_from_string(serialized, "otio_json")
        self.assertIsInstance(deserialized, self.se.RectangleAnnotation)
        self.assertEqual(list(deserialized.min), [-0.2, 0.2])
        self.assertEqual(list(deserialized.max), [0.2, -0.1])
        self.assertEqual(list(deserialized.rgba), [0.0, 1.0, 0.0, 1.0])
        self.assertEqual(list(deserialized.inner_rgba), [1.0, 0.0, 0.0, 0.7])
        self.assertEqual(deserialized.uuid, "test-rectangle-uuid")

    def test_arrow_annotation_roundtrip(self):
        arrow = self.se.ArrowAnnotation(
            start=[-0.4, -0.4],
            end=[0.4, 0.4],
            rgba=[1.0, 1.0, 1.0, 1.0],
            size=3.0,
            uuid="test-arrow-uuid",
            timestamp="2026-07-02T12:10:00Z"
        )
        self.assertEqual(list(arrow.start), [-0.4, -0.4])
        self.assertEqual(list(arrow.end), [0.4, 0.4])
        self.assertEqual(list(arrow.rgba), [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(arrow.size, 3.0)
        self.assertEqual(arrow.uuid, "test-arrow-uuid")
        self.assertEqual(arrow.timestamp, "2026-07-02T12:10:00Z")

        # Serialize
        serialized = otio.adapters.write_to_string(arrow, "otio_json")
        data = json.loads(serialized)
        self.assertEqual(data["OTIO_SCHEMA"], "ArrowAnnotation.1")
        self.assertEqual(data["start"], [-0.4, -0.4])
        self.assertEqual(data["end"], [0.4, 0.4])
        self.assertEqual(data["size"], 3.0)

        # Deserialize
        deserialized = otio.adapters.read_from_string(serialized, "otio_json")
        self.assertIsInstance(deserialized, self.se.ArrowAnnotation)
        self.assertEqual(list(deserialized.start), [-0.4, -0.4])
        self.assertEqual(list(deserialized.end), [0.4, 0.4])
        self.assertEqual(list(deserialized.rgba), [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(deserialized.uuid, "test-arrow-uuid")

    def test_type_validation(self):
        with self.assertRaises(TypeError):
            self.se.EllipseAnnotation(min="not-a-list")
        with self.assertRaises(TypeError):
            self.se.RectangleAnnotation(max="not-a-list")
        with self.assertRaises(TypeError):
            self.se.ArrowAnnotation(start="not-a-list")
        with self.assertRaises(TypeError):
            self.se.EllipseAnnotation(rgba="not-a-list")
        with self.assertRaises(TypeError):
            self.se.EllipseAnnotation(size="not-a-float")
        with self.assertRaises(TypeError):
            self.se.EllipseAnnotation(inner_rgba="not-a-list")

if __name__ == '__main__':
    unittest.main()
