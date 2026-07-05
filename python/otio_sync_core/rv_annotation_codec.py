#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Bidirectional codec: OTIO SyncEvent objects ↔ RV paint-node properties.

This is the sole authoritative implementation of the ``SyncEvent`` ⇄ RV
paint-node mapping. The forward direction (:func:`sync_events_to_rv_specs`) is
a **pure** function — it imports no ``rv.commands`` and returns a list of
:data:`PaintNodeSpec` dicts describing the paint child nodes to create. The
thin RV-touching applier that writes those specs lives in
:mod:`otio_sync_core.rv_paint_applier`.

.. rubric:: Coordinate space

RV paint and OTIO ``SyncEvent`` share the same OTIO-normalized space
(H-normalised, Y-up, centre origin — see :mod:`otio_sync_core.coords`), so
point coordinates pass through **unchanged**. Only host-specific *unit*
conversions are applied here (RV owns these, mirroring how the xStudio codec
owns xStudio's font factor):

* font size: ``TextAnnotation.font_size / RV_FONT_SCALE`` → RV ``.size``
* pen width: ``PaintVertices.size * RV_WIDTH_SCALE`` → RV ``.width``

.. rubric:: PaintNodeSpec

Each spec fully describes one RV paint child node, independent of the target
paint node or the assigned strokeid::

    {
      "kind":  "pen" | "erase" | "text" | "ellipse" | "rect" | "arrow",
      "uuid":  str,
      "user":  str,                       # embedded in the node name
      "props": [ (name, rv_type, value, dim), ... ],  # rv_type in TYPE_*
    }
"""

from __future__ import annotations

import uuid as _uuid_mod
from typing import Any, Dict, List, Optional

import opentimelineio as otio

from otio_sync_core import coords, shapes

_SyncEvent = None


def _se():
    """Return the SyncEvent schemadef module, resolving it on first call."""
    global _SyncEvent
    if _SyncEvent is None:
        _SyncEvent = otio.schema.schemadef.module_from_name("SyncEvent")
    return _SyncEvent

# --- Identity & capability (D9 common contract) ---------------------------

HOST_ID: str = "rv"

#: SyncEvent kinds RV renders natively (as first-class paint nodes). Kinds not
#: in this set are degraded to stroke polylines via the shared tessellation.
SUPPORTED_KINDS = frozenset({"pen", "erase", "text", "ellipse", "rect", "arrow"})

# --- RV unit conversions (owned here, NOT in coords) ----------------------

#: OTIO ``TextAnnotation.font_size`` → RV text-node ``.size`` divisor.
RV_FONT_SCALE: float = 5000.0

#: ``PaintVertices.size`` → RV pen ``.width`` multiplier.
RV_WIDTH_SCALE: float = 0.6

# --- RV property type tags (mapped to commands.*Type by the applier) ------

TYPE_STRING = "string"
TYPE_FLOAT = "float"
TYPE_INT = "int"

PaintNodeSpec = Dict[str, Any]


def font_size_to_rv(font_size: float) -> float:
    """Convert OTIO ``font_size`` to the RV text-node ``.size`` value."""
    return float(font_size) / RV_FONT_SCALE


def rv_to_font_size(rv_size: float) -> float:
    """Convert an RV text-node ``.size`` value back to OTIO ``font_size``."""
    return float(rv_size) * RV_FONT_SCALE


# --- Helpers --------------------------------------------------------------

def _schema(ev: Any) -> str:
    """Return the SyncEvent schema name, tolerant of live objects and dicts.

    Uses ``schema_name()`` (never ``isinstance``) so classification survives a
    double-loaded SyncEvent schemadef, which silently breaks ``isinstance``.
    """
    fn = getattr(ev, "schema_name", None)
    if callable(fn):
        try:
            return ev.schema_name()
        except Exception:
            pass
    # Fallback for raw deserialised dicts (e.g. "PaintStart.1"). Kept inline so
    # the codec has no dependency on the heavier sync ``manager`` module.
    if isinstance(ev, dict):
        return ev.get("OTIO_SCHEMA", "")
    return ""


def _get(ev: Any, attr: str, default: Any = None) -> Any:
    val = getattr(ev, attr, None)
    if val is None and isinstance(ev, dict):
        val = ev.get(attr)
    return default if val is None else val


def _user(friendly_name: Any) -> str:
    if not friendly_name:
        return "user"
    return str(friendly_name).split(":")[-1] or "user"


# --- Parse: events → intermediate strokes ---------------------------------

def _parse_events(events: List[Any]) -> List[dict]:
    """Fold a flat SyncEvent list into intermediate stroke dicts (one per node)."""
    strokes: List[dict] = []
    by_uuid: Dict[str, dict] = {}

    for ev in events:
        schema = _schema(ev)

        if schema.startswith("PaintStart"):
            is_erase = (_get(ev, "type", "color") or "color") == "erase"
            stroke = {
                "kind": "erase" if is_erase else "pen",
                "rgba": [float(x) for x in list(_get(ev, "rgba", [1.0, 1.0, 1.0, 1.0]))],
                "brush": _get(ev, "brush", "oval") or "oval",
                "user": _user(_get(ev, "friendly_name", "")),
                "uuid": _get(ev, "uuid", "") or "",
                "width": [],
                "points": [],
            }
            by_uuid[stroke["uuid"]] = stroke
            strokes.append(stroke)

        elif schema.startswith("PaintPoint"):
            stroke = by_uuid.get(_get(ev, "uuid", "") or "")
            pts = _get(ev, "points")
            if stroke is not None and pts is not None:
                xs, ys, sizes = _points_xyz(pts)
                stroke["width"] = list(sizes)
                stroke["points"] = [v for pair in zip(xs, ys) for v in pair]

        elif schema.startswith("PaintEnd"):
            stroke = by_uuid.get(_get(ev, "uuid", "") or "")
            pts = _get(ev, "points")
            if stroke is not None and pts is not None:
                xs, ys, sizes = _points_xyz(pts)
                stroke["width"].extend(sizes)
                stroke["points"].extend([v for pair in zip(xs, ys) for v in pair])

        elif schema.startswith("TextAnnotation"):
            strokes.append({
                "kind": "text",
                "rgba": [float(x) for x in list(_get(ev, "rgba", [1.0, 1.0, 1.0, 1.0]))],
                "position": [float(x) for x in list(_get(ev, "position", [0.0, 0.0]))],
                "spacing": _get(ev, "spacing", 0.0),
                "font_size": float(_get(ev, "font_size", coords.DEFAULT_FONT_SIZE)),
                "font": _get(ev, "font", "") or "",
                "text": _get(ev, "text", "") or "",
                "scale": _get(ev, "scale", 1.0),
                "rotation": _get(ev, "rotation", 0.0),
                "user": _user(_get(ev, "friendly_name", "")),
                "uuid": _get(ev, "uuid", "") or "",
            })

        elif schema.startswith("EllipseAnnotation") or schema.startswith("RectangleAnnotation"):
            strokes.append({
                "kind": "ellipse" if schema.startswith("Ellipse") else "rect",
                "min": [float(x) for x in list(_get(ev, "min", [0.0, 0.0]))],
                "max": [float(x) for x in list(_get(ev, "max", [0.0, 0.0]))],
                "rgba": [float(x) for x in list(_get(ev, "rgba", [1.0, 1.0, 1.0, 1.0]))],
                "inner_rgba": [float(x) for x in list(_get(ev, "inner_rgba", [0.0, 0.0, 0.0, 0.0]))],
                "size": float(_get(ev, "size", 2.0)),
                "user": _user(_get(ev, "friendly_name", "")),
                "uuid": _get(ev, "uuid", "") or "",
            })

        elif schema.startswith("ArrowAnnotation"):
            strokes.append({
                "kind": "arrow",
                "start": [float(x) for x in list(_get(ev, "start", [0.0, 0.0]))],
                "end": [float(x) for x in list(_get(ev, "end", [0.0, 0.0]))],
                "rgba": [float(x) for x in list(_get(ev, "rgba", [1.0, 1.0, 1.0, 1.0]))],
                "size": float(_get(ev, "size", 2.0)),
                "user": _user(_get(ev, "friendly_name", "")),
                "uuid": _get(ev, "uuid", "") or "",
            })

    return strokes


def _points_xyz(pts: Any):
    if isinstance(pts, dict):
        return list(pts.get("x", [])), list(pts.get("y", [])), list(pts.get("size", []))
    return (list(getattr(pts, "x", [])),
            list(getattr(pts, "y", [])),
            list(getattr(pts, "size", [])))


# --- Convert: intermediate strokes → PaintNodeSpec ------------------------

def _pen_spec(stroke: dict) -> PaintNodeSpec:
    brush_name = "gauss" if str(stroke["brush"]).lower() in ("gauss", "gaussian") else "circle"
    width = stroke["width"] or []
    props = [
        ("brush", TYPE_STRING, [brush_name], 1),
        ("color", TYPE_FLOAT, list(stroke["rgba"]), 4),
        ("debug", TYPE_INT, [0], 1),
        ("join", TYPE_INT, [3], 1),
        ("cap", TYPE_INT, [1], 1),
        ("splat", TYPE_INT, [1 if brush_name == "gauss" else 0], 1),
    ]
    if stroke["kind"] == "erase":
        props.append(("mode", TYPE_INT, [1], 1))
    props.append(("width", TYPE_FLOAT, [w * RV_WIDTH_SCALE for w in width], 1))
    props.append(("points", TYPE_FLOAT, list(stroke["points"]), 2))
    return {"kind": stroke["kind"], "uuid": stroke["uuid"], "user": stroke["user"], "props": props}


def _text_spec(stroke: dict, frame: int) -> PaintNodeSpec:
    spacing = stroke["spacing"] if stroke["spacing"] else coords.DEFAULT_SPACING
    props = [
        ("position", TYPE_FLOAT, list(stroke["position"]), 2),
        ("color", TYPE_FLOAT, [float(x) for x in stroke["rgba"]], 4),
        ("spacing", TYPE_FLOAT, [float(spacing)], 1),
        ("size", TYPE_FLOAT, [font_size_to_rv(stroke["font_size"])], 1),
        ("font", TYPE_STRING, [""], 1),
        ("text", TYPE_STRING, [stroke["text"]], 1),
        ("scale", TYPE_FLOAT, [float(stroke["scale"]) if stroke["scale"] else 1.0], 1),
        ("rotation", TYPE_FLOAT, [float(stroke["rotation"]) if stroke["rotation"] else 0.0], 1),
        ("origin", TYPE_STRING, [""], 1),
        ("debug", TYPE_INT, [0], 1),
        ("startFrame", TYPE_INT, [frame], 1),
        ("duration", TYPE_INT, [1], 1),
        ("mode", TYPE_INT, [0], 1),
        ("uuid", TYPE_STRING, [stroke["uuid"] or str(_uuid_mod.uuid4())], 1),
        ("softDeleted", TYPE_INT, [0], 1),
    ]
    return {"kind": "text", "uuid": stroke["uuid"], "user": stroke["user"], "props": props}


def _box_shape_spec(stroke: dict, frame: int) -> PaintNodeSpec:
    half = stroke["size"] / 2.0
    expanded_min = [stroke["min"][0] - half, stroke["min"][1] - half]
    expanded_max = [stroke["max"][0] + half, stroke["max"][1] + half]
    props = [
        ("min", TYPE_FLOAT, expanded_min, 2),
        ("max", TYPE_FLOAT, expanded_max, 2),
        ("borderColor", TYPE_FLOAT, list(stroke["rgba"]), 4),
        ("innerColor", TYPE_FLOAT, list(stroke["inner_rgba"]), 4),
        ("borderWidth", TYPE_FLOAT, [stroke["size"]], 1),
        ("startFrame", TYPE_INT, [frame], 1),
        ("duration", TYPE_INT, [1], 1),
        ("eye", TYPE_INT, [2], 1),
        ("uuid", TYPE_STRING, [stroke["uuid"]], 1),
        ("softDeleted", TYPE_INT, [0], 1),
    ]
    return {"kind": stroke["kind"], "uuid": stroke["uuid"], "user": stroke["user"], "props": props}


def _arrow_spec(stroke: dict, frame: int) -> PaintNodeSpec:
    props = [
        ("startPos", TYPE_FLOAT, list(stroke["start"]), 2),
        ("endPos", TYPE_FLOAT, list(stroke["end"]), 2),
        ("borderColor", TYPE_FLOAT, list(stroke["rgba"]), 4),
        ("innerColor", TYPE_FLOAT, list(stroke["rgba"]), 4),
        ("borderWidth", TYPE_FLOAT, [0.0], 1),
        ("thickness", TYPE_FLOAT, [stroke["size"] / 2.0], 1),
        ("startFrame", TYPE_INT, [frame], 1),
        ("duration", TYPE_INT, [1], 1),
        ("eye", TYPE_INT, [2], 1),
        ("uuid", TYPE_STRING, [stroke["uuid"]], 1),
        ("softDeleted", TYPE_INT, [0], 1),
    ]
    return {"kind": "arrow", "uuid": stroke["uuid"], "user": stroke["user"], "props": props}


def _degrade_shape_to_pen(stroke: dict) -> dict:
    """Tessellate a shape stroke into a pen stroke (graceful degradation).

    Used when a shape kind is absent from :data:`SUPPORTED_KINDS`. RV supports
    all shapes natively so this does not fire for RV, but it keeps the
    degradation path exercised and available to future host codecs.
    """
    if stroke["kind"] == "rect":
        pts = shapes.rect_polyline(stroke["min"], stroke["max"])
    elif stroke["kind"] == "ellipse":
        pts = shapes.ellipse_polyline(stroke["min"], stroke["max"])
    else:  # arrow
        pts = shapes.arrow_polyline(stroke["start"], stroke["end"])
    flat = [v for pt in pts for v in pt]
    return {
        "kind": "pen",
        "rgba": stroke["rgba"],
        "brush": "circle",
        "user": stroke["user"],
        "uuid": stroke["uuid"],
        "width": [stroke.get("size", 2.0)] * len(pts),
        "points": flat,
    }


def sync_events_to_rv_specs(events: List[Any], ctx: Optional[dict] = None) -> List[PaintNodeSpec]:
    """Convert a flat SyncEvent list to an ordered list of :data:`PaintNodeSpec`.

    Pure: no ``rv.commands`` import, so this is unit-testable outside RV.

    :param events: SyncEvent objects (or serialised dicts) for one frame.
    :param ctx: Optional context. ``ctx["frame"]`` sets ``startFrame`` on text
        and shape specs (default 0).
    :returns: Ordered list of ``PaintNodeSpec`` dicts.
    """
    frame = int((ctx or {}).get("frame", 0))
    specs: List[PaintNodeSpec] = []
    for stroke in _parse_events(events):
        kind = stroke["kind"]
        if kind not in SUPPORTED_KINDS:
            stroke = _degrade_shape_to_pen(stroke)
            kind = "pen"
        if kind in ("pen", "erase"):
            specs.append(_pen_spec(stroke))
        elif kind == "text":
            specs.append(_text_spec(stroke, frame))
        elif kind in ("ellipse", "rect"):
            specs.append(_box_shape_spec(stroke, frame))
        elif kind == "arrow":
            specs.append(_arrow_spec(stroke, frame))
    return specs


# --- Reverse: RV read-back dicts → SyncEvent objects ----------------------

def rv_strokes_to_sync_events(strokes: List[dict]) -> List[Any]:
    """Convert RV paint-node read-back dicts to SyncEvent objects.

    Pure: the impure part (reading properties off the paint node) is the
    caller's job; each ``stroke`` dict here already holds the read values.

    Recognised ``stroke["kind"]`` values: ``pen``/``erase`` (→ ``PaintStart`` +
    ``PaintPoints``), ``text`` (→ ``TextAnnotation``), and ``ellipse``/``rect``/
    ``arrow`` (→ ``EllipseAnnotation``/``RectangleAnnotation``/``ArrowAnnotation``).

    :param strokes: List of read-back dicts.
    :returns: Flat list of SyncEvent objects.
    """
    se = _se()
    events: List[Any] = []
    for stroke in strokes:
        kind = stroke.get("kind")
        if kind in ("pen", "erase"):
            pen_uuid = stroke.get("uuid") or str(_uuid_mod.uuid4())
            start = se.PaintStart(
                brush=stroke.get("brush", "oval"),
                rgba=[float(x) for x in stroke.get("color", [1.0, 1.0, 1.0, 1.0])],
                friendly_name=stroke.get("user", ""),
                uuid=pen_uuid,
            )
            if kind == "erase":
                start.type = "erase"
            events.append(start)
            points = list(stroke.get("points", []))
            # Invert the forward _pen_spec's `w * RV_WIDTH_SCALE` so a stroke's
            # width round-trips to the same OTIO/xStudio size it started as.
            width = [w / RV_WIDTH_SCALE for w in stroke.get("width", [])]
            xs = [p for p in points[0::2]]
            ys = [p for p in points[1::2]]
            if len(width) == 1:
                width = [width[0]] * len(xs)
            events.append(se.PaintPoints(uuid=pen_uuid, points=se.PaintVertices(xs, ys, width)))
        elif kind == "text":
            events.append(se.TextAnnotation(
                rgba=[float(x) for x in stroke.get("color", [1.0, 1.0, 1.0, 1.0])],
                position=[float(x) for x in stroke.get("position", [0.0, 0.0])],
                spacing=float(stroke.get("spacing", coords.DEFAULT_SPACING)),
                friendly_name=stroke.get("user", ""),
                font_size=rv_to_font_size(stroke.get("size", 0.0)) if "size" in stroke
                else float(stroke.get("font_size", coords.DEFAULT_FONT_SIZE)),
                font=stroke.get("font", "") or "",
                text=stroke.get("text", "") or "",
                rotation=float(stroke.get("rotation", 0.0)),
                scale=float(stroke.get("scale", 1.0)),
                uuid=stroke.get("uuid") or str(_uuid_mod.uuid4()),
            ))
        elif kind in ("ellipse", "rect"):
            cls = se.EllipseAnnotation if kind == "ellipse" else se.RectangleAnnotation
            events.append(cls(
                min=[float(x) for x in stroke.get("min", [0.0, 0.0])],
                max=[float(x) for x in stroke.get("max", [0.0, 0.0])],
                rgba=[float(x) for x in stroke.get("rgba", [1.0, 1.0, 1.0, 1.0])],
                size=float(stroke.get("size", 1.0)),
                inner_rgba=[float(x) for x in stroke.get("inner_rgba", [0.0, 0.0, 0.0, 0.0])],
                uuid=stroke.get("uuid") or str(_uuid_mod.uuid4()),
            ))
        elif kind == "arrow":
            events.append(se.ArrowAnnotation(
                start=[float(x) for x in stroke.get("start", [0.0, 0.0])],
                end=[float(x) for x in stroke.get("end", [0.0, 0.0])],
                rgba=[float(x) for x in stroke.get("rgba", [1.0, 1.0, 1.0, 1.0])],
                size=float(stroke.get("size", 1.0)),
                uuid=stroke.get("uuid") or str(_uuid_mod.uuid4()),
            ))
    return events


# --- D9 common contract entry points --------------------------------------

def from_sync_events(events: List[Any], ctx: Optional[dict] = None) -> List[PaintNodeSpec]:
    """Hub → host: SyncEvents → RV ``PaintNodeSpec`` list."""
    return sync_events_to_rv_specs(events, ctx)


def to_sync_events(strokes: List[dict], ctx: Optional[dict] = None) -> List[Any]:
    """Host → hub: RV read-back dicts → SyncEvents."""
    return rv_strokes_to_sync_events(strokes)
