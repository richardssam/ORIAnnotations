#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""The thin RV-touching edge for :mod:`otio_sync_core.rv_annotation_codec`.

:func:`apply_specs` is the *only* function that writes annotation paint-node
properties via RV's ``commands`` module. It is shared by every RV call site
(testchart batch, load plugin, live sync) so the property-writing logic — the
``set_prop`` helper, node-name convention, and per-frame ``order`` list — lives
in exactly one place.

``commands`` is passed in (rather than imported) so this module stays importable
and testable outside RV with a fake commands recorder.

Two modes:

* ``"append"`` — create fresh nodes and append them to the frame ``order``
  (testchart batch / plugin import / live render).
* ``"reconcile"`` — match existing nodes by ``uuid`` within the frame ``order``:
  update in place when found, add when not, and prune managed nodes whose uuid
  is absent from the incoming specs (live replace / partial).
"""

from __future__ import annotations

from typing import List, Optional

from otio_sync_core.rv_annotation_codec import (
    PaintNodeSpec, TYPE_STRING, TYPE_FLOAT, TYPE_INT,
)

#: Map a spec ``kind`` to the RV paint-node name prefix. Erase strokes render
#: as pen nodes (with ``mode=1``), so both map to ``"pen"``.
_ORDER_PREFIX = {
    "pen": "pen",
    "erase": "pen",
    "text": "text",
    "ellipse": "ellipse",
    "rect": "rect",
    "arrow": "arrow",
}


def _rv_type(commands, tag: str):
    return {
        TYPE_STRING: commands.StringType,
        TYPE_FLOAT: commands.FloatType,
        TYPE_INT: commands.IntType,
    }[tag]


def _set_prop(commands, node_path: str, name: str, tag: str, value: list, dim: int) -> None:
    full = f"{node_path}.{name}"
    if not commands.propertyExists(full):
        commands.newProperty(full, _rv_type(commands, tag), dim)
    if tag == TYPE_FLOAT:
        commands.setFloatProperty(full, [float(x) for x in value], True)
    elif tag == TYPE_STRING:
        commands.setStringProperty(full, [str(x) for x in value], True)
    else:
        commands.setIntProperty(full, [int(x) for x in value], True)


def _write_spec_props(commands, node_path: str, spec: PaintNodeSpec) -> None:
    for (name, tag, value, dim) in spec["props"]:
        _set_prop(commands, node_path, name, tag, value, dim)


def _ensure_paint_tags(commands, rv_node: str) -> None:
    annotate = f"{rv_node}.tag.annotate"
    if not commands.propertyExists(annotate):
        commands.newProperty(annotate, commands.StringType, 1)
    commands.setStringProperty(annotate, [""], True)
    ctx = f"{rv_node}.internal.creationContext"
    if not commands.propertyExists(ctx):
        commands.newProperty(ctx, commands.IntType, 1)
    commands.setIntProperty(ctx, [1], True)


def _next_id(commands, rv_node: str, start_id) -> int:
    if start_id is not None:
        return int(start_id)
    nid = f"{rv_node}.paint.nextId"
    if commands.propertyExists(nid):
        vals = commands.getIntProperty(nid)
        if vals:
            return int(vals[0])
    return 1


def _read_order(commands, frame_node: str) -> List[str]:
    prop = f"{frame_node}.order"
    if commands.propertyExists(prop):
        return list(commands.getStringProperty(prop) or [])
    return []


def _write_order(commands, frame_node: str, order: List[str]) -> None:
    prop = f"{frame_node}.order"
    if not commands.propertyExists(prop):
        commands.newProperty(prop, commands.StringType, 1)
    commands.setStringProperty(prop, list(order), True)


def _node_uuid(commands, rv_node: str, item: str) -> str:
    prop = f"{rv_node}.{item}.uuid"
    if commands.propertyExists(prop):
        vals = commands.getStringProperty(prop)
        if vals:
            return vals[0]
    return ""


def apply_specs(specs: List[PaintNodeSpec], commands, *, rv_node: str, frame: int,
                mode: str = "append", start_id=None) -> int:
    """Write ``PaintNodeSpec`` entries to ``rv_node`` for ``frame``.

    :param specs: Ordered specs from :func:`sync_events_to_rv_specs`.
    :param commands: RV ``commands`` module (or a compatible fake).
    :param rv_node: Target paint node (e.g. ``defaultSequence_p_<sg>_switchGroup``).
    :param frame: Frame the annotations belong to (embedded in node names).
    :param mode: ``"append"`` or ``"reconcile"``.
    :param start_id: Optional starting strokeid (else read from ``paint.nextId``).
    :returns: The next free strokeid after writing.
    :raises ValueError: on an unknown spec ``kind``.
    """
    for spec in specs:
        if spec["kind"] not in _ORDER_PREFIX:
            raise ValueError(f"apply_specs: unknown paint kind {spec['kind']!r}")

    if mode == "reconcile":
        return _apply_reconcile(specs, commands, rv_node=rv_node, frame=frame, start_id=start_id)
    if mode == "append":
        return _apply_append(specs, commands, rv_node=rv_node, frame=frame, start_id=start_id)
    raise ValueError(f"apply_specs: unknown mode {mode!r}")


def _apply_append(specs, commands, *, rv_node, frame, start_id) -> int:
    _ensure_paint_tags(commands, rv_node)
    strokeid = _next_id(commands, rv_node, start_id)
    frame_node = f"{rv_node}.frame:{frame}"
    order = _read_order(commands, frame_node)

    for spec in specs:
        prefix = _ORDER_PREFIX[spec["kind"]]
        item = f"{prefix}:{strokeid}:{frame}:{spec['user']}"
        _write_spec_props(commands, f"{rv_node}.{item}", spec)
        order.append(item)
        strokeid += 1

    _write_order(commands, frame_node, order)
    _set_paint_next_id(commands, rv_node, strokeid)
    return strokeid


def _apply_reconcile(specs, commands, *, rv_node, frame, start_id) -> int:
    _ensure_paint_tags(commands, rv_node)
    strokeid = _next_id(commands, rv_node, start_id)
    frame_node = f"{rv_node}.frame:{frame}"
    order = _read_order(commands, frame_node)

    # Index existing managed items by uuid.
    existing_by_uuid = {}
    for item in order:
        uid = _node_uuid(commands, rv_node, item)
        if uid:
            existing_by_uuid[uid] = item

    incoming_uuids = set()
    for spec in specs:
        uid = spec["uuid"]
        incoming_uuids.add(uid)
        item = existing_by_uuid.get(uid)
        if item is not None:
            # Update in place.
            _write_spec_props(commands, f"{rv_node}.{item}", spec)
        else:
            prefix = _ORDER_PREFIX[spec["kind"]]
            item = f"{prefix}:{strokeid}:{frame}:{spec['user']}"
            _write_spec_props(commands, f"{rv_node}.{item}", spec)
            order.append(item)
            strokeid += 1

    # Prune managed items whose uuid is gone.
    pruned = []
    for item in order:
        uid = _node_uuid(commands, rv_node, item)
        if uid and uid not in incoming_uuids:
            continue
        pruned.append(item)

    _write_order(commands, frame_node, pruned)
    _set_paint_next_id(commands, rv_node, strokeid)
    return strokeid


def _set_paint_next_id(commands, rv_node: str, strokeid: int) -> None:
    nid = f"{rv_node}.paint.nextId"
    if not commands.propertyExists(nid):
        commands.newProperty(nid, commands.IntType, 1)
    commands.setIntProperty(nid, [int(strokeid)], False)


# --- Read side: RV paint-node properties → stroke dicts -------------------
#
# The read-side counterpart to apply_specs/_write_spec_props. Produces stroke
# dicts in the shape otio_sync_core.rv_annotation_codec.rv_strokes_to_sync_events
# expects, so callers never touch ``commands`` directly on the read path either.

def _read_float(commands, path: str, default=None):
    if not commands.propertyExists(path):
        return default
    vals = commands.getFloatProperty(path)
    return list(vals) if vals else default


def _read_string(commands, path: str, default=None):
    if not commands.propertyExists(path):
        return default
    vals = commands.getStringProperty(path)
    return list(vals) if vals else default


def _read_int(commands, path: str, default=None):
    if not commands.propertyExists(path):
        return default
    vals = commands.getIntProperty(path)
    return list(vals) if vals else default


def read_stroke(commands, rv_node: str, item: str) -> Optional[dict]:
    """Read one paint child node's properties into a single stroke dict.

    The single-item counterpart to :func:`read_frame_strokes` — reads exactly
    the properties :func:`apply_specs` would have written for ``item``. Callers
    needing an *entire frame* should use :func:`read_frame_strokes`; callers
    naming one specific component directly (e.g. the live-sync broadcaster)
    use this.

    :param commands: RV ``commands`` module (or a compatible fake).
    :param rv_node: Paint node ``item`` belongs to.
    :param item: Order-list entry, e.g. ``"pen:3:42:sam"``.
    :returns: A stroke dict ready for
        :func:`otio_sync_core.rv_annotation_codec.rv_strokes_to_sync_events`,
        or ``None`` if ``item`` doesn't match a known kind prefix.
    """
    user = item.split(":")[-1]
    base = f"{rv_node}.{item}"

    if item.startswith("pen:"):
        mode = _read_int(commands, f"{base}.mode")
        return {
            "kind": "erase" if (mode and mode[0] == 1) else "pen",
            "brush": (_read_string(commands, f"{base}.brush", ["circle"]) or ["circle"])[0],
            "color": _read_float(commands, f"{base}.color", [1.0, 1.0, 1.0, 1.0]),
            "width": _read_float(commands, f"{base}.width", []),
            "points": _read_float(commands, f"{base}.points", []),
            "user": user,
        }
    if item.startswith("text:"):
        size = _read_float(commands, f"{base}.size", [0.0])
        return {
            "kind": "text",
            "color": _read_float(commands, f"{base}.color", [1.0, 1.0, 1.0, 1.0]),
            "position": _read_float(commands, f"{base}.position", [0.0, 0.0]),
            "spacing": (_read_float(commands, f"{base}.spacing", [0.0]) or [0.0])[0],
            "size": (size or [0.0])[0],
            "font": (_read_string(commands, f"{base}.font", [""]) or [""])[0],
            "text": (_read_string(commands, f"{base}.text", [""]) or [""])[0],
            "scale": (_read_float(commands, f"{base}.scale", [1.0]) or [1.0])[0],
            "rotation": (_read_float(commands, f"{base}.rotation", [0.0]) or [0.0])[0],
            "uuid": (_read_string(commands, f"{base}.uuid", [""]) or [""])[0],
            "user": user,
        }
    if item.startswith("ellipse:") or item.startswith("rect:"):
        border_width = (_read_float(commands, f"{base}.borderWidth", [0.0]) or [0.0])[0]
        r_min = _read_float(commands, f"{base}.min", [0.0, 0.0])
        r_max = _read_float(commands, f"{base}.max", [0.0, 0.0])
        half = border_width / 2.0
        c_min = [r_min[0] + half, r_min[1] + half] if r_min else [0.0, 0.0]
        c_max = [r_max[0] - half, r_max[1] - half] if r_max else [0.0, 0.0]
        return {
            "kind": "ellipse" if item.startswith("ellipse:") else "rect",
            "min": c_min,
            "max": c_max,
            "rgba": _read_float(commands, f"{base}.borderColor", [1.0, 1.0, 1.0, 1.0]),
            "inner_rgba": _read_float(commands, f"{base}.innerColor", [0.0, 0.0, 0.0, 0.0]),
            "size": border_width,
            "uuid": (_read_string(commands, f"{base}.uuid", [""]) or [""])[0],
            "user": user,
        }
    if item.startswith("arrow:"):
        thickness = (_read_float(commands, f"{base}.thickness", [0.0]) or [0.0])[0]
        return {
            "kind": "arrow",
            "start": _read_float(commands, f"{base}.startPos", [0.0, 0.0]),
            "end": _read_float(commands, f"{base}.endPos", [0.0, 0.0]),
            "rgba": _read_float(commands, f"{base}.borderColor", [1.0, 1.0, 1.0, 1.0]),
            "size": thickness * 2.0,
            "uuid": (_read_string(commands, f"{base}.uuid", [""]) or [""])[0],
            "user": user,
        }
    return None


def read_frame_strokes(commands, rv_node: str, frame: int) -> List[dict]:
    """Read one frame's paint child nodes back into stroke dicts.

    Reads the frame's ``order`` property and calls :func:`read_stroke` for
    each item. Unknown/unmanaged order items (not matching a known prefix)
    are skipped.

    :param commands: RV ``commands`` module (or a compatible fake).
    :param rv_node: Paint node the frame belongs to.
    :param frame: Frame number.
    :returns: List of stroke dicts ready for
        :func:`otio_sync_core.rv_annotation_codec.rv_strokes_to_sync_events`.
    """
    order = _read_order(commands, f"{rv_node}.frame:{frame}")
    strokes: List[dict] = []
    for item in order:
        stroke = read_stroke(commands, rv_node, item)
        if stroke is not None:
            strokes.append(stroke)
    return strokes
