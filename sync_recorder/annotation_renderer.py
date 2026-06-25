# SPDX-License-Identifier: Apache-2.0
"""Renderer to draw OTIO SyncEvent annotations onto transparent canvases using Pillow."""

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import opentimelineio as otio

from otio_sync_core.manager import sync_event_schema
from otio_sync_core.annotation_builder import norm_to_px


def _load_font(font_name: str, pil_size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a true-type font at the specified size with fallbacks."""
    for path in (
        font_name,
        f"/System/Library/Fonts/{font_name}.ttc",
        f"/System/Library/Fonts/{font_name}.ttf",
        f"/System/Library/Fonts/Supplemental/{font_name}.ttf",
        f"/System/Library/Fonts/Supplemental/{font_name}.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(path, int(round(pil_size)))
        except Exception:
            pass
    return ImageFont.load_default()


def render_annotations(
    commands: list[Any],
    width: int,
    height: int,
) -> Image.Image:
    """Render a list of OTIO SyncEvent annotation commands onto a transparent RGBA image.

    :param commands: List of SyncEvent objects or serialized dicts.
    :param width: Target image width in pixels.
    :param height: Target image height in pixels.
    :returns: Transparent RGBA Image containing the rendered annotations.
    :rtype: :class:`~PIL.Image.Image`
    """
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    active_strokes: dict[str, dict[str, Any]] = {}
    render_elements: list[dict[str, Any]] = []

    # 1. Parse and group events sequentially, maintaining creation order
    for cmd in commands:
        schema = sync_event_schema(cmd)
        if not schema:
            continue

        if schema.startswith("PaintStart"):
            uuid_val = getattr(cmd, "uuid", None)
            if uuid_val is None and isinstance(cmd, dict):
                uuid_val = cmd.get("uuid")
            if not uuid_val:
                continue

            rgba = getattr(cmd, "rgba", None)
            if rgba is None and isinstance(cmd, dict):
                rgba = cmd.get("rgba")
            if not rgba:
                rgba = [1.0, 1.0, 1.0, 1.0]
            rgba = [float(c) for c in rgba]

            brush = getattr(cmd, "brush", None)
            if brush is None and isinstance(cmd, dict):
                brush = cmd.get("brush")
            brush = (brush or "circle").lower()

            visible = getattr(cmd, "visible", None)
            if visible is None and isinstance(cmd, dict):
                visible = cmd.get("visible", True)

            type_val = getattr(cmd, "type", None)
            if type_val is None and isinstance(cmd, dict):
                type_val = cmd.get("type", "color")

            stroke = {
                "type": "stroke",
                "uuid": uuid_val,
                "rgba": rgba,
                "brush": brush,
                "visible": visible,
                "stroke_type": type_val,
                "points": [],
            }
            active_strokes[uuid_val] = stroke
            render_elements.append(stroke)

        elif schema.startswith("PaintPoint") or schema.startswith("PaintVertices"):
            uuid_val = getattr(cmd, "uuid", None)
            if uuid_val is None and isinstance(cmd, dict):
                uuid_val = cmd.get("uuid")
            if not uuid_val or uuid_val not in active_strokes:
                continue

            stroke = active_strokes[uuid_val]
            points_field = getattr(cmd, "points", None)
            if points_field is None and isinstance(cmd, dict):
                points_field = cmd.get("points")
            if points_field is None:
                continue

            if isinstance(points_field, dict):
                xs = list(points_field.get("x", []))
                ys = list(points_field.get("y", []))
                sizes = list(points_field.get("size", []))
            else:
                xs = list(getattr(points_field, "x", []))
                ys = list(getattr(points_field, "y", []))
                sizes = list(getattr(points_field, "size", []))

            for x_val, y_val, size_val in zip(xs, ys, sizes):
                stroke["points"].append((float(x_val), float(y_val), float(size_val)))

        elif schema.startswith("TextAnnotation"):
            uuid_val = getattr(cmd, "uuid", None)
            if uuid_val is None and isinstance(cmd, dict):
                uuid_val = cmd.get("uuid")

            rgba = getattr(cmd, "rgba", None)
            if rgba is None and isinstance(cmd, dict):
                rgba = cmd.get("rgba")
            if not rgba:
                rgba = [1.0, 1.0, 1.0, 1.0]
            rgba = [float(c) for c in rgba]

            position = getattr(cmd, "position", None)
            if position is None and isinstance(cmd, dict):
                position = cmd.get("position")
            if not position:
                position = [0.0, 0.0]
            position = [float(p) for p in position]

            text = getattr(cmd, "text", None)
            if text is None and isinstance(cmd, dict):
                text = cmd.get("text")
            text = text or ""

            font_size = getattr(cmd, "font_size", None)
            if font_size is None and isinstance(cmd, dict):
                font_size = cmd.get("font_size")
            font_size = float(font_size) if font_size is not None else 50.0

            font = getattr(cmd, "font", None)
            if font is None and isinstance(cmd, dict):
                font = cmd.get("font")
            font = font or ""

            text_element = {
                "type": "text",
                "uuid": uuid_val,
                "rgba": rgba,
                "position": position,
                "text": text,
                "font_size": font_size,
                "font": font,
            }
            render_elements.append(text_element)

    # 2. Render each element onto the canvas in sequential order
    for elem in render_elements:
        if elem["type"] == "stroke":
            if not elem["visible"] or not elem["points"]:
                continue

            rgba = elem["rgba"]
            brush = elem["brush"]
            stroke_type = elem["stroke_type"]
            points = elem["points"]

            # Set up target color
            color_tuple = (
                int(round(rgba[0] * 255)),
                int(round(rgba[1] * 255)),
                int(round(rgba[2] * 255)),
                255,
            )

            is_soft = brush in ("gaussian", "gauss")
            is_erase = stroke_type == "erase"

            # Create temporary stroke layer
            stroke_im = Image.new("RGBA", (width, height), (0, 0, 0, 0))

            if is_soft:
                # Soft brush rendering via blurred L-mode mask
                mask = Image.new("L", (width, height), 0)
                mask_draw = ImageDraw.Draw(mask)

                # Draw solid path on mask first
                for i, p in enumerate(points):
                    px, py = norm_to_px(p[0], p[1], width, height)
                    r = max(0.0, p[2] * height / 2.0)
                    mask_draw.ellipse([px - r, py - r, px + r, py + r], fill=255)
                    if i > 0:
                        prev_p = points[i - 1]
                        ppx, ppy = norm_to_px(prev_p[0], prev_p[1], width, height)
                        pr = max(0.0, prev_p[2] * height / 2.0)
                        mask_draw.ellipse([ppx - pr, ppy - pr, ppx + pr, ppy + pr], fill=255)
                        mask_draw.line(
                            [ppx, ppy, px, py],
                            fill=255,
                            width=int(round(r + pr)),
                        )

                # Apply Gaussian Blur to smooth the stroke edges
                avg_size = sum(p[2] for p in points) / len(points)
                blur_rad = max(1.0, (avg_size * height) / 3.0)
                mask = mask.filter(ImageFilter.GaussianBlur(blur_rad))

                # Paste color through blurred mask
                color_im = Image.new("RGBA", (width, height), color_tuple)
                stroke_im.paste(color_im, mask=mask)
            else:
                # Solid brush rendering directly on temporary RGBA image
                stroke_draw = ImageDraw.Draw(stroke_im)
                for i, p in enumerate(points):
                    px, py = norm_to_px(p[0], p[1], width, height)
                    r = max(0.0, p[2] * height / 2.0)
                    stroke_draw.ellipse([px - r, py - r, px + r, py + r], fill=color_tuple)
                    if i > 0:
                        prev_p = points[i - 1]
                        ppx, ppy = norm_to_px(prev_p[0], prev_p[1], width, height)
                        pr = max(0.0, prev_p[2] * height / 2.0)
                        stroke_draw.ellipse([ppx - pr, ppy - pr, ppx + pr, ppy + pr], fill=color_tuple)
                        stroke_draw.line(
                            [ppx, ppy, px, py],
                            fill=color_tuple,
                            width=int(round(r + pr)),
                        )

            # Apply overall stroke opacity
            stroke_opacity = rgba[3]
            r_chan, g_chan, b_chan, a_chan = stroke_im.split()
            a_chan = a_chan.point(lambda p: int(round(p * stroke_opacity)))
            stroke_im = Image.merge("RGBA", (r_chan, g_chan, b_chan, a_chan))

            # Composite onto main canvas
            if is_erase:
                # Eraser composite clears pixels based on stroke mask intensity
                transparent_im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                canvas = Image.composite(transparent_im, canvas, a_chan)
            else:
                canvas = Image.alpha_composite(canvas, stroke_im)

        elif elem["type"] == "text":
            rgba = elem["rgba"]
            position = elem["position"]
            text_str = elem["text"]
            font_size = elem["font_size"]
            font_name = elem["font"]

            if not text_str:
                continue

            color_tuple = (
                int(round(rgba[0] * 255)),
                int(round(rgba[1] * 255)),
                int(round(rgba[2] * 255)),
                int(round(rgba[3] * 255)),
            )

            px, py = norm_to_px(position[0], position[1], width, height)
            pil_size = max(1.0, font_size * height / 417.0)
            font_obj = _load_font(font_name, pil_size)

            # Render text on transparent stroke image to support proper alpha compositing
            text_im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            text_draw = ImageDraw.Draw(text_im)

            try:
                text_draw.text((px, py), text_str, fill=color_tuple, font=font_obj, anchor="ls")
            except (AttributeError, ValueError):
                # Fall back to default anchor alignment if the font/PIL doesn't support middle anchors
                text_draw.text((px, py), text_str, fill=color_tuple, font=font_obj)

            canvas = Image.alpha_composite(canvas, text_im)

    return canvas
