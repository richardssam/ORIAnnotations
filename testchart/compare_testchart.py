#!/usr/bin/env python3
"""
compare_testchart.py  –  Measure annotation registration against the test chart.

Usage
-----
    python compare_testchart.py <rendered.png> [--mode colors|text]

<rendered.png> is the frame exported from RV or xStudio with the annotations
baked in. In "colors" mode (default, the vector_colors.png chart) the script
samples perpendicular cross-sections along each reference arch, finds the
centroid of the annotation colour in each cross-section, and reports the
lateral offset from the expected centre in pixels.

In "text" mode (the vector_fonts.png chart) the script samples a small window
around each known text-annotation anchor position, finds the centroid of the
annotation-colour pixels in that window, and reports the offset from the
expected anchor in pixels — a coarser check than "colors" mode, since font
rendering (antialiasing, hinting) varies across platforms.

A perfect result is 0 px offset. Typical acceptable tolerance is ±1-2 px for
colors mode (sub-pixel accuracy after floating-point round-trip) and ±5 px for
text mode (anchor position only, not exact glyph shape).
"""

import sys
import os
import argparse
import math
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "python"))
from otio_sync_core import coords

# ── Reference arch definitions ────────────────────────────────────────────────
# Each entry: (label, radius, channel_weights)
# channel_weights are [R, G, B] weights used to score the annotation colour.

COLOR_ARCHES = [
    ("RED arch",       150, [1.0, -0.3, -0.3]),
    ("GREEN arch",     200, [-0.3, 1.0, -0.3]),
    ("BLUE arch",      250, [-0.3, -0.1, 1.0]),
    ("YELLOW arch",    300, [0.7, 0.7, -0.5]),
    ("CYAN arch",      350, [-0.3, 0.7, 0.7]),
    ("MAGENTA arch",   400, [0.7, -0.3, 0.7]),
    ("ORANGE arch",    450, [0.8, 0.3, -0.4]),
    ("GREY arch",      500, [0.3, 0.3, 0.3]),
]

PASS_THRESHOLD_PX = 3.0   # offsets below this are considered passing
HALF_WIDTH        = 20    # pixels either side of line to sample
N_SAMPLES         = 40    # cross-sections per arch

# ── Text-label reference definitions (vector_fonts.png chart) ─────────────────
# Ground truth mirrors generate_testchart.py::vector_fonts_annotations(), which
# builds each TextAnnotation via make_text(px, py, W, H, ...) — storing the
# OTIO-normalized position via coords.px_to_otio(px, py, W, H). Expressing the
# ground truth the same way (OTIO-norm, not raw pixels) and resolving it back
# via coords.otio_to_px(nx, ny, w, h) for the image under test means UHD (2x)
# renders need no separate scale factor — H-normalized coordinates are
# resolution-independent by construction.
TEXT_CHART_SIZE = (1920, 1080)
_TEXT_LABEL_PX = [
    # (label, px, py, font_px) — as authored in generate_testchart.py
    ("12pt sample",  100, 160,   12),
    ("16pt sample",  100, 220,   16),
    ("24pt sample",  100, 300,   24),
    ("32pt sample",  100, 400,   32),
    ("48pt sample",  100, 550,   48),
    ("72pt sample",  100, 750,   72),
    ("96pt sample",  100, 1000,  96),
]
TEXT_LABELS = [
    (label, *coords.px_to_otio(px, py, *TEXT_CHART_SIZE), font_px)
    for label, px, py, font_px in _TEXT_LABEL_PX
]

# The vector_fonts.png background is near-white/neutral (245, 245, 240), not
# dark like the colour-arch charts, so weights must sum to ~0 (a true
# "redness" differential: R - (G+B)/2) rather than RED_ARCH's [1.0,-0.3,-0.3]
# (sums to +0.4) — that combination scores ANY bright neutral pixel positively,
# so the background itself would register as a false "text found" match.
TEXT_LABEL_WEIGHTS = [1.0, -0.5, -0.5]
TEXT_PASS_THRESHOLD_PX = 5.0
TEXT_RIGHT_MARGIN = 300   # generous rightward run to catch the first few glyphs
TEXT_X_TOLERANCE = 15     # scan starts slightly left of anchor in case of small overshoot


# ── Per-arch analysis ─────────────────────────────────────────────────────────

