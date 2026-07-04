import sys
import os
import unittest

# Setup paths
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.append(os.path.join(repo_root, 'python'))

from otio_sync_core import coords


class TestCoords(unittest.TestCase):
    def test_aspect_half_16_9(self):
        self.assertAlmostEqual(coords.aspect_half(1920, 1080), 8.0 / 9.0)

    def test_aspect_half_zero_height_fallback(self):
        self.assertEqual(coords.aspect_half(1920, 0), coords.DEFAULT_ASPECT_HALF)

    def test_aspect_half_none_height_fallback(self):
        self.assertEqual(coords.aspect_half(1920, None), coords.DEFAULT_ASPECT_HALF)

    def test_centre_pixel_maps_to_origin(self):
        nx, ny = coords.px_to_otio(1920 / 2.0, 1080 / 2.0, 1920, 1080)
        self.assertAlmostEqual(nx, 0.0)
        self.assertAlmostEqual(ny, 0.0)

    def test_px_otio_roundtrip_stable(self):
        W, H = 1920, 1080
        for px, py in [(0, 0), (100, 250), (1919, 1079), (960, 540)]:
            nx, ny = coords.px_to_otio(px, py, W, H)
            rpx, rpy = coords.otio_to_px(nx, ny, W, H)
            self.assertAlmostEqual(rpx, px, places=6)
            self.assertAlmostEqual(rpy, py, places=6)

    def test_x_half_extent(self):
        # Right edge (px = W) maps to +W/(2H); left edge (px = 0) maps to -W/(2H).
        W, H = 1920, 1080
        nx_right, _ = coords.px_to_otio(W, H / 2.0, W, H)
        nx_left, _ = coords.px_to_otio(0, H / 2.0, W, H)
        self.assertAlmostEqual(nx_right, coords.aspect_half(W, H))
        self.assertAlmostEqual(nx_left, -coords.aspect_half(W, H))

    def test_y_up(self):
        # A pixel above centre (smaller py) should map to positive ny (Y-up).
        _, ny = coords.px_to_otio(960, 200, 1920, 1080)
        self.assertGreater(ny, 0.0)

    def test_matches_annotation_builder_formula(self):
        # coords.px_to_otio must equal the generator's px_to_norm byte-for-byte.
        from otio_sync_core.annotation_builder import px_to_norm
        for px, py in [(0, 0), (123, 456), (1920, 1080), (960, 540)]:
            self.assertEqual(coords.px_to_otio(px, py, 1920, 1080),
                             px_to_norm(px, py, 1920, 1080))


if __name__ == "__main__":
    unittest.main()
