#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""
Bidirectional codec: xStudio pen-stroke dicts ↔ OTIO SyncEvent objects.

Both conversion directions are pure functions with no xStudio SDK dependency.
Callers are responsible for registering the SyncEvent schemadef in
``OTIO_PLUGIN_MANIFEST_PATH`` before importing this module.

.. rubric:: Coordinate systems

+------------------+------------------------------+--------+---------+
| System           | x range                      | y      | origin  |
+==================+==============================+========+=========+
| xStudio native   | ``[−1, +1]`` (W-normalised)  | down   | centre  |
+------------------+------------------------------+--------+---------+
| OTIO SyncEvent / | ``[−aspect/2, +aspect/2]``   | up     | centre  |
| RV paint         | (H-normalised)               |        |         |
+------------------+------------------------------+--------+---------+

Scale factor: ``aspect_half = W / (2 × H)``.
For 16:9 media: ``1920 / (2 × 1080) ≈ 0.8889``.

xStudio → OTIO: ``x_otio =  x_xs * aspect_half``,  ``y_otio = −y_xs * aspect_half``
OTIO → xStudio: ``x_xs   =  x_otio / aspect_half``, ``y_xs   = −y_otio / aspect_half``
"""

from __future__ import annotations

import uuid as _uuid_mod
from typing import List, Optional

import opentimelineio as otio

from otio_sync_core import coords, shapes
from otio_sync_core.manager import sync_event_schema

# --- xStudio unit conversions (owned here, NOT in coords) ------------------

#: OTIO ``TextAnnotation.font_size`` → xStudio caption ``font_size`` multiplier.
#:
#: Paired with ``RV_FONT_SCALE`` in ``rv_annotation_codec`` — the two must be
#: recalibrated together. When ``RV_FONT_SCALE`` was ``1080.0`` (both hosts'
#: native font-size units anchored to the same 1920x1080 reference frame),
#: this was ``1.0``. ``RV_FONT_SCALE`` was later divided by an empirical 2.5
#: (tuned against QPainter's real glyph output vs. a PIL testchart
#: reference — see its docstring), which was never mirrored here. Re-verified
#: empirically post-fix: rendering ``testchart_annotations.otio``'s
#: "96pt Font Size Sample Text" caption (``font_size=37.07``) into both hosts
#: and measuring on-screen glyph height as a fraction of frame height gave a
#: RV/xStudio ratio of ~2.54 with this at 1.0 — i.e. almost exactly
#: proportional to the un-mirrored ``/2.5``, confirming 2.5 restores parity.
XS_FONT_SCALE: float = 2.5

# Lazily resolved — requires OTIO_PLUGIN_MANIFEST_PATH to be populated first.
_SyncEvent = None


def _se():
    """Return the SyncEvent schemadef module, resolving it on first call."""
    global _SyncEvent
    if _SyncEvent is None:
        _SyncEvent = otio.schema.schemadef.module_from_name("SyncEvent")
    return _SyncEvent


# ---------------------------------------------------------------------------
# xStudio → OTIO (export / broadcast)
# ---------------------------------------------------------------------------

def xs_strokes_to_sync_events(
    pen_strokes: list,
    aspect_half: float,
    uuid_list: Optional[List[str]] = None,
) -> list:
    """Convert xStudio *pen_strokes* dicts to a sequence of OTIO SyncEvent objects.

    Each xStudio stroke dict becomes a ``PaintStart`` + ``PaintPoints`` pair.
    Point coordinates are converted from W-normalised / Y-down to
    H-normalised / Y-up by multiplying *x*/*y* by ``aspect_half`` /
    ``−aspect_half``.

    xStudio V4 stroke dicts use ``"colour": [r, g, b]`` and
    ``"type": "Brush"/"Pen"/"Erase"`` (the legacy V3 keys ``"r"``, ``"g"``,
    ``"b"`` and ``"is_erase_stroke"`` are automatically upgraded by xStudio's
    annotation deserialiser before they reach Python).

    :param pen_strokes: List of xStudio pen-stroke dicts as returned by
        ``Bookmark.annotation_data["Data"]["pen_strokes"]``.
    :param aspect_half: ``W / (2H)`` coordinate scale factor.
    :param uuid_list: Optional list of stable UUID strings, one per stroke.
        When given, ``uuid_list[i]`` is used for stroke *i* instead of a
        freshly generated UUID.  Pass this from ``_stroke_uuid_cache`` when
        repeated partial broadcasts of the same frame must share stable UUIDs.
    :returns: List of SyncEvent objects (interleaved ``PaintStart`` /
        ``PaintPoints`` entries).
    :rtype: list
    """
    se = _se()
    events: list = []
    for i, stroke in enumerate(pen_strokes):
        # Prefer existing stroke UUID if available.
        stroke_uuid = stroke.get("uuid") or (
            uuid_list[i]
            if uuid_list and i < len(uuid_list)
            else str(_uuid_mod.uuid4())
        )
        # xStudio V4 stores colour as a 3-element array under "colour".
        # Legacy V3 used separate "r", "g", "b" keys — keep as fallback.
        colour = stroke.get("colour")
        if isinstance(colour, (list, tuple)) and len(colour) >= 3:
            r, g, b = float(colour[0]), float(colour[1]), float(colour[2])
        else:
            r = float(stroke.get("r", 1.0))
            g = float(stroke.get("g", 1.0))
            b = float(stroke.get("b", 1.0))
        rgba = [r, g, b, float(stroke.get("opacity", 1.0))]
        thickness = stroke.get("thickness", 0.003)
        # xStudio V4 "type" is "Brush", "Pen", or "Erase".
        # Legacy V3 used "is_erase_stroke": bool.
        stroke_type = stroke.get("type", "Brush")
        is_erase = stroke_type == "Erase" or stroke.get("is_erase_stroke", False)
        raw_pts = stroke.get("points", [])

        xs_coords = [x * aspect_half for x in raw_pts[0::4]]
        ys_coords = [-y * aspect_half for y in raw_pts[1::4]]
        sps = raw_pts[2::4]
        widths = (
            [2.0 * thickness * aspect_half * sp for sp in sps]
            if xs_coords and any(sp != 0.0 for sp in sps)
            else [2.0 * thickness * aspect_half] * len(xs_coords)
        )

        start_evt = se.PaintStart(
            brush="oval", rgba=rgba, friendly_name="", uuid=stroke_uuid
        )
        if is_erase:
            start_evt.type = "erase"
        events.append(start_evt)
        events.append(
            se.PaintPoints(
                uuid=stroke_uuid,
                points=se.PaintVertices(list(xs_coords), list(ys_coords), widths),
            )
        )
    return events


def xs_captions_to_sync_events(
    captions: list,
    aspect_half: float,
    existing_uuids: Optional[List[str]] = None,
) -> list:
    """Convert xStudio *captions* dicts to a sequence of OTIO SyncEvent objects.

    :param captions: List of xStudio caption dicts from
        ``Bookmark.annotation_data["Data"]["captions"]``.
    :param aspect_half: ``W / (2H)`` coordinate scale factor.
    :param existing_uuids: When provided, reuse these UUID strings (by index)
        instead of generating fresh ones.  Pass the existing UUIDs from the
        OTIO clip when building a replacement command list so that RV can
        update text nodes in place.
    :returns: List of ``TextAnnotation`` SyncEvent objects.
    :rtype: list
    """
    se = _se()
    events: list = []
    for i, caption in enumerate(captions):
        caption_uuid = (
            existing_uuids[i]
            if existing_uuids and i < len(existing_uuids)
            else str(_uuid_mod.uuid4())
        )
        colour = caption.get("colour", ["colour", 1, 1.0, 1.0, 1.0])
        if isinstance(colour, list) and len(colour) >= 5:
            r, g, b = float(colour[2]), float(colour[3]), float(colour[4])
        else:
            r, g, b = 1.0, 1.0, 1.0
        opacity = float(caption.get("opacity", 1.0))
        pos = caption.get("position", ["vec2", 1, 0.0, 0.0])
        position = (
            [float(pos[2]) * aspect_half, -float(pos[3]) * aspect_half]
            if isinstance(pos, list) and len(pos) >= 4
            else [0.0, 0.0]
        )
        font_name = caption.get("font_name", "")

        events.append(
            se.TextAnnotation(
                rgba=[r, g, b, opacity],
                position=position,
                spacing=coords.DEFAULT_SPACING,
                friendly_name=font_name,
                font_size=float(caption.get("font_size", coords.DEFAULT_FONT_SIZE)) / XS_FONT_SCALE,
                font=font_name,
                text=caption.get("text", ""),
                rotation=0.0,
                scale=1.0,
                uuid=caption_uuid,
            )
        )
    return events


# ---------------------------------------------------------------------------
# OTIO → xStudio (import / receive)
# ---------------------------------------------------------------------------

def sync_events_to_xs_strokes(commands: list, aspect_half: float) -> list:
    """Convert a ``PaintStart`` / ``PaintPoints`` command sequence to xStudio stroke dicts.

    Inverts the H-normalised / Y-up (OTIO/RV) coordinate system back to the
    W-normalised / Y-down system that xStudio expects:

    .. code-block:: text

        x_xs = x_otio / aspect_half
        y_xs = −y_otio / aspect_half

    :param commands: Sequence of SyncEvent objects from an annotation clip
        (``PaintStart``, ``PaintPoints``, and ``TextAnnotation`` entries are
        all accepted; only the paint entries are processed here).
    :param aspect_half: ``W / (2H)`` derived from the target media resolution.
    :returns: List of xStudio pen-stroke dicts suitable for
        ``Bookmark.set_annotation(strokes=...)``.
    :rtype: list
    """
    pen_strokes: list = []
    current_stroke: dict | None = None

    import json
    for cmd in commands:
        if not isinstance(cmd, dict) and not hasattr(cmd, "rgba") and not hasattr(cmd, "points") and not hasattr(cmd, "min") and not hasattr(cmd, "start"):
            try:
                cmd = json.loads(otio.adapters.write_to_string(cmd, "otio_json"))
            except Exception:
                pass

        schema = sync_event_schema(cmd)

        is_ellipse = schema.startswith("EllipseAnnotation")
        is_rect = schema.startswith("RectangleAnnotation")
        is_arrow = schema.startswith("ArrowAnnotation")

        if is_ellipse or is_rect or is_arrow:
            rgba = cmd.get("rgba") if isinstance(cmd, dict) else getattr(cmd, "rgba", None)
            if rgba is None:
                rgba = [1.0, 1.0, 1.0, 1.0]
            rgba = list(rgba)
            r_val = rgba[0] if len(rgba) > 0 else 1.0
            g_val = rgba[1] if len(rgba) > 1 else 1.0
            b_val = rgba[2] if len(rgba) > 2 else 1.0
            opacity = rgba[3] if len(rgba) > 3 else 1.0
            
            size = cmd.get("size") if isinstance(cmd, dict) else getattr(cmd, "size", 2.0)
            thickness = size / (2.0 * aspect_half)
            
            # Shape geometry → OTIO-norm polyline via the shared host-neutral
            # tessellation helper (see otio_sync_core.shapes).
            pts_list = []
            if is_rect:
                min_val = cmd.get("min") if isinstance(cmd, dict) else getattr(cmd, "min", [0.0, 0.0])
                max_val = cmd.get("max") if isinstance(cmd, dict) else getattr(cmd, "max", [0.0, 0.0])
                pts_list = shapes.rect_polyline(min_val, max_val)
            elif is_ellipse:
                min_val = cmd.get("min") if isinstance(cmd, dict) else getattr(cmd, "min", [0.0, 0.0])
                max_val = cmd.get("max") if isinstance(cmd, dict) else getattr(cmd, "max", [0.0, 0.0])
                pts_list = shapes.ellipse_polyline(min_val, max_val)
            elif is_arrow:
                start_val = cmd.get("start") if isinstance(cmd, dict) else getattr(cmd, "start", [0.0, 0.0])
                end_val = cmd.get("end") if isinstance(cmd, dict) else getattr(cmd, "end", [0.0, 0.0])
                pts_list = shapes.arrow_polyline(start_val, end_val)

            raw_pts = []
            for x, y in pts_list:
                raw_pts.extend([
                    x / aspect_half,
                    -y / aspect_half,
                    1.0,  # pressure
                    1.0,  # opacity
                ])
                
            stroke_uuid = cmd.get("uuid") if isinstance(cmd, dict) else getattr(cmd, "uuid", None)
            if not stroke_uuid:
                import uuid
                stroke_uuid = str(uuid.uuid4())
                
            pen_strokes.append({
                "colour": [r_val, g_val, b_val],
                "r": r_val,
                "g": g_val,
                "b": b_val,
                "opacity": opacity,
                "thickness": thickness,
                "softness": 0.0,
                "size_sensitivity": 1.0,
                "opacity_sensitivity": 1.0,
                "type": "Brush",
                "is_erase_stroke": False,
                "points": raw_pts,
                "uuid": stroke_uuid
            })
        elif schema.startswith("PaintStart"):
            # Tolerate both live OTIO schemadef objects and raw deserialised dicts.
            rgba = getattr(cmd, "rgba", None)
            if rgba is None and isinstance(cmd, dict):
                rgba = cmd.get("rgba")
            if not rgba:
                rgba = [1.0, 1.0, 1.0, 1.0]
            rgba = list(rgba)  # AnyVector → plain list so len/index work reliably

            # OTIO SyncEvent type: "color" (normal) or "erase".
            cmd_type = getattr(cmd, "type", None)
            if cmd_type is None and isinstance(cmd, dict):
                cmd_type = cmd.get("type", "color")
            is_erase = (cmd_type or "color") == "erase"

            # Read the brush field to determine if this is a Gaussian soft brush.
            # A brush of "gaussian" or "gauss" maps to xStudio softness=1.0,
            # which drives soft_edge = thickness * softness in the stroke shader.
            brush_name = getattr(cmd, "brush", None)
            if brush_name is None and isinstance(cmd, dict):
                brush_name = cmd.get("brush", "oval")
            brush_name = (brush_name or "oval").lower()
            is_gaussian = brush_name in ("gaussian", "gauss")
            softness = 1.0 if is_gaussian else 0.0

            r_val = rgba[0] if len(rgba) > 0 else 1.0
            g_val = rgba[1] if len(rgba) > 1 else 1.0
            b_val = rgba[2] if len(rgba) > 2 else 1.0
            # PaintStart carries no width field; thickness is set from PaintVertices.size.
            # Populating both legacy (r, g, b, is_erase_stroke) and modern (colour, type)
            # formats ensures compatibility across all xStudio versions.
            current_stroke = {
                "colour": [r_val, g_val, b_val],
                "r": r_val,
                "g": g_val,
                "b": b_val,
                "opacity": rgba[3] if len(rgba) > 3 else 1.0,
                "thickness": 0.003,
                "softness": softness,
                "size_sensitivity": 1.0,
                "opacity_sensitivity": 1.0,
                "type": "Erase" if is_erase else "Brush",
                "is_erase_stroke": is_erase,
                "points": [],
            }
            # Preserve UUID for sync matching
            stroke_uuid = getattr(cmd, "uuid", None)
            if stroke_uuid is None and isinstance(cmd, dict):
                stroke_uuid = cmd.get("uuid")
            if stroke_uuid:
                current_stroke["uuid"] = stroke_uuid

            pen_strokes.append(current_stroke)

        # Python class is PaintPoints; serialised label is "PaintPoint.1".
        elif schema.startswith("PaintPoint") and current_stroke is not None:
            points_obj = getattr(cmd, "points", None)
            if points_obj is None and isinstance(cmd, dict):
                points_obj = cmd.get("points")
            if points_obj is None:
                continue

            if isinstance(points_obj, dict):
                xs_in = list(points_obj.get("x", []))
                ys_in = list(points_obj.get("y", []))
                sizes = list(points_obj.get("size", []))
            else:
                xs_in = list(getattr(points_obj, "x", []))
                ys_in = list(getattr(points_obj, "y", []))
                sizes = list(getattr(points_obj, "size", []))

            # Base thickness T
            if sizes:
                base_size = max(sizes)
                thickness = base_size / (2.0 * aspect_half)
            else:
                base_size = 0.0
                thickness = 0.003 / 2.0

            if is_gaussian:
                # Scale down xStudio gaussian brush to better match RV's apparent soft stroke size
                thickness *= 0.75

            current_stroke["thickness"] = thickness if thickness > 0.0 else 0.003 / 2.0

            raw_pts: list = []
            for idx, (x, y) in enumerate(zip(xs_in, ys_in)):
                if base_size > 0.0 and idx < len(sizes):
                    size_pressure = sizes[idx] / base_size
                else:
                    size_pressure = 1.0

                raw_pts.extend([
                    x / aspect_half,
                    -y / aspect_half,
                    size_pressure,
                    1.0,  # opacity_pressure
                ])
            current_stroke["points"] = raw_pts

    return pen_strokes


def sync_events_to_xs_captions(commands: list, aspect_half: float) -> list:
    """Convert ``TextAnnotation`` SyncEvent objects to xStudio caption dicts.

    :param commands: Sequence of SyncEvent objects; only ``TextAnnotation``
        entries are processed.
    :param aspect_half: ``W / (2H)`` coordinate scale factor.
    :returns: List of xStudio caption dicts suitable for
        ``Bookmark.set_annotation(captions=...)``.
    :rtype: list
    """
    captions: list = []
    import json
    for cmd in commands:
        if not isinstance(cmd, dict) and not hasattr(cmd, "rgba") and not hasattr(cmd, "position"):
            try:
                cmd = json.loads(otio.adapters.write_to_string(cmd, "otio_json"))
            except Exception:
                pass

        schema = sync_event_schema(cmd)
        if not schema.startswith("TextAnnotation"):
            continue

        # Tolerate both live OTIO schemadef objects and raw deserialised dicts.
        def _get(attr: str, default):
            val = getattr(cmd, attr, None)
            if val is None and isinstance(cmd, dict):
                val = cmd.get(attr)
            return val if val is not None else default

        rgba = _get("rgba", [1.0, 1.0, 1.0, 1.0]) or [1.0, 1.0, 1.0, 1.0]
        position = _get("position", [0.0, 0.0]) or [0.0, 0.0]
        text = _get("text", "") or ""
        font = _get("font", "") or ""
        font_size = float(_get("font_size", coords.DEFAULT_FONT_SIZE) or coords.DEFAULT_FONT_SIZE)
        # xStudio has no per-caption scale field (see "TextAnnotation Scale
        # Round-Trip" in otio-annotation-sync), so a host that DOES have one
        # (RV, whose interactive text tool resizes via this field rather than
        # `size` — its drag-to-resize handle drives `scale`, not `font_size`)
        # must have that multiplier folded into font_size here, or it is
        # silently dropped and the caption renders at the host's on-screen
        # size as if scale were always 1.0 — verified empirically to produce
        # a ~10x size mismatch for a real RV-drawn caption with scale=0.1.
        scale = float(_get("scale", 1.0) or 1.0)
        uuid_val = _get("uuid", "") or ""

        # xStudio requires a valid font name to render text; default to one of its built-ins
        if not font:
            font = "Overpass Regular"

        x_xs = float(position[0]) / aspect_half
        y_xs = -float(position[1]) / aspect_half

        cap_dict = {
            "colour": ["colour", 1, rgba[0], rgba[1], rgba[2]],
            "opacity": rgba[3] if len(rgba) > 3 else 1.0,
            "position": ["vec2", 1, x_xs, y_xs],
            "font_name": font,
            "font_size": font_size * scale * XS_FONT_SCALE,
            "text": text,
            "wrap_width": 1.5,
            "justification": 0,
            "background_colour": ["colour", 1, 0.0, 0.0, 0.0],
            "background_opacity": 0.5,
        }
        if uuid_val:
            cap_dict["uuid"] = uuid_val
        captions.append(cap_dict)
    return captions


# ---------------------------------------------------------------------------
# D9 common contract entry points (host-agnostic tooling; see design.md)
# ---------------------------------------------------------------------------

HOST_ID = "xstudio"

#: SyncEvent kinds xStudio renders natively. Shapes (ellipse/rect/arrow) are
#: NOT in this set — xStudio has no shape primitives, so
#: :func:`sync_events_to_xs_strokes` already tessellates them into stroke
#: polylines via :mod:`otio_sync_core.shapes` before this point is reached.
SUPPORTED_KINDS = frozenset({"pen", "erase", "text"})


def from_sync_events(events: list, ctx: Optional[dict] = None) -> dict:
    """Hub → host: SyncEvents → xStudio's native ``{"strokes", "captions"}`` dict.

    The dict shape matches ``Bookmark.set_annotation(strokes=..., captions=...)``
    — xStudio's single-handoff API — so callers can pass the result straight
    through without unpacking.

    :param events: SyncEvent objects (or serialised dicts) for one frame.
    :param ctx: Optional context; ``ctx["aspect_half"]`` overrides the default.
    :returns: ``{"strokes": [...], "captions": [...]}``.
    """
    aspect_half = (ctx or {}).get("aspect_half", coords.DEFAULT_ASPECT_HALF)
    return {
        "strokes": sync_events_to_xs_strokes(events, aspect_half),
        "captions": sync_events_to_xs_captions(events, aspect_half),
    }


def to_sync_events(native: dict, ctx: Optional[dict] = None) -> list:
    """Host → hub: xStudio's native ``{"strokes", "captions"}`` dict → SyncEvents.

    :param native: ``{"strokes": [...], "captions": [...]}`` as read from
        ``Bookmark.annotation_data["Data"]``.
    :param ctx: Optional context; ``ctx["aspect_half"]`` overrides the default,
        ``ctx["uuid_list"]``/``ctx["existing_uuids"]`` thread through to the
        underlying stroke/caption converters for stable-uuid partial rebroadcasts.
    :returns: Flat list of SyncEvent objects (strokes then captions).
    """
    aspect_half = (ctx or {}).get("aspect_half", coords.DEFAULT_ASPECT_HALF)
    uuid_list = (ctx or {}).get("uuid_list") if ctx else None
    existing_uuids = (ctx or {}).get("existing_uuids") if ctx else None
    return (
        xs_strokes_to_sync_events(native.get("strokes", []), aspect_half, uuid_list=uuid_list)
        + xs_captions_to_sync_events(native.get("captions", []), aspect_half, existing_uuids=existing_uuids)
    )