def analyse_arch(img_arr, center, r, weights, n_samples=N_SAMPLES, half_width=HALF_WIDTH):
    """
    Return an array of signed radial offsets (px) at n_samples points along
    the arch centered at center with radius r.

    Positive = offset outward (away from center).
    """
    cx, cy = center
    h, w = img_arr.shape[:2]

    weights = np.array(weights, dtype=float)

    offsets = []
    for i in range(n_samples):
        # Sample angles from 10% to 90% of the semi-circle to avoid text / borders
        t = 0.1 + 0.8 * (i / max(n_samples - 1, 1))
        theta = math.pi * t
        
        # Base point on the arch
        ax = cx + r * math.cos(theta)
        ay = cy - r * math.sin(theta)
        
        # Radial unit vector (outward from center)
        rx = math.cos(theta)
        ry = -math.sin(theta)

        # Build pixel positions for the cross-section profile
        positions = []
        for d in range(-half_width, half_width + 1):
            px = ax + d * rx
            py = ay + d * ry
            ipx, ipy = int(round(px)), int(round(py))
            if 0 <= ipx < w and 0 <= ipy < h:
                positions.append((d, ipx, ipy))

        if len(positions) < 3:
            continue

        # Score each pixel by the weighted colour response
        scores = []
        for d, ipx, ipy in positions:
            pixel = img_arr[ipy, ipx, :3].astype(float) / 255.0
            score = float(np.dot(pixel, weights))
            scores.append((d, max(score, 0.0)))

        total = sum(s for _, s in scores)
        if total < 1e-6:
            continue

        centroid = sum(d * s for d, s in scores) / total
        offsets.append(centroid)

    return np.array(offsets) if offsets else np.array([0.0])


# ── Per-label text analysis ───────────────────────────────────────────────────

def analyse_text_label(img_arr, px, py, font_px, weights=TEXT_LABEL_WEIGHTS,
                        right_margin=TEXT_RIGHT_MARGIN, x_tolerance=TEXT_X_TOLERANCE):
    """Locate the left edge of the annotation-coloured text ink near the
    expected left-baseline anchor (px, py) and report its horizontal offset.

    Text is left-baseline anchored, so it extends *upward* (ascent) and
    *rightward* from (px, py) — a symmetric square window is the wrong shape,
    especially at large font sizes where the ascent alone can exceed 100px.
    Instead this scans a region sized from the known font pixel height
    (covering ascent above the baseline and a small descender allowance below)
    and a generous rightward run, then finds the leftmost column containing
    matching-colour ink — i.e. does the text actually *start* where expected.

    :param img_arr: Full-frame RGB pixel array.
    :param px: Expected left-baseline anchor pixel x.
    :param py: Expected left-baseline anchor pixel y.
    :param font_px: Font size in pixels, used to size the vertical scan band.
    :param weights: [R, G, B] scoring weights for the annotation colour.
    :param right_margin: How far right of the anchor to scan.
    :param x_tolerance: How far left of the anchor to scan (small overshoot).
    :returns: ``(offset_px, found)`` — horizontal distance from the expected
        anchor to the leftmost matching-colour column, and whether any
        matching-colour ink was found at all in the scanned region.
    """
    h, w = img_arr.shape[:2]
    weights_arr = np.array(weights, dtype=float)

    y0 = max(0, int(py - font_px * 1.1))
    y1 = min(h, int(py + font_px * 0.3))
    x0 = max(0, int(px - x_tolerance))
    x1 = min(w, int(px + right_margin))
    if y1 <= y0 or x1 <= x0:
        return 0.0, False

    region = img_arr[y0:y1, x0:x1, :3].astype(float) / 255.0
    scores = np.tensordot(region, weights_arr, axes=([2], [0]))
    scores = np.maximum(scores, 0.0)

    cols_with_ink = np.where(scores.max(axis=0) > 0.15)[0]
    if len(cols_with_ink) == 0:
        return 0.0, False

    leftmost_x = x0 + int(cols_with_ink[0])
    return float(abs(leftmost_x - px)), True


def _load_cropped(rendered_path):
    """Load *rendered_path*, auto-cropping letterbox/pillarbox bars."""
    img = Image.open(rendered_path).convert("RGB")
    arr_full = np.array(img)
    corner = arr_full[0, 0].astype(int)
    diff = np.max(np.abs(arr_full.astype(int) - corner), axis=2)
    mask = diff > 20
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if rows.any() and cols.any():
        r0, r1 = np.where(rows)[0][[0, -1]]
        c0, c1 = np.where(cols)[0][[0, -1]]
        cropped = arr_full[r0:r1+1, c0:c1+1]
        if cropped.shape[:2] != arr_full.shape[:2]:
            print(f"Auto-cropped letterbox: {arr_full.shape[1]}×{arr_full.shape[0]}"
                  f" → {cropped.shape[1]}×{cropped.shape[0]} (offset {c0},{r0})")
        return cropped
    return arr_full


