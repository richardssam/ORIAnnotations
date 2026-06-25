#!/usr/bin/env python3
"""
Generate testchart images and an OTIO annotation file for verifying
annotation coordinate alignment.

Two test images are created (landscape 1920×1080 and portrait 1080×1920),
each with coloured reference lines at precise pixel positions.  The OTIO
annotation file traces those same lines with brush strokes so that, when
overlaid, each stroke should sit exactly on top of its reference line.

Brush variety:
  Landscape
    RED   – thick circle,  full opacity,      straight diagonal
    BLUE  – medium gaussian, 70 % opacity,    horizontal
    GREEN – thin circle,   85 % opacity,      vertical
    YELLOW – medium circle, 100 % opacity,    varying pressure (anti-diagonal)

  Portrait
    CYAN    – medium gaussian, 80 % opacity,  horizontal
    MAGENTA – thin circle,    100 % opacity,  vertical
    ORANGE  – medium circle,  100 % opacity,  varying pressure (diagonal)
    WHITE   – medium circle,   90 % opacity,  anti-diagonal

Coordinate system
  RV normalises paint coordinates by image height.
  For a W×H image:  norm_x = (px - W/2) / H,  norm_y = -(py - H/2) / H
  Landscape (1920×1080): x ∈ [-0.5, 0.5],      y ∈ [-0.281, 0.281]
  Portrait  (1080×1920): x ∈ [-0.5, 0.5],      y ∈ [-0.889, 0.889]
"""

import sys, os, json, uuid, math
from datetime import datetime

# ─── OTIO plugin setup ────────────────────────────────────────────────────────

SCRIPT_DIR   = os.path.dirname(os.path.realpath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")

manifest_path = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
if manifest_path:
    manifest_path += os.pathsep
os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = manifest_path + os.path.join(
    PROJECT_ROOT, "otio_event_plugin", "plugin_manifest.json"
)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))

import opentimelineio as otio
import ORIAnnotations

from otio_sync_core.annotation_builder import (
    px_to_norm,
    line_pts,
    pressure_sizes,
    bezier_curve,
    make_stroke,
    make_text,
    ts,
)



# ─── Reference line coordinates ───────────────────────────────────────────────

LAND_W, LAND_H = 1920, 1080

# Landscape reference lines (pixel coords)
L_RED_P0    = (200,  200)
L_RED_P1    = (1720, 880)

L_BLUE_P0   = (100,  360)   # horizontal at y = 360  (⅓ from top)
L_BLUE_P1   = (1820, 360)

L_GREEN_P0  = (1440, 100)   # vertical   at x = 1440 (¾ from left)
L_GREEN_P1  = (1440, 980)

L_YELLOW_P0 = (1720, 200)   # anti-diagonal
L_YELLOW_P1 = (200,  880)

# Line Width Tests
L_WIDTH_P0 = (800, 1920/2)
ANNOTATION_WIDTH_P0 = (800, 1920/2 - 50)
CORNER_LINE_WIDTH = 60
LINE_WIDTH_LENGTH = -100
GAUSSIAN_OFFSET = -160

PORT_W, PORT_H = 1080, 1920

# Portrait reference lines (pixel coords)
P_CYAN_P0   = (60,   480)   # horizontal at y = 480 (¼ from top)
P_CYAN_P1   = (1020, 480)

P_MAG_P0    = (360,  100)   # vertical   at x = 360 (⅓ from left)
P_MAG_P1    = (360,  1820)

P_ORA_P0    = (100,  200)   # diagonal
P_ORA_P1    = (980,  1720)

P_WHT_P0    = (980,  200)   # anti-diagonal
P_WHT_P1    = (100,  1720)


# ─── Image generators ─────────────────────────────────────────────────────────

