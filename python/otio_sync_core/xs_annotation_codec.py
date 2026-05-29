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

from otio_sync_core.manager import sync_event_schema

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
        events.append(
            se.TextAnnotation(
                rgba=[r, g, b, opacity],
                position=position,
                spacing=0.0,
                friendly_name=caption.get("font_name", ""),
                font_size=float(caption.get("font_size", 50.0)),
                font=caption.get("font_name", ""),
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
        if not isinstance(cmd, dict) and not hasattr(cmd, "rgba") and not hasattr(cmd, "points"):
            try:
                cmd = json.loads(otio.adapters.write_to_string(cmd, "otio_json"))
            except Exception:
                pass

        schema = sync_event_schema(cmd)

        if schema.startswith("PaintStart"):
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
                "softness": 0.0,
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
        font_size = float(_get("font_size", 50.0) or 50.0)

        x_xs = float(position[0]) / aspect_half
        y_xs = -float(position[1]) / aspect_half

        captions.append({
            "colour": ["colour", 1, rgba[0], rgba[1], rgba[2]],
            "opacity": rgba[3] if len(rgba) > 3 else 1.0,
            "position": ["vec2", 1, x_xs, y_xs],
            "font_name": font,
            "font_size": font_size,
            "text": text,
            "wrap_width": 0.0,
            "justification": 0,
        })
    return captions
