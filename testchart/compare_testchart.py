#!/usr/bin/env python3
"""
compare_testchart.py  –  Measure annotation registration against the test chart.

Usage
-----
    python compare_testchart.py <rendered.png> [--chart landscape|portrait]

<rendered.png> is the frame exported from RV with the annotations baked in.
The script samples perpendicular cross-sections along each reference line,
finds the centroid of the annotation colour in each cross-section, and reports
the lateral offset from the expected centre in pixels.

A perfect result is 0 px offset on every line.  Typical acceptable tolerance
is ±1-2 px (sub-pixel accuracy after floating-point round-trip).

How it works
------------
Each reference line has a known colour.  Along the line we take N evenly-spaced
sample points and at each point we extract a short perpendicular profile
(±HALF_WIDTH pixels).  We weight the profile by the target colour channel and
compute the centroid.  The centroid offset from the midpoint of the profile is
the registration error at that sample.

We report mean, std-dev, and max absolute error for each line, plus an overall
pass/fail at a configurable pixel threshold.
"""

import sys
import os
import argparse
import math
import numpy as np
from PIL import Image

# ── Reference line definitions ────────────────────────────────────────────────
# Each entry: (label, (x0,y0), (x1,y1), channel_weights)
# channel_weights are [R, G, B] weights used to score the annotation colour.

LANDSCAPE_LINES = [
    ("RED diagonal",       (200, 200), (1720, 880), [1.0, -0.3, -0.3]),
    ("BLUE horizontal",    (100, 360), (1820, 360), [-0.3, -0.1, 1.0]),
    ("GREEN vertical",    (1440, 100), (1440, 980), [-0.3, 1.0, -0.3]),
    ("YELLOW anti-diag",  (1720, 200),  (200, 880), [0.7, 0.7, -0.5]),
]

PORTRAIT_LINES = [
    ("CYAN horizontal",    (60,  480), (1020,  480), [-0.3, 0.7, 0.7]),
    ("MAGENTA vertical",  (360,  100),  (360, 1820), [0.7, -0.3, 0.7]),
    ("ORANGE diagonal",   (100,  200),  (980, 1720), [0.8, 0.3, -0.4]),
    ("WHITE anti-diag",   (980,  200),  (100, 1720), [0.5, 0.5, 0.5]),
]

PASS_THRESHOLD_PX = 3.0   # offsets below this are considered passing
HALF_WIDTH        = 20    # pixels either side of line to sample
N_SAMPLES         = 30    # cross-sections per line


# ── Geometry helpers ──────────────────────────────────────────────────────────

def unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 0 else (0.0, 0.0)


def perp(v):
    """90-degree clockwise rotation."""
    return (v[1], -v[0])


def lerp(a, b, t):
    return a + (b - a) * t


# ── Per-line analysis ─────────────────────────────────────────────────────────

def analyse_line(img_arr, p0, p1, weights, n_samples=N_SAMPLES, half_width=HALF_WIDTH):
    """
    Return an array of signed lateral offsets (px) at n_samples points along
    the line from p0 to p1.

    Positive = offset toward the perpendicular direction (clockwise from line).
    """
    x0, y0 = p0
    x1, y1 = p1
    h, w = img_arr.shape[:2]

    along = unit((x1 - x0, y1 - y0))
    across = perp(along)           # unit vector perpendicular to line

    weights = np.array(weights, dtype=float)

    offsets = []
    for i in range(n_samples):
        t = (i + 0.5) / n_samples
        cx = lerp(x0, x1, t)
        cy = lerp(y0, y1, t)

        # Build pixel positions for the cross-section profile
        positions = []
        for d in range(-half_width, half_width + 1):
            px = cx + d * across[0]
            py = cy + d * across[1]
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rendered", help="Rendered PNG from RV with annotations baked in")
    parser.add_argument("--chart", choices=["landscape", "portrait"], default=None,
                        help="Which chart to compare against (auto-detected from image size if omitted)")
    parser.add_argument("--threshold", type=float, default=PASS_THRESHOLD_PX,
                        help=f"Pass/fail threshold in pixels (default {PASS_THRESHOLD_PX})")
    args = parser.parse_args()

    if not os.path.exists(args.rendered):
        print(f"ERROR: file not found: {args.rendered}")
        sys.exit(1)

    img = Image.open(args.rendered).convert("RGB")

    # Auto-crop letterbox/pillarbox.
    # Use the corner pixel as the letterbox colour (works for black or white bars).
    arr_full = np.array(img)
    corner = arr_full[0, 0].astype(int)
    diff = np.max(np.abs(arr_full.astype(int) - corner), axis=2)
    mask = diff > 20          # pixels that differ from the letterbox colour
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if rows.any() and cols.any():
        r0, r1 = np.where(rows)[0][[0, -1]]
        c0, c1 = np.where(cols)[0][[0, -1]]
        cropped = arr_full[r0:r1+1, c0:c1+1]
        if cropped.shape[:2] != arr_full.shape[:2]:
            print(f"Auto-cropped letterbox: {arr_full.shape[1]}×{arr_full.shape[0]}"
                  f" → {cropped.shape[1]}×{cropped.shape[0]} (offset {c0},{r0})")
        img_arr = cropped
    else:
        img_arr = arr_full

    h, w = img_arr.shape[:2]

    if args.chart:
        chart = args.chart
    elif w > h:
        chart = "landscape"
    else:
        chart = "portrait"

    lines = LANDSCAPE_LINES if chart == "landscape" else PORTRAIT_LINES
    expected_size = (1920, 1080) if chart == "landscape" else (1080, 1920)

    print(f"\nTest chart : {chart}  ({w}×{h})")
    print(f"Expected   : {expected_size[0]}×{expected_size[1]}")

    if (w, h) != expected_size:
        sx = w / expected_size[0]
        sy = h / expected_size[1]
        print(f"  ⚠  Size mismatch — scaling reference coords by ({sx:.3f}, {sy:.3f})")
    else:
        sx, sy = 1.0, 1.0

    print(f"\nThreshold  : ±{args.threshold:.1f} px\n")
    print(f"{'Line':<22}  {'Mean':>7}  {'Std':>7}  {'Max':>7}  {'Result'}")
    print("─" * 60)

    all_pass = True
    for label, p0, p1 in [(l[0], l[1], l[2]) for l in lines]:
        weights = next(l[3] for l in lines if l[0] == label)
        p0s = (p0[0] * sx, p0[1] * sy)
        p1s = (p1[0] * sx, p1[1] * sy)

        offsets = analyse_line(img_arr, p0s, p1s, weights)
        # Reject outliers beyond 2 std-devs (cross-stroke interference)
        med = np.median(offsets)
        sd  = np.std(offsets)
        clean = offsets[np.abs(offsets - med) <= 2 * sd] if sd > 0 else offsets
        mean_off = float(np.mean(clean))
        std_off  = float(np.std(clean))
        max_off  = float(np.max(np.abs(clean)))

        passed = max_off <= args.threshold
        if not passed:
            all_pass = False
        status = "PASS" if passed else "FAIL"

        print(f"{label:<22}  {mean_off:>+7.2f}  {std_off:>7.2f}  {max_off:>7.2f}  {status}")

    print("─" * 60)
    print("Overall:", "PASS ✓" if all_pass else "FAIL ✗")
    print()
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