def run_colors_mode(img_arr, threshold):
    """Colour-arch registration check (vector_colors.png). Returns True on overall PASS."""
    h, w = img_arr.shape[:2]
    expected_size = (1920, 1080)
    center_ref = (960.0, 800.0)

    print(f"\nTest chart : Color Curves  ({w}×{h})")
    print(f"Expected   : {expected_size[0]}×{expected_size[1]}")

    if (w, h) != expected_size:
        sx = w / expected_size[0]
        sy = h / expected_size[1]
        print(f"  ⚠  Size mismatch — scaling reference coords by ({sx:.3f}, {sy:.3f})")
    else:
        sx, sy = 1.0, 1.0

    center_scaled = (center_ref[0] * sx, center_ref[1] * sy)
    # Radial scaling factor uses height scale to ensure aspect ratio consistency
    sr = sy

    print(f"\nThreshold  : ±{threshold:.1f} px\n")
    print(f"{'Arch':<22}  {'Mean':>7}  {'Std':>7}  {'Max':>7}  {'Result'}")
    print("─" * 60)

    all_pass = True
    for label, r_ref, weights in COLOR_ARCHES:
        r_scaled = r_ref * sr

        offsets = analyse_arch(img_arr, center_scaled, r_scaled, weights)
        # Reject outliers beyond 2 std-devs (cross-stroke interference)
        med = np.median(offsets)
        sd  = np.std(offsets)
        clean = offsets[np.abs(offsets - med) <= 2 * sd] if sd > 0 else offsets
        mean_off = float(np.mean(clean))
        std_off  = float(np.std(clean))
        max_off  = float(np.max(np.abs(clean)))

        passed = max_off <= threshold
        if not passed:
            all_pass = False
        status = "PASS" if passed else "FAIL"

        print(f"{label:<22}  {mean_off:>+7.2f}  {std_off:>7.2f}  {max_off:>7.2f}  {status}")

    print("─" * 60)
    print("Overall:", "PASS ✓" if all_pass else "FAIL ✗")
    print()
    return all_pass


def run_text_mode(img_arr, threshold):
    """Text-annotation anchor check (vector_fonts.png). Returns True on overall PASS."""
    h, w = img_arr.shape[:2]
    print(f"\nTest chart : Font Alignment  ({w}×{h})")
    print(f"Expected   : {TEXT_CHART_SIZE[0]}×{TEXT_CHART_SIZE[1]} (or 2x UHD)")

    # font_px (and therefore a "5px offset") is defined at 1920x1080; UHD (2x)
    # renders the same relative error at twice the raw pixel count, so the
    # comparison threshold scales with resolution — same approach the colour
    # arches use (`sr` in run_colors_mode) rather than a fixed pixel count.
    scale = h / TEXT_CHART_SIZE[1]
    threshold_scaled = threshold * scale

    print(f"\nThreshold  : ±{threshold:.1f} px (±{threshold_scaled:.1f} px at this resolution)\n")
    print(f"{'Label':<22}  {'Offset':>7}  {'Result'}")
    print("─" * 45)

    all_pass = True
    for label, nx, ny, font_px in TEXT_LABELS:
        px, py = coords.otio_to_px(nx, ny, w, h)
        offset_px, found = analyse_text_label(img_arr, px, py, font_px * scale)

        passed = found and offset_px <= threshold_scaled
        if not passed:
            all_pass = False
        status = "PASS" if passed else ("FAIL (not found)" if not found else "FAIL")

        print(f"{label:<22}  {offset_px:>7.2f}  {status}")

    print("─" * 45)
    print("Overall:", "PASS ✓" if all_pass else "FAIL ✗")
    print()
    return all_pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rendered", help="Rendered PNG with annotations baked in")
    parser.add_argument("--mode", choices=["colors", "text"], default="colors",
                        help="Comparison mode: colour-arch registration or text-anchor position (default colors)")
    parser.add_argument("--threshold", type=float, default=None,
                        help=f"Pass/fail threshold in pixels (default {PASS_THRESHOLD_PX} for colors, "
                             f"{TEXT_PASS_THRESHOLD_PX} for text)")
    args = parser.parse_args()

    if not os.path.exists(args.rendered):
        print(f"ERROR: file not found: {args.rendered}")
        sys.exit(1)

    img_arr = _load_cropped(args.rendered)

    if args.mode == "text":
        threshold = args.threshold if args.threshold is not None else TEXT_PASS_THRESHOLD_PX
        all_pass = run_text_mode(img_arr, threshold)
    else:
        threshold = args.threshold if args.threshold is not None else PASS_THRESHOLD_PX
        all_pass = run_colors_mode(img_arr, threshold)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
