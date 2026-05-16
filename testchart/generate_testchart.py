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

# Access the SyncEvent schema module (loaded via the plugin manifest)
SyncEvent = otio.schema.schemadef.module_from_name("SyncEvent")

# ─── Coordinate helpers ───────────────────────────────────────────────────────

def px_to_norm(px, py, width, height):
    """Pixel → RV paint normalised coordinates.
    RV normalises by image width: x ∈ [-0.5, 0.5], y ∈ [-h/(2w), h/(2w)].
    """
    nx = (px - width  / 2.0) / height
    ny = -((py - height / 2.0) / height)
    return float(nx), float(ny)


def line_pts(x0, y0, x1, y1, n=24):
    """Return *n* evenly-spaced pixel points along the segment."""
    return [
        (x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1))
        for i in range(n)
    ]


def pressure_sizes(base_size, n, variation=0.6):
    """Sizes that swell from thin → thick → thin (simulates pen pressure)."""
    return [
        base_size * (0.5 + variation * math.sin(math.pi * i / max(n - 1, 1)))
        for i in range(n)
    ]


def ts():
    return datetime.now().isoformat()


# ─── OTIO event builders ──────────────────────────────────────────────────────

def make_stroke(points_px, width, height, rgba, brush_size,
                brush="circle", varying_pressure=False):
    """
    Build [PaintStart, PaintPoints, PaintEnd] OTIO objects for one stroke.

    points_px : list of (pixel_x, pixel_y)
    rgba      : [r, g, b, a]  (0-1 floats)
    brush_size: normalised radius (e.g. 0.02 ≈ 2 % of half-height)
    """
    stroke_id = str(uuid.uuid4())
    n = len(points_px)

    xs, ys, sizes = [], [], []
    pressure = pressure_sizes(brush_size, n) if varying_pressure else [brush_size] * n

    for i, (px, py) in enumerate(points_px):
        nx, ny = px_to_norm(px, py, width, height)
        xs.append(nx)
        ys.append(ny)
        sizes.append(float(pressure[i]))

    events = []

    # PaintStart
    start = json.dumps({
        "OTIO_SCHEMA":  "PaintStart.1",
        "brush":        brush,
        "friendly_name": "testchart_generator",
        "rgba":         [float(c) for c in rgba],
        "source_index": 0,
        "timestamp":    ts(),
        "type":         "color",
        "uuid":         stroke_id,
        "visible":      True,
    })
    events.append(otio.adapters.read_from_string(start, adapter_name="otio_json"))

    # PaintPoints  (uses PaintVertices array format)
    pts = json.dumps({
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
    events.append(otio.adapters.read_from_string(pts, adapter_name="otio_json"))

    # PaintEnd
    end = json.dumps({
        "OTIO_SCHEMA": "PaintEnd.1",
        "uuid":        stroke_id,
        "timestamp":   ts(),
    })
    events.append(otio.adapters.read_from_string(end, adapter_name="otio_json"))

    return events


def make_text(px, py, width, height, text, rgba, font_size=0.05):
    """Build a single TextAnnotation event."""
    nx, ny = px_to_norm(px, py, width, height)
    j = json.dumps({
        "OTIO_SCHEMA":   "TextAnnotation.1",
        "uuid":          str(uuid.uuid4()),
        "rgba":          [float(c) for c in rgba],
        "friendly_name": "testchart_generator",
        "text":          text,
        "position":      [nx, ny],
        "font_size":     float(font_size),
        "scale":         1.0,
        "rotation":      0.0,
        "spacing":       1.0,
        "font":          "monospace",
        "timestamp":     ts(),
    })
    return [otio.adapters.read_from_string(j, adapter_name="otio_json")]


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
L_WIDTH_P0 = (400, 1920/2)
ANNOTATION_WIDTH_P0 = (400, 1920/2 - 50)
CORNER_LINE_WIDTH = 40

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


def create_landscape_image(path):
    from PIL import Image, ImageDraw

    W, H = LAND_W, LAND_H
    img  = Image.new("RGB", (W, H), (26, 26, 46))
    d    = ImageDraw.Draw(img)
    f  = _load_font(22)
    sf = _load_font(16)

    _draw_grid(d, W, H)

    # Reference lines – colours match annotation rgba values below
    d.line([L_RED_P0,    L_RED_P1   ], fill=(220,  60,  60), width=4)
    d.line([L_BLUE_P0,   L_BLUE_P1  ], fill=( 60, 120, 220), width=3)
    d.line([L_GREEN_P0,  L_GREEN_P1 ], fill=( 60, 200,  80), width=3)
    d.line([L_YELLOW_P0, L_YELLOW_P1], fill=(220, 200,  60), width=4)

    # Draw line widths
    for i, width in enumerate((1, 2, 4, 8, 16, 32)):
        d.line([L_WIDTH_P0[0]+i*40, L_WIDTH_P0[1], L_WIDTH_P0[0]+i*40, L_WIDTH_P0[1]+32], fill=(255, 255,  255), width=width)

    # Draw corner box
    d.line([CORNER_LINE_WIDTH,CORNER_LINE_WIDTH, W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, W - 1 - CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH], fill=(255, 255,  255), width=2)

    d.text((L_WIDTH_P0[0]+8*40, L_WIDTH_P0[1]), "Brush Widths 1px - 64px",
           fill=(255, 255, 255), font=f)

    _crosshair(d, W // 2, H // 2)

    d.text((10, 10), "LANDSCAPE TEST CHART  1920×1080",
           fill=(255, 255, 255), font=f)

    # Line labels (offset slightly from the line so they remain readable)
    d.text((750, 180),  "RED – thick circle, full opacity",
           fill=(240,  80,  80), font=sf)
    d.text((110, 370),  "BLUE – gaussian, 70 % opacity",
           fill=( 80, 140, 220), font=sf)
    d.text((1452, 490), "GREEN\nvertical\nthin circle\n85 % opacity",
           fill=( 80, 210, 100), font=sf)
    d.text((750, 830),  "YELLOW – varying pressure, anti-diagonal",
           fill=(220, 210,  80), font=sf)
    d.text((860, 520),  "CENTER\n(0, 0)", fill=(180, 180, 180), font=sf)

    img.save(path)
    print(f"  Saved landscape image : {path}")


def create_portrait_image(path):
    from PIL import Image, ImageDraw

    W, H = PORT_W, PORT_H
    img  = Image.new("RGB", (W, H), (20, 40, 20))
    d    = ImageDraw.Draw(img)

    _draw_grid(d, W, H)

    d.line([P_CYAN_P0, P_CYAN_P1], fill=( 60, 210, 210), width=3)
    d.line([P_MAG_P0,  P_MAG_P1 ], fill=(210,  60, 210), width=3)
    d.line([P_ORA_P0,  P_ORA_P1 ], fill=(220, 120,  40), width=4)
    d.line([P_WHT_P0,  P_WHT_P1 ], fill=(210, 210, 210), width=3)

    _crosshair(d, W // 2, H // 2)

    f  = _load_font(22)
    sf = _load_font(16)

    d.text((10, 10), "PORTRAIT TEST CHART  1080×1920",
           fill=(255, 255, 255), font=f)

    d.text((100, 490), "CYAN – gaussian, 80 % opacity",
           fill=( 80, 220, 220), font=sf)
    d.text((370, 650), "MAGENTA – thin circle",
           fill=(220,  80, 220), font=sf)
    d.text((340, 1240), "ORANGE – varying pressure",
           fill=(220, 130,  60), font=sf)
    d.text((100, 1100), "WHITE – medium circle, 90 % opacity",
           fill=(210, 210, 210), font=sf)
    d.text((478, 945), "CENTER\n(0, 0)", fill=(180, 180, 180), font=sf)

    # Draw corner box
    d.line([CORNER_LINE_WIDTH,CORNER_LINE_WIDTH, W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, W - 1 - CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, H - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, CORNER_LINE_WIDTH], fill=(255, 255,  255), width=2)


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
            line_pts(ANNOTATION_WIDTH_P0[0]+i*40, ANNOTATION_WIDTH_P0[1], ANNOTATION_WIDTH_P0[0]+i*40, ANNOTATION_WIDTH_P0[1]+32, 2), W, H,
            rgba=[1.0, 0.24, 0.24, 1.0], brush_size=width,
        )
        ev += make_stroke(
            line_pts(ANNOTATION_WIDTH_P0[0]+i*40, ANNOTATION_WIDTH_P0[1] - 60, ANNOTATION_WIDTH_P0[0]+i*40, ANNOTATION_WIDTH_P0[1]+32 - 60, 2), W, H,
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
        line_pts(PORT_W - 1, 0, PORT_W - 1 - CORNER_LINE_WIDTH, CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )
    ev += make_stroke(
        line_pts(0, PORT_H - 1, CORNER_LINE_WIDTH, PORT_H - 1 - CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )
    ev += make_stroke(
        line_pts(PORT_W - 1, PORT_H - 1, PORT_W - 1 - CORNER_LINE_WIDTH, PORT_H - 1 - CORNER_LINE_WIDTH, 4), W, H,
        rgba=[0, 1, 0, 1.0], brush_size=line1pixel,
        varying_pressure=False,
    )

    return ev


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    landscape_path = os.path.join(SCRIPT_DIR, "landscape_testchart.png")
    portrait_path  = os.path.join(SCRIPT_DIR, "portrait_testchart.png")
    otio_path      = os.path.join(SCRIPT_DIR, "testchart_annotations.otio")

    print("Generating test images …")
    create_landscape_image(landscape_path)
    create_portrait_image(portrait_path)

    print("Building OTIO annotation file …")

    # ── Media ──────────────────────────────────────────────────────────────────
    media_land = ORIAnnotations.Media(
        name="landscape_testchart.png",
        media_path=landscape_path,
        frame_rate=24.0,
        duration=24,
        start_frame=0,
    )
    media_port = ORIAnnotations.Media(
        name="portrait_testchart.png",
        media_path=portrait_path,
        frame_rate=24.0,
        duration=24,
        start_frame=0,
    )

    # ── Landscape review item ──────────────────────────────────────────────────
    ri_land = ORIAnnotations.ReviewItem(media=media_land)
    frame_land = ORIAnnotations.ReviewItemFrame(
        review_item=ri_land,
        frame=1,
        note=(
            "**Landscape alignment test**\n\n"
            "Each annotation stroke traces the matching reference line in the image.\n\n"
            "| Stroke | Brush    | Size   | Opacity | Style              |\n"
            "|--------|----------|--------|---------|--------------------|\n"
            "| Red    | circle   | thick  | 100 %   | diagonal           |\n"
            "| Blue   | gaussian | medium |  70 %   | horizontal y=360   |\n"
            "| Green  | circle   | thin   |  85 %   | vertical x=1440    |\n"
            "| Yellow | circle   | medium | 100 %   | anti-diag, pressure|\n"
        ),
        annotation_image=landscape_path,
    )
    frame_land.annotation_commands = landscape_annotations()
    ri_land.review_frames = [frame_land]

    # ── Portrait review item ───────────────────────────────────────────────────
    ri_port = ORIAnnotations.ReviewItem(media=media_port)
    frame_port = ORIAnnotations.ReviewItemFrame(
        review_item=ri_port,
        frame=1,
        note=(
            "**Portrait alignment test**\n\n"
            "Each annotation stroke traces the matching reference line in the image.\n\n"
            "| Stroke  | Brush    | Size   | Opacity | Style              |\n"
            "|---------|----------|--------|---------|--------------------|\n"
            "| Cyan    | gaussian | medium |  80 %   | horizontal y=480   |\n"
            "| Magenta | circle   | thin   | 100 %   | vertical x=360     |\n"
            "| Orange  | circle   | medium | 100 %   | diagonal, pressure |\n"
            "| White   | circle   | medium |  90 %   | anti-diagonal      |\n"
        ),
        annotation_image=portrait_path,
    )
    frame_port.annotation_commands = portrait_annotations()
    ri_port.review_frames = [frame_port]

    # ── Review & group ─────────────────────────────────────────────────────────
    review = ORIAnnotations.Review(
        title="Test Chart Alignment Review",
        participants=["testchart_generator"],
        notes="Generated to verify annotation coordinate alignment across landscape and portrait formats.",
        review_items=[ri_land, ri_port],
    )

    rg = ORIAnnotations.ReviewGroup(
        media=[media_land, media_port],
        reviews=[review],
    )

    timeline = rg.export_otio_timeline()
    otio.adapters.write_to_file(timeline, otio_path)
    print(f"  Saved OTIO file       : {otio_path}")
    print("Done.")


if __name__ == "__main__":
    main()
