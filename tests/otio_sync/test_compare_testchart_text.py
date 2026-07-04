"""Tests for compare_testchart.py's text-annotation anchor comparison (Group 9).

Validates analyse_text_label against synthetic images (no RV/xStudio needed),
so the logic is proven independently of any live render.
"""

import os
import sys
import unittest

import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../testchart")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../python")))

import compare_testchart as ct


def _blank_image(w=400, h=300, bg=(245, 245, 240)):
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = bg
    return arr


def _paint_glyph(arr, x, y, glyph_h, glyph_w=8, color=(255, 61, 61)):
    """Paint a solid rectangle simulating a glyph's ink, top-left at
    (x, y - glyph_h) extending down to the baseline y (left-baseline anchor)."""
    y0 = max(0, y - glyph_h)
    arr[y0:y, x:x + glyph_w] = color
    return arr


class TestAnalyseTextLabel(unittest.TestCase):
    def test_finds_glyph_at_expected_anchor(self):
        arr = _blank_image()
        px, py, font_px = 100, 160, 24
        _paint_glyph(arr, px, py, glyph_h=int(font_px * 0.8))
        offset, found = ct.analyse_text_label(arr, px, py, font_px)
        self.assertTrue(found)
        self.assertLessEqual(offset, 1.0)

    def test_reports_offset_when_glyph_shifted(self):
        arr = _blank_image()
        px, py, font_px = 100, 160, 24
        shift = 12
        _paint_glyph(arr, px + shift, py, glyph_h=int(font_px * 0.8))
        offset, found = ct.analyse_text_label(arr, px, py, font_px)
        self.assertTrue(found)
        self.assertAlmostEqual(offset, shift, delta=1.0)

    def test_not_found_on_blank_image(self):
        arr = _blank_image()
        offset, found = ct.analyse_text_label(arr, 100, 160, 24)
        self.assertFalse(found)
        self.assertEqual(offset, 0.0)

    def test_large_font_ascent_is_covered(self):
        # A 96pt glyph's ascent can exceed 100px above the baseline; the scan
        # region must be sized from font_px, not a small fixed window.
        arr = _blank_image(w=1920, h=1080)
        px, py, font_px = 100, 1000, 96
        _paint_glyph(arr, px, py, glyph_h=int(font_px * 1.05))
        offset, found = ct.analyse_text_label(arr, px, py, font_px)
        self.assertTrue(found)
        self.assertLessEqual(offset, 1.0)


class TestTextLabelsGroundTruth(unittest.TestCase):
    def test_seven_labels_matching_generate_testchart(self):
        self.assertEqual(len(ct.TEXT_LABELS), 7)
        labels = [label for label, _, _, _ in ct.TEXT_LABELS]
        self.assertEqual(labels, [
            "12pt sample", "16pt sample", "24pt sample", "32pt sample",
            "48pt sample", "72pt sample", "96pt sample",
        ])

    def test_positions_are_otio_normalized_and_roundtrip(self):
        # Ground truth is stored in OTIO-norm space; converting back via
        # coords.otio_to_px at the base chart resolution must recover the
        # original pixel positions authored in generate_testchart.py.
        from otio_sync_core import coords
        expected_px = [(100, 160), (100, 220), (100, 300), (100, 400),
                       (100, 550), (100, 750), (100, 1000)]
        for (label, nx, ny, font_px), (epx, epy) in zip(ct.TEXT_LABELS, expected_px):
            px, py = coords.otio_to_px(nx, ny, *ct.TEXT_CHART_SIZE)
            self.assertAlmostEqual(px, epx, places=6)
            self.assertAlmostEqual(py, epy, places=6)


if __name__ == "__main__":
    unittest.main()
