"""Ground-truth-driven visual verification of a captured live-app frame.

Given a frame captured via the `capture_frame` script-driven action (see
`openrv_hook.py`/`xstudio_hook.py`) and the same OTIO-normalized geometry the
test told the driver app to draw (`draw_annotation`'s payload), project that
geometry into the captured image's own actual pixel resolution and measure
whether annotation-colored ink appears where/how thick expected.

This is the same cross-section/weighted-centroid technique
`testchart/compare_testchart.py::analyse_arch` uses for its hardcoded
reference-chart arches, generalized here to an arbitrary straight line segment
(a rect/ellipse edge or an arrow shaft, none of which are arc-shaped) and
driven by a test's own known ground truth instead of a fixed chart — see the
`sync-test-frame-capture` change design doc, decision D4.

Deliberately imports PIL/numpy at module scope rather than guarding them:
callers (`runner.py`) are expected to catch `ImportError` and treat a missing
PIL/numpy as "skip this optional check", not a hard failure — see design
Risk: PIL/numpy availability.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple

import numpy as np
from PIL import Image

from otio_sync_core import coords

#: Cross-section sampling defaults, matching `compare_testchart.analyse_arch`.
N_SAMPLES = 20
HALF_WIDTH = 20


#: Default colour-match tolerance (Euclidean distance in normalized 0..1 RGB).
#: A pixel scores 1.0 at zero distance, 0.0 at this distance or beyond.
COLOR_TOLERANCE = 0.35


def analyse_line_segment(
    img_arr: np.ndarray,
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    target_color: Sequence[float],
    n_samples: int = N_SAMPLES,
    half_width: int = HALF_WIDTH,
    t_start: float = 0.15,
    t_end: float = 0.85,
    tolerance: float = COLOR_TOLERANCE,
) -> list:
    """Straight-line-segment analogue of `compare_testchart.analyse_arch`.

    Samples perpendicular cross-sections along the segment `p0` -> `p1`
    (pixel coordinates), scores each pixel by how close it is to
    `target_color` (`score = max(0, 1 - distance/tolerance)` — 1.0 at an exact
    match, decaying to 0 by `tolerance` away in normalized RGB distance, so it
    is maximal at the annotation's own known colour regardless of how
    saturated/primary that colour is, unlike a hand-picked per-hue dot-product
    weight vector), and for each cross-section with any matching-colour ink
    returns `(centroid_offset, width)`: the signed offset (px) of the colour
    centroid from the segment's own centerline, and the Gaussian-equivalent
    full width (`3.464 * std`, the same convention
    `testchart/compare_thickness.py::get_profile_stats` uses).

    `t_start`/`t_end` restrict sampling to the middle portion of the segment
    (default 15%-85%) to avoid corner interference for closed shapes; callers
    measuring a curved edge (e.g. an ellipse's bounding-box tangent) should
    narrow this further toward 0.5 to limit curvature bias.
    """
    h, w = img_arr.shape[:2]
    color_arr = np.array(target_color[:3], dtype=float)

    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return []

    ux, uy = dx / length, dy / length
    # Perpendicular (normal) direction to sample cross-sections across.
    nx_, ny_ = -uy, ux

    results = []
    for i in range(n_samples):
        t = t_start + (t_end - t_start) * (i / max(n_samples - 1, 1))
        ax = x0 + dx * t
        ay = y0 + dy * t

        profile = []
        for d in range(-half_width, half_width + 1):
            px = ax + d * nx_
            py = ay + d * ny_
            ipx, ipy = int(round(px)), int(round(py))
            if 0 <= ipx < w and 0 <= ipy < h:
                pixel = img_arr[ipy, ipx, :3].astype(float) / 255.0
                dist = float(np.linalg.norm(pixel - color_arr))
                score = max(0.0, 1.0 - dist / tolerance)
                profile.append((d, score))

        total = sum(s for _, s in profile)
        if total < 1e-6:
            continue
        centroid = sum(d * s for d, s in profile) / total
        variance = sum(((d - centroid) ** 2) * s for d, s in profile) / total
        width = 3.464 * math.sqrt(variance)
        results.append((centroid, width))

    return results


def _edges_for_kind(kind: str, geometry: dict, w: int, h: int) -> Iterable[Tuple[str, tuple, tuple, float, float]]:
    """Yield `(label, p0_px, p1_px, t_start, t_end)` straight segments to sample for `kind`."""
    if kind in ("rect", "ellipse"):
        mn = geometry["min"]
        mx = geometry["max"]
        p_min = coords.otio_to_px(mn[0], mn[1], w, h)
        p_max = coords.otio_to_px(mx[0], mx[1], w, h)
        # Rect edges are straight for their full span; an ellipse's bounding-box
        # tangent is only horizontal/vertical exactly at the apex, so narrow the
        # sampled range toward the midpoint to limit curvature bias (non-goal:
        # this is not a full elliptical-arc trace, just a boundary presence/
        # thickness check near the known extents).
        t_range = (0.15, 0.85) if kind == "rect" else (0.45, 0.55)
        yield ("top", (p_min[0], p_min[1]), (p_max[0], p_min[1]), *t_range)
        yield ("bottom", (p_min[0], p_max[1]), (p_max[0], p_max[1]), *t_range)
    elif kind == "arrow":
        s = geometry["start"]
        e = geometry["end"]
        p_s = coords.otio_to_px(s[0], s[1], w, h)
        p_e = coords.otio_to_px(e[0], e[1], w, h)
        # The arrowhead is a wide triangle flared out near the `end` point —
        # much wider than the plain shaft — so sampling anywhere close to it
        # inflates the measured width (verified empirically: mean measured
        # width dropped from ~42px to ~34.5px, matching the expected value,
        # as t_end was narrowed from 0.85 down to 0.5). Stay well clear of it.
        yield ("shaft", p_s, p_e, 0.15, 0.5)
    elif kind == "pen":
        x0, y0, x1, y1 = geometry["points"]
        p0 = coords.otio_to_px(x0, y0, w, h)
        p1 = coords.otio_to_px(x1, y1, w, h)
        # A pen stroke has no arrowhead, but round end caps can be slightly
        # wider than the shaft — stay clear of both ends the same way the
        # rect/arrow edges do.
        yield ("stroke", p0, p1, 0.15, 0.85)
    else:
        raise ValueError(f"visual_geometry: unsupported kind {kind!r} (rect/ellipse/arrow/pen only)")


def measure_shape_border(
    image_path: str,
    kind: str,
    geometry: dict,
    color: Sequence[float],
    otio_thickness: float,
    tolerance: float = COLOR_TOLERANCE,
) -> dict:
    """Measure a captured shape annotation's rendered border/line thickness.

    Reads `image_path`'s actual pixel dimensions, projects the known
    OTIO-normalized `geometry` (`min`/`max` for rect/ellipse, `start`/`end` for
    arrow) into that image's pixel space via `coords.otio_to_px`, and samples a
    perpendicular cross-section at the expected boundary location(s) to locate
    the annotation-colored centroid and thickness — never assumes a fixed
    resolution (design D2/spec scenario "does not assume a fixed capture
    resolution").

    :param image_path: Path to the captured frame PNG.
    :param kind: `"rect"`, `"ellipse"`, or `"arrow"`.
    :param geometry: OTIO-normalized ground truth dict for `kind` (see
        `annotation_assertions.DEFAULT_SHAPE_GEOMETRY`).
    :param color: `[R, G, B, ...]` the annotation was drawn with (0..1), from
        the test's own `draw_annotation` payload.
    :param otio_thickness: Expected OTIO-normalized (H-normalized) border/line
        thickness — e.g. from `annotation_assertions.otio_size_from_driver_nominal`.
    :param tolerance: Colour-match distance tolerance, see `analyse_line_segment`.
    :returns: dict with `found`, `expected_thickness_px`, `measured_thickness_px`,
        `offset_px`, `centroid_offset_px`, `image_size`.
    """
    img = Image.open(image_path).convert("RGB")
    img_arr = np.array(img)
    h, w = img_arr.shape[:2]
    expected_thickness_px = otio_thickness * h

    # The sampled cross-section is centered on the geometry's own boundary
    # line (`min`/`max`), but a host's border can be drawn centered on that
    # line, entirely inside it, or entirely outside it — not knowable in
    # advance. `half_width` must comfortably contain the border regardless of
    # which side it grows toward, so it scales with `expected_thickness_px`
    # (with `HALF_WIDTH` kept as extra margin) rather than staying fixed: a
    # fixed half_width was verified to silently truncate a thick,
    # entirely-inward-growing border and under-report its measured thickness.
    half_width = max(HALF_WIDTH, int(math.ceil(expected_thickness_px)) + HALF_WIDTH)

    measurements = []
    for _label, p0, p1, t_start, t_end in _edges_for_kind(kind, geometry, w, h):
        measurements.extend(
            analyse_line_segment(
                img_arr, p0, p1, color, half_width=half_width,
                t_start=t_start, t_end=t_end, tolerance=tolerance,
            )
        )

    if not measurements:
        return {
            "found": False,
            "expected_thickness_px": expected_thickness_px,
            "measured_thickness_px": None,
            "offset_px": None,
            "centroid_offset_px": None,
            "image_size": (w, h),
        }

    centroids = [c for c, _ in measurements]
    widths = [wd for _, wd in measurements]
    measured_thickness_px = float(np.mean(widths))
    centroid_offset_px = float(np.mean(np.abs(centroids)))

    return {
        "found": True,
        "expected_thickness_px": expected_thickness_px,
        "measured_thickness_px": measured_thickness_px,
        "offset_px": measured_thickness_px - expected_thickness_px,
        "centroid_offset_px": centroid_offset_px,
        "image_size": (w, h),
    }
