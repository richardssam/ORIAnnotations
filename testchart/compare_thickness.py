#!/usr/bin/env python3
import sys
import os
import math
import numpy as np
from PIL import Image

def get_profile_stats(profile):
    """
    Given a list of (d, score), compute:
      - sum of scores
      - centroid (mean position)
      - standard deviation
      - width (3.464 * std)
    """
    total = sum(s for _, s in profile)
    if total < 1e-5:
        return None, None
    centroid = sum(d * s for d, s in profile) / total
    variance = sum(((d - centroid) ** 2) * s for d, s in profile) / total
    std = math.sqrt(variance)
    width = 3.464 * std
    return centroid, width

def main():
    if len(sys.argv) < 3:
        print("Usage: python testchart/compare_thickness.py <background_image> <composite_image>")
        sys.exit(1)

    bg_path = sys.argv[1]
    comp_path = sys.argv[2]

    for path in (bg_path, comp_path):
        if not os.path.exists(path):
            print(f"ERROR: File not found: {path}")
            sys.exit(1)

    bg_img = Image.open(bg_path).convert("RGB")
    comp_img = Image.open(comp_path).convert("RGB")

    bg_arr = np.array(bg_img)
    comp_arr = np.array(comp_img)

    # Resolution scaling factors
    bg_h, bg_w = bg_arr.shape[:2]
    comp_h, comp_w = comp_arr.shape[:2]

    # Use comp resolution as the coordinate frame and scale bg if needed
    scale_x = comp_w / 1920.0
    scale_y = comp_h / 1080.0
    scale_bg_x = comp_w / bg_w
    scale_bg_y = comp_h / bg_h

    # Define the lines on the reference 1920x1080 canvas
    solid_lines = []
    thicknesses = [1, 2, 4, 8, 12, 16, 24, 32]
    y_pos = [200, 290, 380, 470, 560, 650, 740, 830]
    for th, y in zip(thicknesses, y_pos):
        solid_lines.append((f"Solid {th}px", (100, y), (500, y), th))

    gaussian_lines = []
    for th, y in zip(thicknesses, y_pos):
        gaussian_lines.append((f"Soft {th}px", (700, y), (1100, y), th))

    tapered_lines = []
    taper_y = [220, 370, 520, 670, 820]
    taper_th = [4, 8, 16, 24, 32]
    for th, y in zip(taper_th, taper_y):
        tapered_lines.append((f"Taper {th}px", (1350, y), (1750, y), th))

    all_lines = solid_lines + gaussian_lines + tapered_lines

    print(f"\n===========================================================================================")
    print(f"Line Thickness & Profile Comparison")
    print(f"  Background: {bg_path} ({bg_w}x{bg_h})")
    print(f"  Composite:  {comp_path} ({comp_w}x{comp_h})")
    print(f"===========================================================================================\n")

    t_vals = [0.1, 0.3, 0.5, 0.7, 0.9]

    # Print header
    header_fmt = "{:<12} | " + " | ".join(["Pos {:.1f}".format(t) for t in t_vals]) + " | {:<10} | {:<10}"
    sub_fmt = "{:<12} | " + " | ".join(["{:^14}".format("Off / Scale") for _ in t_vals]) + " | {:<10} | {:<10}"
    
    print(header_fmt.format("Line", *["" for _ in t_vals], "Avg Offset", "Avg Scale"))
    print(sub_fmt.format("", *["" for _ in t_vals], "(pixels)", "Factor"))
    print("-" * 115)

    for name, p0, p1, expected_th in all_lines:
        offsets = []
        scale_factors = []
        pos_strings = []

        # Scale endpoints to composite coordinate space
        p0_c = (p0[0] * scale_x, p0[1] * scale_y)
        p1_c = (p1[0] * scale_x, p1[1] * scale_y)

        # Scale endpoints to background coordinate space
        p0_bg = (p0[0] * scale_x / scale_bg_x, p0[1] * scale_y / scale_bg_y)
        p1_bg = (p1[0] * scale_x / scale_bg_x, p1[1] * scale_y / scale_bg_y)

        for t in t_vals:
            # Composite point
            cx_c = p0_c[0] + (p1_c[0] - p0_c[0]) * t
            cy_c = p0_c[1] + (p1_c[1] - p0_c[1]) * t

            # Background point
            cx_bg = p0_bg[0] + (p1_bg[0] - p0_bg[0]) * t
            cy_bg = p0_bg[1] + (p1_bg[1] - p0_bg[1]) * t

            # Sample vertical profiles
            half_w = 40
            bg_profile = []
            comp_profile = []

            for d in range(-half_w, half_w + 1):
                # Background image (green lines)
                by = int(round(cy_bg + d))
                bx = int(round(cx_bg))
                if 0 <= bx < bg_w and 0 <= by < bg_h:
                    pix = bg_arr[by, bx, :3] / 255.0
                    # Green score: G - R
                    score_g = max(0.0, pix[1] - pix[0])
                    bg_profile.append((d, score_g))

                # Composite image (red lines)
                cy = int(round(cy_c + d))
                cx = int(round(cx_c))
                if 0 <= cx < comp_w and 0 <= cy < comp_h:
                    pix = comp_arr[cy, cx, :3] / 255.0
                    # Red score: R - G
                    score_r = max(0.0, pix[0] - pix[1])
                    comp_profile.append((d, score_r))

            centroid_bg, width_bg = get_profile_stats(bg_profile)
            centroid_comp, width_comp = get_profile_stats(comp_profile)

            if centroid_bg is not None and centroid_comp is not None:
                # We need to scale background stats if sizes differ
                centroid_bg_scaled = centroid_bg * scale_bg_y
                width_bg_scaled = width_bg * scale_bg_y

                offset = centroid_comp - centroid_bg_scaled
                scale_factor = width_comp / width_bg_scaled if width_bg_scaled > 0.0 else 0.0

                offsets.append(offset)
                scale_factors.append(scale_factor)
                pos_strings.append(f"{offset:+.2f}/{scale_factor:.2f}")
            else:
                pos_strings.append("    N/A      ")

        if offsets:
            avg_offset = np.mean(offsets)
            avg_scale = np.mean(scale_factors)
            avg_offset_str = f"{avg_offset:+.2f} px"
            avg_scale_str = f"{avg_scale:.2f}"
        else:
            avg_offset_str = "N/A"
            avg_scale_str = "N/A"

        row_fmt = "{:<12} | " + " | ".join(["{:<14}" for _ in t_vals]) + " | {:<10} | {:<10}"
        print(row_fmt.format(name, *pos_strings, avg_offset_str, avg_scale_str))

    print("-" * 115)
    print()

if __name__ == "__main__":
    main()
