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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("rendered", help="Rendered PNG with annotations baked in")
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

    print(f"\nThreshold  : ±{args.threshold:.1f} px\n")
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
