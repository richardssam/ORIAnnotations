# SPDX-License-Identifier: Apache-2.0
"""Core annotation event creation library for OTIO sessions."""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

import opentimelineio as otio

from otio_sync_core import coords

# Lazily resolved — requires OTIO_PLUGIN_MANIFEST_PATH to be populated first.
_SyncEvent = None


def _se() -> Any:
    """Return the SyncEvent schemadef module, resolving it on first call."""
    global _SyncEvent
    if _SyncEvent is None:
        _SyncEvent = otio.schema.schemadef.module_from_name("SyncEvent")
    return _SyncEvent


def px_to_norm(px: float, py: float, width: float, height: float) -> tuple[float, float]:
    """Convert pixel coordinates to H-normalised coordinates (RV paint / OTIO SyncEvent)."""
    return coords.px_to_otio(px, py, width, height)


def norm_to_px(nx: float, ny: float, width: float, height: float) -> tuple[float, float]:
    """Convert H-normalised coordinates (RV paint / OTIO SyncEvent) to pixels."""
    return coords.otio_to_px(nx, ny, width, height)


def line_pts(x0: float, y0: float, x1: float, y1: float, n: int = 24) -> list[tuple[float, float]]:
    """Return *n* evenly-spaced pixel points along the segment."""
    if n <= 1:
        return [(x0, y0)]
    return [
        (x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1))
        for i in range(n)
    ]


def pressure_sizes(base_size: float, n: int, variation: float = 1.6) -> list[float]:
    """Sizes that swell from thin → thick → thin (simulates pen pressure).

    Normalised so the peak value is exactly base_size.
    """
    if n <= 1:
        return [base_size]
    peak = 0.5 + variation
    return [
        base_size * (0.5 + variation * math.sin(math.pi * i / (n - 1))) / peak
        for i in range(n)
    ]


def bezier_curve(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float], n: int = 50) -> list[tuple[float, float]]:
    """Generate *n* points along a cubic Bezier curve."""
    pts = []
    for i in range(n):
        t = i / max(1, n - 1)
        x = (1-t)**3 * p0[0] + 3*(1-t)**2 * t * p1[0] + 3*(1-t) * t**2 * p2[0] + t**3 * p3[0]
        y = (1-t)**3 * p0[1] + 3*(1-t)**2 * t * p1[1] + 3*(1-t) * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def ts() -> str:
    """Return current ISO 8601 timestamp string."""
    return datetime.now().isoformat()


def make_stroke(
    points_px: list[tuple[float, float]],
    width: float,
    height: float,
    rgba: list[float],
    brush_size: float,
    brush: str = "circle",
    varying_pressure: bool = False,
) -> list[Any]:
    """Build [PaintStart, PaintPoint, PaintEnd] SyncEvent objects for one stroke.

    :param points_px: list of (pixel_x, pixel_y) coordinates.
    :param width: Image width in pixels.
    :param height: Image height in pixels.
    :param rgba: [r, g, b, a] color vector (0-1 floats).
    :param brush_size: Normalised brush radius (fraction of height).
    :param brush: Brush profile type (e.g. "circle" or "gaussian").
    :param varying_pressure: If True, varies thickness along the stroke.
    :returns: List of SyncEvent objects.
    """
    se = _se()
    stroke_id = str(uuid.uuid4())
    n = len(points_px)

    xs, ys, sizes = [], [], []
    pressure = pressure_sizes(brush_size, n) if varying_pressure else [brush_size] * n

    for i, (px, py) in enumerate(points_px):
        nx, ny = px_to_norm(px, py, width, height)
        xs.append(nx)
        ys.append(ny)
        sizes.append(float(pressure[i]))

    # PaintStart
    start = se.PaintStart()
    start.brush = brush
    start.friendly_name = "annotation_builder"
    start.rgba = [float(c) for c in rgba]
    start.source_index = 0
    start.timestamp = ts()
    start.type = "color"
    start.uuid = stroke_id
    start.visible = True

    # PaintPoints (PaintPoint schema in SyncEvent)
    pts = se.PaintPoints()
    pts.source_index = 0
    pts.uuid = stroke_id
    pts.timestamp = ts()
    pts.points = se.PaintVertices(xs, ys, sizes)

    # PaintEnd
    end = se.PaintEnd()
    end.uuid = stroke_id
    end.timestamp = ts()

    return [start, pts, end]


def make_text(
    px: float,
    py: float,
    width: float,
    height: float,
    text: str,
    rgba: list[float],
    font_size: float = 50.0,
) -> list[Any]:
    """Build a single TextAnnotation event.

    :param px: Center pixel x coordinate.
    :param py: Center pixel y coordinate.
    :param width: Image width in pixels.
    :param height: Image height in pixels.
    :param text: Text string.
    :param rgba: [r, g, b, a] color vector (0-1 floats).
    :param font_size: Normalised font size (height scale or points scale).
    :returns: List containing a single TextAnnotation event.
    """
    se = _se()
    nx, ny = px_to_norm(px, py, width, height)

    text_evt = se.TextAnnotation()
    text_evt.uuid = str(uuid.uuid4())
    text_evt.rgba = [float(c) for c in rgba]
    text_evt.friendly_name = "annotation_builder"
    text_evt.text = text
    text_evt.position = [nx, ny]
    text_evt.font_size = float(font_size)
    text_evt.scale = 1.0
    text_evt.rotation = 0.0
    text_evt.spacing = 1.0
    text_evt.font = "monospace"
    text_evt.timestamp = ts()

    return [text_evt]
