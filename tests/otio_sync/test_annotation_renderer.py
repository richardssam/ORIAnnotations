# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Pillow-based annotation renderer."""

from __future__ import annotations

import os
import sys
import unittest
from PIL import Image

# Ensure project root is in path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if project_root not in sys.path:
    sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "python"))

import opentimelineio as otio
from sync_recorder.annotation_renderer import render_annotations
from otio_sync_core.annotation_builder import make_stroke, make_text


class TestAnnotationRenderer(unittest.TestCase):
    """Test suite validating drawing correctness in annotation_renderer."""

    def test_solid_stroke_rendering(self) -> None:
        """Verify that a simple solid stroke renders the expected color and bounding pixels."""
        # Draw a thick horizontal line in red
        events = make_stroke(
            points_px=[(100, 100), (200, 100)],
            width=300,
            height=300,
            rgba=[1.0, 0.0, 0.0, 1.0],
            brush_size=10.0 / 300.0,  # 10px diameter -> radius 5.0
            brush="circle",
        )

        canvas = render_annotations(events, width=300, height=300)
        self.assertEqual(canvas.size, (300, 300))
        self.assertEqual(canvas.mode, "RGBA")

        # The center of the stroke should be solid red (255, 0, 0, 255)
        pixel_center = canvas.getpixel((150, 100))
        self.assertEqual(pixel_center, (255, 0, 0, 255))

        # A pixel far outside the stroke should be completely transparent (0, 0, 0, 0)
        pixel_bg = canvas.getpixel((150, 200))
        self.assertEqual(pixel_bg, (0, 0, 0, 0))

    def test_eraser_stroke_rendering(self) -> None:
        """Verify that an eraser stroke clears existing drawing content to transparent."""
        # 1. Start with a red stroke
        red_stroke = make_stroke(
            points_px=[(100, 100), (200, 100)],
            width=300,
            height=300,
            rgba=[1.0, 0.0, 0.0, 1.0],
            brush_size=20.0 / 300.0,
            brush="circle",
        )

        # 2. Add an eraser stroke cutting vertically through the center of the first stroke
        eraser_stroke = make_stroke(
            points_px=[(150, 50), (150, 150)],
            width=300,
            height=300,
            rgba=[0.0, 0.0, 0.0, 1.0],
            brush_size=10.0 / 300.0,
            brush="circle",
        )
        # Manually mark PaintStart as an eraser
        for event in eraser_stroke:
            if hasattr(event, "type"):
                event.type = "erase"
            elif isinstance(event, dict):
                event["type"] = "erase"

        events = red_stroke + eraser_stroke
        canvas = render_annotations(events, width=300, height=300)

        # The intersection (150, 100) should now be transparent due to the eraser
        pixel_intersect = canvas.getpixel((150, 100))
        self.assertEqual(pixel_intersect[3], 0)  # Alpha should be 0

        # The remaining parts (e.g. 110, 100) should still be red
        pixel_left = canvas.getpixel((110, 100))
        self.assertEqual(pixel_left, (255, 0, 0, 255))

    def test_text_annotation_rendering(self) -> None:
        """Verify that a text annotation draws pixels onto the transparent canvas."""
        events = make_text(
            px=150,
            py=150,
            width=300,
            height=300,
            text="TEST",
            rgba=[0.0, 0.0, 1.0, 1.0],  # Blue
            font_size=40.0,
        )

        canvas = render_annotations(events, width=300, height=300)

        # Ensure the output has blue pixels drawn
        has_blue_pixel = False
        for x in range(300):
            for y in range(300):
                r, g, b, a = canvas.getpixel((x, y))
                if b > 200 and a > 0:
                    has_blue_pixel = True
                    break
            if has_blue_pixel:
                break

        self.assertTrue(has_blue_pixel, "Text should render some blue pixels onto the canvas")


if __name__ == "__main__":
    unittest.main()