def _load_font(size):
    from PIL import ImageFont
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _draw_grid(draw, width, height, step_pct=10):
    sx = max(1, width  * step_pct // 100)
    sy = max(1, height * step_pct // 100)
    color = (48, 48, 68)
    for x in range(0, width + 1, sx):
        draw.line([(x, 0), (x, height)], fill=color, width=1)
    for y in range(0, height + 1, sy):
        draw.line([(0, y), (width, y)],  fill=color, width=1)


def _crosshair(draw, cx, cy, size=40, color=(255, 255, 255), lw=2):
    draw.line([(cx - size, cy), (cx + size, cy)], fill=color, width=lw)
    draw.line([(cx, cy - size), (cx, cy + size)], fill=color, width=lw)


def create_landscape_image(path, is_uhd=False):
    from PIL import Image, ImageDraw

    scale = 2 if is_uhd else 1
    W, H = LAND_W * scale, LAND_H * scale
    img  = Image.new("RGB", (W, H), (26, 26, 46))
    d    = ImageDraw.Draw(img)
    f  = _load_font(22 * scale)
    sf = _load_font(16 * scale)

    _draw_grid(d, W, H)

    # Reference lines – colours match annotation rgba values below
    d.line([(L_RED_P0[0]*scale, L_RED_P0[1]*scale), (L_RED_P1[0]*scale, L_RED_P1[1]*scale)], fill=(220,  60,  60), width=4*scale)
    d.line([(L_BLUE_P0[0]*scale, L_BLUE_P0[1]*scale), (L_BLUE_P1[0]*scale, L_BLUE_P1[1]*scale)], fill=( 60, 120, 220), width=3*scale)
    d.line([(L_GREEN_P0[0]*scale, L_GREEN_P0[1]*scale), (L_GREEN_P1[0]*scale, L_GREEN_P1[1]*scale)], fill=( 60, 200,  80), width=3*scale)
    d.line([(L_YELLOW_P0[0]*scale, L_YELLOW_P0[1]*scale), (L_YELLOW_P1[0]*scale, L_YELLOW_P1[1]*scale)], fill=(220, 200,  60), width=4*scale)

    # Draw line widths
    for i, width in enumerate((1, 2, 4, 8, 16, 32)):
        d.line([(L_WIDTH_P0[0]+i*40)*scale, L_WIDTH_P0[1]*scale, (L_WIDTH_P0[0]+i*40)*scale, (L_WIDTH_P0[1]+LINE_WIDTH_LENGTH)*scale], fill=(255, 255,  255), width=width*scale)

    # Draw corner box
    c_lw = CORNER_LINE_WIDTH * scale
    d.line([c_lw, c_lw, W - 1 - c_lw, c_lw, W - 1 - c_lw, H - 1 - c_lw, c_lw, H - 1 - c_lw, c_lw, c_lw], fill=(255, 255,  255), width=2*scale)

    d.text(((L_WIDTH_P0[0]+8*40)*scale, L_WIDTH_P0[1]*scale), "Brush Widths 1px - 64px",
           fill=(255, 255, 255), font=f)

    _crosshair(d, W // 2, H // 2, size=40*scale, lw=2*scale)

    title_text = "LANDSCAPE TEST CHART  3840×2160" if is_uhd else "LANDSCAPE TEST CHART  1920×1080"
    d.text((10*scale, 10*scale), title_text, fill=(255, 255, 255), font=f)

    # Line labels (offset slightly from the line so they remain readable)
    d.text((750*scale, 180*scale),  "RED – thick circle, full opacity",
           fill=(240,  80,  80), font=sf)
    d.text((110*scale, 370*scale),  "BLUE – gaussian, 70 % opacity",
           fill=( 80, 140, 220), font=sf)
    d.text((1452*scale, 490*scale), "GREEN\nvertical\nthin circle\n85 % opacity",
           fill=( 80, 210, 100), font=sf)
    d.text((750*scale, 750*scale),  "YELLOW – varying pressure, anti-diagonal",
           fill=(220, 210,  80), font=sf)
    d.text((860*scale, 520*scale),  "CENTER\n(0, 0)", fill=(180, 180, 180), font=sf)

    img.save(path)
    print(f"  Saved landscape image : {path}")


def create_portrait_image(path, is_uhd=False):
    from PIL import Image, ImageDraw

    scale = 2 if is_uhd else 1
    W, H = PORT_W * scale, PORT_H * scale
    img  = Image.new("RGB", (W, H), (20, 40, 20))
    d    = ImageDraw.Draw(img)

    _draw_grid(d, W, H)

    d.line([(P_CYAN_P0[0]*scale, P_CYAN_P0[1]*scale), (P_CYAN_P1[0]*scale, P_CYAN_P1[1]*scale)], fill=( 60, 210, 210), width=3*scale)
    d.line([(P_MAG_P0[0]*scale, P_MAG_P0[1]*scale), (P_MAG_P1[0]*scale, P_MAG_P1[1]*scale)], fill=(210,  60, 210), width=3*scale)
    d.line([(P_ORA_P0[0]*scale, P_ORA_P0[1]*scale), (P_ORA_P1[0]*scale, P_ORA_P1[1]*scale)], fill=(220, 120,  40), width=4*scale)
    d.line([(P_WHT_P0[0]*scale, P_WHT_P0[1]*scale), (P_WHT_P1[0]*scale, P_WHT_P1[1]*scale)], fill=(210, 210, 210), width=3*scale)

    _crosshair(d, W // 2, H // 2, size=40*scale, lw=2*scale)

    f  = _load_font(22 * scale)
    sf = _load_font(16 * scale)

    title_text = "PORTRAIT TEST CHART  2160×3840" if is_uhd else "PORTRAIT TEST CHART  1080×1920"
    d.text((10*scale, 10*scale), title_text, fill=(255, 255, 255), font=f)

    d.text((100*scale, 490*scale), "CYAN – gaussian, 80 % opacity",
           fill=( 80, 220, 220), font=sf)
    d.text((370*scale, 650*scale), "MAGENTA – thin circle",
           fill=(220,  80, 220), font=sf)
    d.text((340*scale, 1240*scale), "ORANGE – varying pressure",
           fill=(220, 130,  60), font=sf)
    d.text((100*scale, 1100*scale), "WHITE – medium circle, 90 % opacity",
           fill=(210, 210, 210), font=sf)
    d.text((478*scale, 945*scale), "CENTER\n(0, 0)", fill=(180, 180, 180), font=sf)

    # Draw corner box
    c_lw = CORNER_LINE_WIDTH * scale
    d.line([c_lw, c_lw, W - 1 - c_lw, c_lw, W - 1 - c_lw, H - 1 - c_lw, c_lw, H - 1 - c_lw, c_lw, c_lw], fill=(255, 255,  255), width=2*scale)

    img.save(path)
    print(f"  Saved portrait image  : {path}")


# ─── Annotation event sets ────────────────────────────────────────────────────

def landscape_annotations():
    W, H = LAND_W, LAND_H
    ev = []

    # RED diagonal – thick circle, full opacity
    ev += make_stroke(
        line_pts(*L_RED_P0, *L_RED_P1, 30), W, H,
        rgba=[1.0, 0.24, 0.24, 1.0], brush_size=0.014,
    )

    # BLUE horizontal – medium gaussian, 70 % opacity
    ev += make_stroke(
        line_pts(*L_BLUE_P0, *L_BLUE_P1, 40), W, H,
        rgba=[0.24, 0.47, 0.86, 0.7], brush_size=0.009, brush="gaussian",
    )

    # GREEN vertical – thin circle, 85 % opacity
    ev += make_stroke(
        line_pts(*L_GREEN_P0, *L_GREEN_P1, 20), W, H,
        rgba=[0.24, 0.78, 0.31, 0.85], brush_size=0.005,
    )

    # YELLOW anti-diagonal – medium circle, varying pressure
    ev += make_stroke(
        line_pts(*L_YELLOW_P0, *L_YELLOW_P1, 30), W, H,
        rgba=[0.86, 0.78, 0.24, 1.0], brush_size=0.011,
        varying_pressure=True,
    )

    # Text label at centre
    ev += make_text(
        LAND_W // 2, LAND_H // 2 + 80, W, H,
        "LANDSCAPE ALIGNMENT TEST", [1.0, 1.0, 1.0, 0.9], font_size=0.04,
    )

    # Lines to corner
    line1pixel = 2 / H
    
    ev += make_stroke(
        line_pts(0, 0, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )
    ev += make_stroke(
        line_pts(LAND_W - 1, 0, LAND_W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )
    ev += make_stroke(
        line_pts(0, LAND_H - 1, CORNER_LINE_WIDTH, LAND_H - 1 - CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )
    ev += make_stroke(
        line_pts(LAND_W - 1, LAND_H - 1, LAND_W - 1 - CORNER_LINE_WIDTH, LAND_H - 1 - CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )

    # Draw line widths
    for i, width in enumerate((1, 2, 4, 8, 16, 32)):
        width = width * 1 / H
        ev += make_stroke(
            line_pts(ANNOTATION_WIDTH_P0[0]+i*40, ANNOTATION_WIDTH_P0[1]+LINE_WIDTH_LENGTH, ANNOTATION_WIDTH_P0[0]+i*40, ANNOTATION_WIDTH_P0[1]+LINE_WIDTH_LENGTH*2, 2), W, H,
            rgba=[1.0, 0.24, 0.24, 1.0], brush_size=width,
        )
        ev += make_stroke(
            line_pts(ANNOTATION_WIDTH_P0[0]+i*40, ANNOTATION_WIDTH_P0[1] + GAUSSIAN_OFFSET+LINE_WIDTH_LENGTH*2.25, ANNOTATION_WIDTH_P0[0]+i*40, ANNOTATION_WIDTH_P0[1]+LINE_WIDTH_LENGTH*3+ GAUSSIAN_OFFSET, 2), W, H,
            rgba=[1.0, 0.24, 0.24, 1.0], brush_size=width, brush="gaussian"
        )

    return ev


def portrait_annotations():
    W, H = PORT_W, PORT_H
    ev = []

    # CYAN horizontal – gaussian, 80 % opacity
    ev += make_stroke(
        line_pts(*P_CYAN_P0, *P_CYAN_P1, 20), W, H,
        rgba=[0.24, 0.82, 0.82, 0.8], brush_size=0.009, brush="gaussian",
    )

    # MAGENTA vertical – thin circle
    ev += make_stroke(
        line_pts(*P_MAG_P0, *P_MAG_P1, 30), W, H,
        rgba=[0.82, 0.24, 0.82, 1.0], brush_size=0.005,
    )

    # ORANGE diagonal – medium circle, varying pressure
    ev += make_stroke(
        line_pts(*P_ORA_P0, *P_ORA_P1, 30), W, H,
        rgba=[0.86, 0.47, 0.16, 1.0], brush_size=0.011,
        varying_pressure=True,
    )

    # WHITE anti-diagonal – medium circle, 90 % opacity
    ev += make_stroke(
        line_pts(*P_WHT_P0, *P_WHT_P1, 30), W, H,
        rgba=[0.82, 0.82, 0.82, 0.9], brush_size=0.009,
    )

    # Text label at centre
    ev += make_text(
        PORT_W // 2, PORT_H // 2 + 100, W, H,
        "PORTRAIT ALIGNMENT TEST", [1.0, 1.0, 1.0, 0.9], font_size=0.04,
    )

    # Lines to corner
    line1pixel = 2 / H
    
    ev += make_stroke(
        line_pts(0, 0, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )
    ev += make_stroke(
        line_pts(W - 1, 0, W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )
    ev += make_stroke(
        line_pts(0, H - 1, CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )
    ev += make_stroke(
        line_pts(W - 1, H - 1, W - 1 - CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )

    return ev


def draw_gaussian_line(img, p0, p1, width, color):
    from PIL import ImageDraw, ImageFilter, Image
    # Create single-channel mask image
    mask = Image.new("L", img.size, 0)
    d_mask = ImageDraw.Draw(mask)
    d_mask.line([p0, p1], fill=255, width=width)
    # Apply Gaussian blur to the mask
    blur_rad = max(1.0, width / 3.0)
    blurred_mask = mask.filter(ImageFilter.GaussianBlur(blur_rad))
    # Paste onto img
    color_img = Image.new("RGB", img.size, color)
    img.paste(color_img, mask=blurred_mask)


def create_vector_shapes_image(path, is_uhd=False):
    from PIL import Image, ImageDraw

    scale = 2 if is_uhd else 1
    W, H = LAND_W * scale, LAND_H * scale
    img  = Image.new("RGB", (W, H), (245, 245, 240))
    d    = ImageDraw.Draw(img)
    f  = _load_font(22 * scale)
    title_f = _load_font(24 * scale)
    section_f = _load_font(18 * scale)

    title_text = "VECTOR SHAPES TEST CHART  3840×2160" if is_uhd else "VECTOR SHAPES TEST CHART  1920×1080"
    d.text((50*scale, 40*scale), title_text, fill=(40, 40, 50), font=title_f)

    ref_color = (80, 80, 90)

    # 1. Squares
    d.text((150*scale, 120*scale), "Squares Test", fill=(60, 60, 70), font=section_f)
    for i in range(4):
        offset = i * 30
        d.rectangle([
            (150 + offset)*scale, (300 + offset)*scale,
            (550 - offset)*scale, (700 - offset)*scale
        ], outline=ref_color, width=4*scale)

    # 2. Circles
    d.text((760*scale, 120*scale), "Circles Test", fill=(60, 60, 70), font=section_f)
    cx, cy = 960, 500
    for r in (200, 160, 120, 80):
        d.ellipse([
            (cx - r)*scale, (cy - r)*scale,
            (cx + r)*scale, (cy + r)*scale
        ], outline=ref_color, width=4*scale)

    # 3. Triangles
    d.text((1370*scale, 120*scale), "Triangles Test", fill=(60, 60, 70), font=section_f)
    triangles = [
        [(1370, 700), (1770, 700), (1570, 353)],
        [(1420, 670), (1720, 670), (1570, 410)],
        [(1470, 640), (1670, 640), (1570, 467)],
        [(1520, 610), (1620, 610), (1570, 524)]
    ]
    for tri in triangles:
        scaled_tri = [(pt[0]*scale, pt[1]*scale) for pt in tri]
        d.polygon(scaled_tri, outline=ref_color, width=4*scale)

    _crosshair(d, W // 2, H // 2, size=40*scale, lw=2*scale)
    c_lw = CORNER_LINE_WIDTH * scale
    d.line([c_lw, c_lw, W - 1 - c_lw, c_lw, W - 1 - c_lw, H - 1 - c_lw, c_lw, H - 1 - c_lw, c_lw, c_lw], fill=(120, 120, 130), width=2*scale)

    img.save(path)
    print(f"  Saved shapes image    : {path}")


def vector_shapes_annotations():
    W, H = LAND_W, LAND_H
    ev = []

    # Trace all 4 concentric squares in blue
    for i in range(4):
        offset = i * 30
        ev += make_stroke(
            [(150 + offset, 300 + offset), (550 - offset, 300 + offset),
             (550 - offset, 700 - offset), (150 + offset, 700 - offset),
             (150 + offset, 300 + offset)], W, H,
            rgba=[0.24, 0.47, 0.86, 1.0], brush_size=4 / H
        )

    # Trace all 4 concentric circles in green
    cx, cy = 960, 500
    for r in (200, 160, 120, 80):
        circle_pts = []
        for k in range(80):
            theta = 2.0 * math.pi * k / 79
            circle_pts.append((cx + r * math.cos(theta), cy + r * math.sin(theta)))
        ev += make_stroke(circle_pts, W, H, rgba=[0.24, 0.78, 0.31, 1.0], brush_size=4 / H)

    # Trace all 4 concentric triangles in orange
    triangles = [
        [(1370, 700), (1770, 700), (1570, 353)],
        [(1420, 670), (1720, 670), (1570, 410)],
        [(1470, 640), (1670, 640), (1570, 467)],
        [(1520, 610), (1620, 610), (1570, 524)]
    ]
    for tri in triangles:
        ev += make_stroke(
            [tri[0], tri[1], tri[2], tri[0]], W, H,
            rgba=[0.86, 0.47, 0.16, 1.0], brush_size=4 / H
        )

    # Corner tracing lines
    line1pixel = 2 / H
    ev += make_stroke(line_pts(0, 0, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, 0, W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(0, H - 1, CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, H - 1, W - 1 - CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)

    return ev


def create_vector_thickness_image(path, is_uhd=False):
    from PIL import Image, ImageDraw

    scale = 2 if is_uhd else 1
    W, H = LAND_W * scale, LAND_H * scale
    img  = Image.new("RGB", (W, H), (245, 245, 240))
    d    = ImageDraw.Draw(img)
    sf = _load_font(16 * scale)
    title_f = _load_font(24 * scale)
    section_f = _load_font(18 * scale)

    title_text = "LINE THICKNESS & PROFILE TEST CHART  3840×2160" if is_uhd else "LINE THICKNESS & PROFILE TEST CHART  1920×1080"
    d.text((50*scale, 40*scale), title_text, fill=(40, 40, 50), font=title_f)

    ref_color = (60, 200, 80)

    # Column 1: Solid Circle Brush Lines (x from 100 to 500)
    d.text((100*scale, 120*scale), "Solid Circle Brush", fill=(60, 60, 70), font=section_f)
    thicknesses = [1, 2, 4, 8, 12, 16, 24, 32]
    y_pos = [200, 290, 380, 470, 560, 650, 740, 830]
    for th, y in zip(thicknesses, y_pos):
        d.line([(100*scale, y*scale), (500*scale, y*scale)], fill=ref_color, width=th*scale)
        d.text((515*scale, (y - 8)*scale), f"{th}px", fill=(100, 100, 110), font=sf)

    # Column 2: Gaussian Soft Brush Lines (x from 700 to 1100)
    d.text((700*scale, 120*scale), "Gaussian Soft Brush", fill=(60, 60, 70), font=section_f)
    for th, y in zip(thicknesses, y_pos):
        draw_gaussian_line(img, (700*scale, y*scale), (1100*scale, y*scale), th*scale, ref_color)
        d.text((1115*scale, (y - 8)*scale), f"Soft {th}px", fill=(100, 100, 110), font=sf)

    # Column 3: Tapered Profiles (x from 1350 to 1750)
    d.text((1350*scale, 120*scale), "Tapered Profiles (Varying Width)", fill=(60, 60, 70), font=section_f)
    taper_y = [220, 370, 520, 670, 820]
    taper_th = [4, 8, 16, 24, 32]
    for th, y in zip(taper_th, taper_y):
        half_th = th // 2
        poly_pts = [
            (1350*scale, y*scale),
            (1550*scale, (y - half_th)*scale),
            (1750*scale, y*scale),
            (1550*scale, (y + half_th)*scale)
        ]
        d.polygon(poly_pts, fill=ref_color)
        d.text((1765*scale, (y - 8)*scale), f"Taper {th}px", fill=(100, 100, 110), font=sf)

    c_lw = CORNER_LINE_WIDTH * scale
    d.line([c_lw, c_lw, W - 1 - c_lw, c_lw, W - 1 - c_lw, H - 1 - c_lw, c_lw, H - 1 - c_lw, c_lw, c_lw], fill=(120, 120, 130), width=2*scale)

    img.save(path)
    print(f"  Saved thickness image : {path}")


def vector_thickness_annotations():
    W, H = LAND_W, LAND_H
    ev = []
    y_pos = [200, 290, 380, 470, 560, 650, 740, 830]
    thicknesses = [1, 2, 4, 8, 12, 16, 24, 32]

    # Trace all 8 Solid Circle Lines in red circle brush
    for th, y in zip(thicknesses, y_pos):
        ev += make_stroke([(100, y), (500, y)], W, H, rgba=[1.0, 0.24, 0.24, 0.8], brush_size=th / H)

    # Trace all 8 Gaussian Soft Lines in red gaussian brush
    for th, y in zip(thicknesses, y_pos):
        ev += make_stroke(line_pts(700, y, 1100, y, 400), W, H, rgba=[1.0, 0.24, 0.24, 0.8], brush_size=th / H, brush="gaussian")

    # Trace all 5 Tapered Profiles
    taper_y = [220, 370, 520, 670, 820]
    taper_th = [4, 8, 16, 24, 32]
    for th, y in zip(taper_th, taper_y):
        t_pts = line_pts(1350, y, 1750, y, 40)
        ev += make_stroke(t_pts, W, H, rgba=[1.0, 0.24, 0.24, 0.8], brush_size=th / H, varying_pressure=True)

    # Corner tracing lines
    line1pixel = 2 / H
    ev += make_stroke(line_pts(0, 0, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, 0, W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(0, H - 1, CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, H - 1, W - 1 - CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)

    return ev





def join_segments(segs):
    res = []
    for idx, s in enumerate(segs):
        if idx == len(segs) - 1:
            res.extend(s)
        else:
            res.extend(s[:-1])
    return res

def get_calligraphy_paths(W, H):
    """Generate variable-width brush strokes for the test chart.

    Returns a list of dicts with keys:
        points   – list of (x, y) tuples (already scaled to W×H)
        widths   – list of per-point width multipliers (0..1)
        base_w   – base brush diameter in pixels (at 1920×1080)
        rgba     – [r, g, b, a]
        name     – descriptive label
    """

    def scale_pts(pts):
        return [(pt[0] * W / 1920.0, pt[1] * H / 1080.0) for pt in pts]

    def brush_width(idx, total, taper_start=True, taper_end=True, min_w=0.1, max_w=1.0, pulses=1):
        progress = idx / max(1, total - 1)
        taper_len = min(20, total // 4)
        
        taper = 1.0
        if taper_start and idx < taper_len:
            taper = idx / float(taper_len)
        elif taper_end and total - 1 - idx < taper_len:
            taper = (total - 1 - idx) / float(taper_len)
            
        if pulses == 1:
            swell = math.sin(progress * math.pi)
        else:
            swell = 0.5 - 0.5 * math.cos(progress * math.pi * 2 * pulses)
            
        return min_w + (max_w - min_w) * swell * taper

    paths = []

    # ── Colour palette ────────────────────────────────────────────────
    magenta = [0.72, 0.18, 0.52, 1.0]
    dark_ink = [0.18, 0.16, 0.22, 1.0]
    orange = [0.86, 0.42, 0.12, 1.0]
    teal = [0.12, 0.62, 0.62, 1.0]

    # ==================================================================
    # GROUP 1 — Variable Thickness Swirls (Left)
    # ==================================================================
    swirl_pts = []
    for i in range(200):
        t = i / 199.0
        angle = t * math.pi * 5
        radius = 20 + t * 250
        x = 350 + math.cos(angle) * radius
        y = 500 + math.sin(angle) * radius
        swirl_pts.append((x, y))

    swirl_widths = [brush_width(i, len(swirl_pts), pulses=2.5, min_w=0.15, max_w=1.0) for i in range(len(swirl_pts))]
    paths.append({"points": scale_pts(swirl_pts), "widths": swirl_widths, "base_w": 35.0, "rgba": teal, "name": "Swirl"})

    swirl2_pts = []
    for i in range(150):
        t = i / 149.0
        angle = -t * math.pi * 4
        radius = 10 + t * 150
        x = 350 + math.cos(angle) * radius
        y = 800 + math.sin(angle) * radius
        swirl2_pts.append((x, y))

    swirl2_widths = [brush_width(i, len(swirl2_pts), pulses=2, min_w=0.1, max_w=0.8) for i in range(len(swirl2_pts))]
    paths.append({"points": scale_pts(swirl2_pts), "widths": swirl2_widths, "base_w": 25.0, "rgba": magenta, "name": "Swirl 2"})

    # ==================================================================
    # GROUP 2 — Variable Thickness Curves (Center)
    # ==================================================================
    curve1 = bezier_curve((800, 200), (1000, 200), (700, 500), (900, 500), 80)
    c1_widths = [brush_width(i, len(curve1), max_w=1.0, pulses=1) for i in range(len(curve1))]
    paths.append({"points": scale_pts(curve1), "widths": c1_widths, "base_w": 40.0, "rgba": orange, "name": "Curve 1"})

    curve2 = bezier_curve((800, 400), (1100, 400), (600, 700), (900, 700), 80)
    c2_widths = [brush_width(i, len(curve2), max_w=1.0, pulses=2) for i in range(len(curve2))]
    paths.append({"points": scale_pts(curve2), "widths": c2_widths, "base_w": 30.0, "rgba": dark_ink, "name": "Curve 2"})

    curve3 = bezier_curve((800, 600), (1200, 600), (500, 900), (900, 900), 80)
    c3_widths = [brush_width(i, len(curve3), max_w=1.0, pulses=3) for i in range(len(curve3))]
    paths.append({"points": scale_pts(curve3), "widths": c3_widths, "base_w": 20.0, "rgba": magenta, "name": "Curve 3"})

    # ==================================================================
    # GROUP 3 — Arrows (Top/Mid Right)
    # ==================================================================
    arrow_shaft = bezier_curve((1400, 700), (1200, 500), (1300, 200), (1600, 300), 100)
    def shaft_width(idx, total):
        progress = idx / max(1, total - 1)
        w = 0.1 + 0.9 * math.pow(progress, 2)
        # taper at the very end to match barb start thickness (0.2)
        if progress > 0.9:
            t = (1.0 - progress) / 0.1
            w = 0.2 + (w - 0.2) * t
        return max(0.0, w)

    def barb_width(idx, total):
        progress = idx / max(1, total - 1)
        if progress < 0.5:
            t = progress / 0.5
            return 0.2 * (1 - t) + 0.33 * t
        else:
            t = (progress - 0.5) / 0.5
            return 0.33 * (1 - t) + 0.0 * t

    a_widths = [shaft_width(i, len(arrow_shaft)) for i in range(len(arrow_shaft))]
    paths.append({"points": scale_pts(arrow_shaft), "widths": a_widths, "base_w": 25.0, "rgba": dark_ink, "name": "Arrow Shaft"})

    head_tip = arrow_shaft[-1]
    dx = arrow_shaft[-1][0] - arrow_shaft[-5][0]
    dy = arrow_shaft[-1][1] - arrow_shaft[-5][1]
    ang = math.atan2(dy, dx)
    
    barb_len = 80
    
    ang1_start = ang + math.pi * 0.95
    ang1_mid = ang + math.pi * 0.90
    ang1_end = ang + math.pi * 0.85
    barb1 = bezier_curve(
        head_tip,
        (head_tip[0] + math.cos(ang1_start)*barb_len*0.3, head_tip[1] + math.sin(ang1_start)*barb_len*0.3),
        (head_tip[0] + math.cos(ang1_mid)*barb_len*0.7, head_tip[1] + math.sin(ang1_mid)*barb_len*0.7),
        (head_tip[0] + math.cos(ang1_end)*barb_len, head_tip[1] + math.sin(ang1_end)*barb_len),
        20)
    b1_widths = [barb_width(i, len(barb1)) for i in range(len(barb1))]
    paths.append({"points": scale_pts(barb1), "widths": b1_widths, "base_w": 20.0, "rgba": dark_ink, "name": "Arrow Barb 1"})

    ang2_start = ang - math.pi * 0.95
    ang2_mid = ang - math.pi * 0.90
    ang2_end = ang - math.pi * 0.85
    barb2 = bezier_curve(
        head_tip,
        (head_tip[0] + math.cos(ang2_start)*barb_len*0.3, head_tip[1] + math.sin(ang2_start)*barb_len*0.3),
        (head_tip[0] + math.cos(ang2_mid)*barb_len*0.7, head_tip[1] + math.sin(ang2_mid)*barb_len*0.7),
        (head_tip[0] + math.cos(ang2_end)*barb_len, head_tip[1] + math.sin(ang2_end)*barb_len),
        20)
    b2_widths = [barb_width(i, len(barb2)) for i in range(len(barb2))]
    paths.append({"points": scale_pts(barb2), "widths": b2_widths, "base_w": 20.0, "rgba": dark_ink, "name": "Arrow Barb 2"})

    arrow2_shaft = bezier_curve((1350, 400), (1500, 400), (1700, 600), (1500, 800), 100)
    a2_widths = [shaft_width(i, len(arrow2_shaft)) for i in range(len(arrow2_shaft))]
    paths.append({"points": scale_pts(arrow2_shaft), "widths": a2_widths, "base_w": 20.0, "rgba": teal, "name": "Arrow 2 Shaft"})

    head2_tip = arrow2_shaft[-1]
    dx2 = arrow2_shaft[-1][0] - arrow2_shaft[-5][0]
    dy2 = arrow2_shaft[-1][1] - arrow2_shaft[-5][1]
    ang_2 = math.atan2(dy2, dx2)
    
    ang2_1_start = ang_2 + math.pi * 0.95
    ang2_1_mid = ang_2 + math.pi * 0.90
    ang2_1_end = ang_2 + math.pi * 0.85
    barb2_1 = bezier_curve(
        head2_tip,
        (head2_tip[0] + math.cos(ang2_1_start)*barb_len*0.3, head2_tip[1] + math.sin(ang2_1_start)*barb_len*0.3),
        (head2_tip[0] + math.cos(ang2_1_mid)*barb_len*0.7, head2_tip[1] + math.sin(ang2_1_mid)*barb_len*0.7),
        (head2_tip[0] + math.cos(ang2_1_end)*barb_len, head2_tip[1] + math.sin(ang2_1_end)*barb_len),
        20)
    b3_widths = [barb_width(i, len(barb2_1)) for i in range(len(barb2_1))]
    paths.append({"points": scale_pts(barb2_1), "widths": b3_widths, "base_w": 15.0, "rgba": teal, "name": "Arrow 2 Barb 1"})

    ang2_2_start = ang_2 - math.pi * 0.95
    ang2_2_mid = ang_2 - math.pi * 0.90
    ang2_2_end = ang_2 - math.pi * 0.85
    barb2_2 = bezier_curve(
        head2_tip,
        (head2_tip[0] + math.cos(ang2_2_start)*barb_len*0.3, head2_tip[1] + math.sin(ang2_2_start)*barb_len*0.3),
        (head2_tip[0] + math.cos(ang2_2_mid)*barb_len*0.7, head2_tip[1] + math.sin(ang2_2_mid)*barb_len*0.7),
        (head2_tip[0] + math.cos(ang2_2_end)*barb_len, head2_tip[1] + math.sin(ang2_2_end)*barb_len),
        20)
    b4_widths = [barb_width(i, len(barb2_2)) for i in range(len(barb2_2))]
    paths.append({"points": scale_pts(barb2_2), "widths": b4_widths, "base_w": 15.0, "rgba": teal, "name": "Arrow 2 Barb 2"})

    return paths



def create_vector_calligraphy_image(path, is_uhd=False):
    from PIL import Image, ImageDraw

    scale = 2 if is_uhd else 1
    W, H = LAND_W * scale, LAND_H * scale
    img  = Image.new("RGB", (W, H), (245, 245, 240))
    d    = ImageDraw.Draw(img)
    title_f = _load_font(24 * scale)
    section_f = _load_font(18 * scale)

    title_text = "CALLIGRAPHY & VARIABLE WIDTH TEST CHART  3840×2160" if is_uhd else "CALLIGRAPHY & VARIABLE WIDTH TEST CHART  1920×1080"
    d.text((50*scale, 40*scale), title_text, fill=(40, 40, 50), font=title_f)

    # Section titles
    d.text((100*scale, 100*scale), "Variable Thickness Swirls", fill=(60, 60, 70), font=section_f)
    d.text((800*scale, 100*scale), "Variable Thickness Curves", fill=(60, 60, 70), font=section_f)
    d.text((1400*scale, 100*scale), "Artistic Arrows", fill=(60, 60, 70), font=section_f)

    paths = get_calligraphy_paths(LAND_W, LAND_H)
    for p in paths:
        pts = p["points"]
        widths = p["widths"]
        base_w = p["base_w"]
        col_rgb = (int(p["rgba"][0]*255), int(p["rgba"][1]*255), int(p["rgba"][2]*255))
        for i, (px, py) in enumerate(pts):
            w = base_w * widths[i]
            r_val = w * scale / 2.0
            d.ellipse([
                (px * scale - r_val), (py * scale - r_val),
                (px * scale + r_val), (py * scale + r_val)
            ], fill=col_rgb)

    c_lw = CORNER_LINE_WIDTH * scale
    d.line([c_lw, c_lw, W - 1 - c_lw, c_lw, W - 1 - c_lw, H - 1 - c_lw, c_lw, H - 1 - c_lw, c_lw, c_lw], fill=(120, 120, 130), width=2*scale)

    img.save(path)
    print(f"  Saved calligraphy img : {path}")


def vector_calligraphy_annotations():
    W, H = LAND_W, LAND_H
    ev = []

    paths = get_calligraphy_paths(W, H)
    for p in paths:
        pts = p["points"]
        widths = p["widths"]
        base_w = p["base_w"]
        rgba = p["rgba"]

        stroke_id = str(uuid.uuid4())
        xs, ys, sizes = [], [], []
        for i, (px, py) in enumerate(pts):
            nx, ny = px_to_norm(px, py, W, H)
            xs.append(nx)
            ys.append(ny)
            sizes.append(float(widths[i] * base_w / H))

        start = json.dumps({
            "OTIO_SCHEMA":  "PaintStart.1",
            "brush":        "circle",
            "friendly_name": "testchart_generator",
            "rgba":         [float(c) for c in rgba],
            "source_index": 0,
            "timestamp":    ts(),
            "type":         "color",
            "uuid":         stroke_id,
            "visible":      True,
        })
        events = [otio.adapters.read_from_string(start, adapter_name="otio_json")]

        pts_json = json.dumps({
            "OTIO_SCHEMA":  "PaintPoint.1",
            "source_index": 0,
            "uuid":         stroke_id,
            "timestamp":    ts(),
            "points": {
                "OTIO_SCHEMA": "PaintVertices.1",
                "x":    xs,
                "y":    ys,
                "size": sizes,
            },
        })
        events.append(otio.adapters.read_from_string(pts_json, adapter_name="otio_json"))

        end = json.dumps({
            "OTIO_SCHEMA": "PaintEnd.1",
            "uuid":        stroke_id,
            "timestamp":   ts(),
        })
        events.append(otio.adapters.read_from_string(end, adapter_name="otio_json"))
        ev += events

    # Corner tracing lines
    line1pixel = 2 / H
    ev += make_stroke(line_pts(0, 0, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, 0, W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(0, H - 1, CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, H - 1, W - 1 - CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)

    return ev


def create_vector_colors_image(path, is_uhd=False):
    from PIL import Image, ImageDraw

    scale = 2 if is_uhd else 1
    W, H = LAND_W * scale, LAND_H * scale
    img  = Image.new("RGB", (W, H), (245, 245, 240))
    d    = ImageDraw.Draw(img)
    title_f = _load_font(24 * scale)

    title_text = "COLOR CURVES TEST CHART  3840×2160" if is_uhd else "COLOR CURVES TEST CHART  1920×1080"
    d.text((50*scale, 40*scale), title_text, fill=(40, 40, 50), font=title_f)

    colors = [
        (220, 60, 60),    # Red
        (60, 200, 80),    # Green
        (60, 120, 220),   # Blue
        (220, 200, 60),   # Yellow
        (60, 210, 210),   # Cyan
        (210, 60, 210),   # Magenta
        (220, 120, 40),   # Orange
        (80, 80, 90),     # Dark slate/grey
    ]
    radii = [150, 200, 250, 300, 350, 400, 450, 500]
    for r, col in zip(radii, colors):
        arch_pts = []
        n_arch = 150
        for k in range(n_arch):
            theta = math.pi * k / (n_arch - 1)
            ax = 960 + r * math.cos(theta)
            ay = 800 - r * math.sin(theta)
            arch_pts.append((ax*scale, ay*scale))
        d.line(arch_pts, fill=col, width=5*scale)

    c_lw = CORNER_LINE_WIDTH * scale
    d.line([c_lw, c_lw, W - 1 - c_lw, c_lw, W - 1 - c_lw, H - 1 - c_lw, c_lw, H - 1 - c_lw, c_lw, c_lw], fill=(120, 120, 130), width=2*scale)

    img.save(path)
    print(f"  Saved colors image    : {path}")


def vector_colors_annotations():
    W, H = LAND_W, LAND_H
    ev = []

    colors = [
        [0.86, 0.24, 0.24, 1.0],    # Red
        [0.24, 0.78, 0.31, 1.0],    # Green
        [0.24, 0.47, 0.86, 1.0],    # Blue
        [0.86, 0.78, 0.24, 1.0],    # Yellow
        [0.24, 0.82, 0.82, 1.0],    # Cyan
        [0.82, 0.24, 0.82, 1.0],    # Magenta
        [0.86, 0.47, 0.16, 1.0],    # Orange
        [0.31, 0.31, 0.35, 1.0],    # Dark slate/grey
    ]
    radii = [150, 200, 250, 300, 350, 400, 450, 500]
    for r, col in zip(radii, colors):
        arch_pts = []
        n_arch = 50
        for k in range(n_arch):
            theta = math.pi * k / (n_arch - 1)
            ax = 960 + r * math.cos(theta)
            ay = 800 - r * math.sin(theta)
            arch_pts.append((ax, ay))
        ev += make_stroke(arch_pts, W, H, rgba=col, brush_size=5 / H)

    # Corner tracing lines
    line1pixel = 2 / H
    ev += make_stroke(line_pts(0, 0, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, 0, W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(0, H - 1, CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, H - 1, W - 1 - CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)

    return ev


def create_vector_fonts_image(path, is_uhd=False):
    from PIL import Image, ImageDraw

    scale = 2 if is_uhd else 1
    W, H = LAND_W * scale, LAND_H * scale
    img  = Image.new("RGB", (W, H), (245, 245, 240))
    d    = ImageDraw.Draw(img)
    title_f = _load_font(24 * scale)

    title_text = "FONT ALIGNMENT & SIZING TEST CHART  3840×2160" if is_uhd else "FONT ALIGNMENT & SIZING TEST CHART  1920×1080"
    d.text((50*scale, 40*scale), title_text, fill=(40, 40, 50), font=title_f)

    # Draw reference text in dark slate
    font_y = [160, 220, 300, 400, 550, 750, 1000]
    font_sizes = [12, 16, 24, 32, 48, 72, 96]
    for sz, y in zip(font_sizes, font_y):
        tf = _load_font(sz * scale)
        d.text((100*scale, y*scale), f"{sz}pt Font Size Sample Text", fill=(60, 60, 70), font=tf, anchor="ls")

    c_lw = CORNER_LINE_WIDTH * scale
    d.line([c_lw, c_lw, W - 1 - c_lw, c_lw, W - 1 - c_lw, H - 1 - c_lw, c_lw, H - 1 - c_lw, c_lw, c_lw], fill=(120, 120, 130), width=2*scale)

    img.save(path)
    print(f"  Saved fonts image     : {path}")


def vector_fonts_annotations():
    W, H = LAND_W, LAND_H
    ev = []

    font_y = [160, 220, 300, 400, 550, 750, 1000]
    font_sizes = [12, 16, 24, 32, 48, 72, 96]
    for sz, y in zip(font_sizes, font_y):
        ev += make_text(
            100, y, W, H,
            f"{sz}pt Font Size Sample Text",
            [1.0, 0.24, 0.24, 0.9],
            font_size=sz * 417.0 / H,
        )

    # Corner tracing lines
    line1pixel = 2 / H
    ev += make_stroke(line_pts(0, 0, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, 0, W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(0, H - 1, CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)
    ev += make_stroke(line_pts(W - 1, H - 1, W - 1 - CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, 4), W, H, rgba=[0, 1, 0, 1.0], brush_size=line1pixel)

    return ev


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Modular vector paths
    v_shapes_path = os.path.join(SCRIPT_DIR, "vector_shapes.png")
    v_shapes_uhd_path = os.path.join(SCRIPT_DIR, "vector_shapes_uhd.png")
    v_thickness_path = os.path.join(SCRIPT_DIR, "vector_thickness.png")
    v_thickness_uhd_path = os.path.join(SCRIPT_DIR, "vector_thickness_uhd.png")
    v_calligraphy_path = os.path.join(SCRIPT_DIR, "vector_calligraphy.png")
    v_calligraphy_uhd_path = os.path.join(SCRIPT_DIR, "vector_calligraphy_uhd.png")
    v_colors_path = os.path.join(SCRIPT_DIR, "vector_colors.png")
    v_colors_uhd_path = os.path.join(SCRIPT_DIR, "vector_colors_uhd.png")
    v_fonts_path = os.path.join(SCRIPT_DIR, "vector_fonts.png")
    v_fonts_uhd_path = os.path.join(SCRIPT_DIR, "vector_fonts_uhd.png")

    otio_path = os.path.join(SCRIPT_DIR, "testchart_annotations.otio")

    print("Generating test images …")
    create_vector_shapes_image(v_shapes_path, is_uhd=False)
    create_vector_shapes_image(v_shapes_uhd_path, is_uhd=True)
    create_vector_thickness_image(v_thickness_path, is_uhd=False)
    create_vector_thickness_image(v_thickness_uhd_path, is_uhd=True)
    create_vector_calligraphy_image(v_calligraphy_path, is_uhd=False)
    create_vector_calligraphy_image(v_calligraphy_uhd_path, is_uhd=True)
    create_vector_colors_image(v_colors_path, is_uhd=False)
    create_vector_colors_image(v_colors_uhd_path, is_uhd=True)
    create_vector_fonts_image(v_fonts_path, is_uhd=False)
    create_vector_fonts_image(v_fonts_uhd_path, is_uhd=True)

    print("Building OTIO annotation file …")

    m_shapes = ORIAnnotations.Media(name="vector_shapes.png", media_path=v_shapes_path, frame_rate=24.0, duration=24, start_frame=0)
    m_shapes_uhd = ORIAnnotations.Media(name="vector_shapes_uhd.png", media_path=v_shapes_uhd_path, frame_rate=24.0, duration=24, start_frame=0)
    m_thickness = ORIAnnotations.Media(name="vector_thickness.png", media_path=v_thickness_path, frame_rate=24.0, duration=24, start_frame=0)
    m_thickness_uhd = ORIAnnotations.Media(name="vector_thickness_uhd.png", media_path=v_thickness_uhd_path, frame_rate=24.0, duration=24, start_frame=0)
    m_calligraphy = ORIAnnotations.Media(name="vector_calligraphy.png", media_path=v_calligraphy_path, frame_rate=24.0, duration=24, start_frame=0)
    m_calligraphy_uhd = ORIAnnotations.Media(name="vector_calligraphy_uhd.png", media_path=v_calligraphy_uhd_path, frame_rate=24.0, duration=24, start_frame=0)
    m_colors = ORIAnnotations.Media(name="vector_colors.png", media_path=v_colors_path, frame_rate=24.0, duration=24, start_frame=0)
    m_colors_uhd = ORIAnnotations.Media(name="vector_colors_uhd.png", media_path=v_colors_uhd_path, frame_rate=24.0, duration=24, start_frame=0)
    m_fonts = ORIAnnotations.Media(name="vector_fonts.png", media_path=v_fonts_path, frame_rate=24.0, duration=24, start_frame=0)
    m_fonts_uhd = ORIAnnotations.Media(name="vector_fonts_uhd.png", media_path=v_fonts_uhd_path, frame_rate=24.0, duration=24, start_frame=0)

    ri_shapes = ORIAnnotations.ReviewItem(media=m_shapes)
    frame_shapes = ORIAnnotations.ReviewItemFrame(review_item=ri_shapes, frame=1, note="**Vector Shapes test**", annotation_image=v_shapes_path)
    frame_shapes.annotation_commands = vector_shapes_annotations()
    ri_shapes.review_frames = [frame_shapes]

    ri_shapes_uhd = ORIAnnotations.ReviewItem(media=m_shapes_uhd)
    frame_shapes_uhd = ORIAnnotations.ReviewItemFrame(review_item=ri_shapes_uhd, frame=1, note="**Vector Shapes UHD test**", annotation_image=v_shapes_uhd_path)
    frame_shapes_uhd.annotation_commands = vector_shapes_annotations()
    ri_shapes_uhd.review_frames = [frame_shapes_uhd]

    ri_thickness = ORIAnnotations.ReviewItem(media=m_thickness)
    frame_thickness = ORIAnnotations.ReviewItemFrame(review_item=ri_thickness, frame=1, note="**Vector Thickness/Gaussian test**", annotation_image=v_thickness_path)
    frame_thickness.annotation_commands = vector_thickness_annotations()
    ri_thickness.review_frames = [frame_thickness]

    ri_thickness_uhd = ORIAnnotations.ReviewItem(media=m_thickness_uhd)
    frame_thickness_uhd = ORIAnnotations.ReviewItemFrame(review_item=ri_thickness_uhd, frame=1, note="**Vector Thickness/Gaussian UHD test**", annotation_image=v_thickness_uhd_path)
    frame_thickness_uhd.annotation_commands = vector_thickness_annotations()
    ri_thickness_uhd.review_frames = [frame_thickness_uhd]

    ri_calligraphy = ORIAnnotations.ReviewItem(media=m_calligraphy)
    frame_calligraphy = ORIAnnotations.ReviewItemFrame(review_item=ri_calligraphy, frame=1, note="**Vector Calligraphy test**", annotation_image=v_calligraphy_path)
    frame_calligraphy.annotation_commands = vector_calligraphy_annotations()
    ri_calligraphy.review_frames = [frame_calligraphy]

    ri_calligraphy_uhd = ORIAnnotations.ReviewItem(media=m_calligraphy_uhd)
    frame_calligraphy_uhd = ORIAnnotations.ReviewItemFrame(review_item=ri_calligraphy_uhd, frame=1, note="**Vector Calligraphy UHD test**", annotation_image=v_calligraphy_uhd_path)
    frame_calligraphy_uhd.annotation_commands = vector_calligraphy_annotations()
    ri_calligraphy_uhd.review_frames = [frame_calligraphy_uhd]

    ri_colors = ORIAnnotations.ReviewItem(media=m_colors)
    frame_colors = ORIAnnotations.ReviewItemFrame(review_item=ri_colors, frame=1, note="**Vector Color Curves test**", annotation_image=v_colors_path)
    frame_colors.annotation_commands = vector_colors_annotations()
    ri_colors.review_frames = [frame_colors]

    ri_colors_uhd = ORIAnnotations.ReviewItem(media=m_colors_uhd)
    frame_colors_uhd = ORIAnnotations.ReviewItemFrame(review_item=ri_colors_uhd, frame=1, note="**Vector Color Curves UHD test**", annotation_image=v_colors_uhd_path)
    frame_colors_uhd.annotation_commands = vector_colors_annotations()
    ri_colors_uhd.review_frames = [frame_colors_uhd]

    ri_fonts = ORIAnnotations.ReviewItem(media=m_fonts)
    frame_fonts = ORIAnnotations.ReviewItemFrame(review_item=ri_fonts, frame=1, note="**Vector Font test**", annotation_image=v_fonts_path)
    frame_fonts.annotation_commands = vector_fonts_annotations()
    ri_fonts.review_frames = [frame_fonts]

    ri_fonts_uhd = ORIAnnotations.ReviewItem(media=m_fonts_uhd)
    frame_fonts_uhd = ORIAnnotations.ReviewItemFrame(review_item=ri_fonts_uhd, frame=1, note="**Vector Font UHD test**", annotation_image=v_fonts_uhd_path)
    frame_fonts_uhd.annotation_commands = vector_fonts_annotations()
    ri_fonts_uhd.review_frames = [frame_fonts_uhd]

    # ── Review & group ─────────────────────────────────────────────────────────
    review_items_list = [
        ri_shapes, ri_thickness, ri_calligraphy, ri_colors, ri_fonts,
        ri_shapes_uhd, ri_thickness_uhd, ri_calligraphy_uhd, ri_colors_uhd, ri_fonts_uhd
    ]
    review = ORIAnnotations.Review(
        title="Test Chart Alignment Review",
        participants=["testchart_generator"],
        notes="Generated to verify annotation coordinate alignment across modular and responsive formats.",
        review_items=review_items_list,
    )

    rg = ORIAnnotations.ReviewGroup(
        media=[item.media for item in review_items_list],
        reviews=[review],
    )

    timeline = rg.export_otio_timeline()
    otio.adapters.write_to_file(timeline, otio_path)
    print(f"  Saved OTIO file       : {otio_path}")
    print("Done.")


if __name__ == "__main__":
    main()
