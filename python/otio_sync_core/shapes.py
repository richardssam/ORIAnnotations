#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Host-neutral shape tessellation.

Converts ellipse, rectangle, and arrow annotations into OTIO-normalized point
polylines. This geometry is shared, not host-specific: any host codec whose
native capabilities exclude first-class shapes can degrade gracefully by
rendering the shape as a stroke polyline produced here.

All coordinates are in OTIO-normalized space (H-normalised, Y-up, centre
origin — see :mod:`otio_sync_core.coords`). Callers convert to their own host
coordinate space afterwards.
"""

from __future__ import annotations

import math
from typing import List, Sequence

Point = List[float]


def rect_polyline(min_xy: Sequence[float], max_xy: Sequence[float]) -> List[Point]:
    """Return the closed 5-point rectangle outline for a min/max bounding box.

    :param min_xy: ``[x, y]`` top-left corner (OTIO-norm).
    :param max_xy: ``[x, y]`` bottom-right corner (OTIO-norm).
    :returns: Five points tracing the rectangle, closing back to the start.
    """
    mn, mx = list(min_xy), list(max_xy)
    return [
        [mn[0], mn[1]],
        [mx[0], mn[1]],
        [mx[0], mx[1]],
        [mn[0], mx[1]],
        [mn[0], mn[1]],
    ]


def ellipse_polyline(
    min_xy: Sequence[float], max_xy: Sequence[float], steps: int = 36
) -> List[Point]:
    """Return an ``steps``-segment polyline approximating the ellipse in the box.

    :param min_xy: ``[x, y]`` bounding box min corner (OTIO-norm).
    :param max_xy: ``[x, y]`` bounding box max corner (OTIO-norm).
    :param steps: Number of segments (``steps + 1`` points, closing the loop).
    :returns: Polyline points tracing the ellipse.
    """
    mn, mx = list(min_xy), list(max_xy)
    cx = (mn[0] + mx[0]) / 2.0
    cy = (mn[1] + mx[1]) / 2.0
    rx = (mx[0] - mn[0]) / 2.0
    ry = (mx[1] - mn[1]) / 2.0
    pts: List[Point] = []
    for step in range(steps + 1):
        theta = 2.0 * math.pi * step / steps
        pts.append([cx + rx * math.cos(theta), cy + ry * math.sin(theta)])
    return pts


def arrow_polyline(start_xy: Sequence[float], end_xy: Sequence[float]) -> List[Point]:
    """Return a polyline for an arrow: shaft plus a two-barb arrowhead.

    The result is ``[start, end, left_barb, end, right_barb]`` so a stroke
    renderer draws the shaft and both arrowhead edges in one path. When the
    shaft has near-zero length only ``[start, end]`` is returned.

    :param start_xy: ``[x, y]`` tail of the arrow (OTIO-norm).
    :param end_xy: ``[x, y]`` head of the arrow (OTIO-norm).
    :returns: Polyline points for the arrow.
    """
    start_val, end_val = list(start_xy), list(end_xy)
    pts: List[Point] = [start_val, end_val]
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
        pts.extend([[lx, ly], end_val, [rx_val, ry_val]])
    return pts
