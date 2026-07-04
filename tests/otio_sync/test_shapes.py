import sys
import os
import math
import unittest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.append(os.path.join(repo_root, 'python'))

from otio_sync_core import shapes


def _legacy_rect(min_val, max_val):
    """Pre-refactor rectangle tessellation (copied verbatim from xs codec)."""
    min_val, max_val = list(min_val), list(max_val)
    return [
        [min_val[0], min_val[1]],
        [max_val[0], min_val[1]],
        [max_val[0], max_val[1]],
        [min_val[0], max_val[1]],
        [min_val[0], min_val[1]],
    ]


def _legacy_ellipse(min_val, max_val):
    min_val, max_val = list(min_val), list(max_val)
    cx = (min_val[0] + max_val[0]) / 2.0
    cy = (min_val[1] + max_val[1]) / 2.0
    rx = (max_val[0] - min_val[0]) / 2.0
    ry = (max_val[1] - min_val[1]) / 2.0
    steps = 36
    pts = []
    for step in range(steps + 1):
        theta = 2.0 * math.pi * step / steps
        pts.append([cx + rx * math.cos(theta), cy + ry * math.sin(theta)])
    return pts


def _legacy_arrow(start_val, end_val):
    start_val, end_val = list(start_val), list(end_val)
    pts_list = [start_val, end_val]
    dx = end_val[0] - start_val[0]
    dy = end_val[1] - start_val[1]
    length = math.sqrt(dx * dx + dy * dy)
    if length > 0.0001:
        ux = dx / length
        uy = dy / length
        nx = -uy
        ny = ux
        hl = min(0.3, length * 0.25)
        lx = end_val[0] - hl * ux + 0.5 * hl * nx
        ly = end_val[1] - hl * uy + 0.5 * hl * ny
        rx_val = end_val[0] - hl * ux - 0.5 * hl * nx
        ry_val = end_val[1] - hl * uy - 0.5 * hl * ny
        pts_list.extend([[lx, ly], end_val, [rx_val, ry_val]])
    return pts_list


class TestShapesBehaviorPreserving(unittest.TestCase):
    CASES = [
        ([-0.2, 0.2], [0.2, -0.1]),
        ([-0.15, 0.05], [0.35, -0.25]),
        ([0.0, 0.0], [0.0, 0.0]),  # degenerate
    ]

    def test_rect_matches_legacy(self):
        for mn, mx in self.CASES:
            self.assertEqual(shapes.rect_polyline(mn, mx), _legacy_rect(mn, mx))

    def test_ellipse_matches_legacy(self):
        for mn, mx in self.CASES:
            self.assertEqual(shapes.ellipse_polyline(mn, mx), _legacy_ellipse(mn, mx))

    def test_arrow_matches_legacy(self):
        for start, end in [([-0.3, -0.3], [0.3, 0.3]), ([0.0, 0.0], [0.0, 0.0])]:
            self.assertEqual(shapes.arrow_polyline(start, end), _legacy_arrow(start, end))


if __name__ == "__main__":
    unittest.main()
