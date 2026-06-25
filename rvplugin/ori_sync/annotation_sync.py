import time
import re
import uuid
import json
import collections.abc
import rv.commands

try:
    import opentimelineio as otio
except ImportError:
    otio = None

try:
    from PySide2 import QtCore
except ImportError:
    try:
        from PySide6 import QtCore
    except ImportError:
        QtCore = None

from utils import _log, _log_exc, _media_path
from otio_sync_core.manager import STATE_SYNCED


class AnnotationSyncController:
    def __init__(self, plugin):
        self.plugin = plugin
        self._pending_stroke = None            # (node_name, pen_component, stroke_uuid)
        self._next_stroke_uuid = None          # set when paint.nextId fires; consumed on first .points
        self._stroke_timer = None              # repeating partial-broadcast timer during drawing
        self._last_partial_point_count = 0
        self._partial_pen_nodes = {}           # stroke_uuid → rv pen node name (e.g. "pen:3:42:remote")
        self._last_sent_replace_sig = {}       # ann_clip_guid → JSON sig of last broadcast
        self._ignore_annotations_until = 0.0

    def _resolve_media_path_for_paint_node(self, node_name):
        """Return the media file path for an RVPaint node, or None.

        Supports both sequence-context nodes (``{seq}_p_{slot}``) and
        direct-source nodes (``{sg}_paint``).
        """
        if "_p_" in node_name:
            seq_name = node_name.split("_p_")[0]
            display_slot = node_name.split("_p_")[1]
            # Probe the slot node itself, plus — for OTIO sequences whose slot is
            # the source's switch group (e.g. "sourceGroup000011_switchGroup") —
            # the wrapped source group and the switch group's connected inputs.
            candidates = [display_slot]
            if display_slot.endswith("_switchGroup"):
                candidates.append(display_slot[: -len("_switchGroup")])
                try:
                    conns = rv.commands.nodeConnections(display_slot)
                    if conns and conns[0]:
                        candidates.extend(conns[0])
                except Exception:
                    pass
            for cand in candidates:
                try:
                    for n in rv.commands.nodesInGroup(cand):
                        if rv.commands.nodeType(n) == "RVFileSource":
                            path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                            if path:
                                return path
                except Exception:
                    pass
            m = re.match(r'^sourceGroup(\d+)$', display_slot)
            if m:
                slot_idx = int(m.group(1))
                seq_inputs = self.plugin.sequence._get_sequence_inputs(seq_name)
                if 0 <= slot_idx < len(seq_inputs):
                    actual_sg = seq_inputs[slot_idx]
                    for n in rv.commands.nodesInGroup(actual_sg):
                        if rv.commands.nodeType(n) == "RVFileSource":
                            try:
                                path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                                if path:
                                    return path
                            except Exception:
                                pass
        elif node_name.endswith("_paint"):
            source_group = node_name[:-len("_paint")]
            try:
                for n in rv.commands.nodesInGroup(source_group):
                    if rv.commands.nodeType(n) == "RVFileSource":
                        path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                        if path:
                            return path
            except Exception:
                pass
        return None

    def _import_existing_rv_annotations(self):
        """Broadcast annotations already in RV paint nodes into the OTIO session.

        Called during master initialisation so strokes painted before the sync
        session started become part of the shared timeline.  Only sequence-context
        paint nodes (``{seq}_p_{slot}``) and direct-source nodes (``{sg}_paint``)
        are considered; other node types are ignored.
        """
        _log("_import_existing_rv_annotations: scanning pre-existing RV annotations")
        try:
            fps = rv.commands.fps()
            for node in rv.commands.nodesOfType("RVPaint"):
                if "_p_" not in node and not node.endswith("_paint"):
                    continue
                try:
                    media_path = self._resolve_media_path_for_paint_node(node)
                    if not media_path:
                        continue
                    clip_guid = self.plugin.playback._clip_guid_for_media_path(media_path)
                    if not clip_guid:
                        continue
                    annotation_track_guid = self.plugin.sync_manager.annotation_track_guid_for_clip(clip_guid)
                    if not annotation_track_guid:
                        continue
                    clip = self.plugin.sync_manager._object_map.get(clip_guid)
                    n_frames = 1000
                    if clip and getattr(clip, 'source_range', None):
                        try:
                            n_frames = max(1, int(clip.source_range.duration.value))
                        except Exception:
                            pass
                    count = 0
                    for frame in range(1, n_frames + 1):
                        order_prop = f"{node}.frame:{frame}.order"
                        if not rv.commands.propertyExists(order_prop):
                            continue
                        try:
                            items = rv.commands.getStringProperty(order_prop)
                        except Exception:
                            continue
                        for item in items:
                            try:
                                self._broadcast_annotation(node, item)
                                count += 1
                            except Exception as e:
                                _log(f"  import: failed {item}: {e}")
                    if count:
                        _log(f"  import: {count} annotation(s) from {node}")
                except Exception as e:
                    _log(f"  import: error scanning {node}: {e}")
        except Exception as e:
            _log_exc(f"_import_existing_rv_annotations failed: {e}")

    def _apply_partial_annotation(self, payload):
        """Render a mid-stroke partial annotation from a remote peer.

        The stroke is drawn into RV paint using the same UUID as the final
        stroke.  If this UUID was already seen (a previous partial), the
        existing paint node's points are updated in-place instead of
        creating a duplicate.  The OTIO timeline is not modified.

        :param payload: Dict with keys ``clip_guid``, ``frame``, ``fps``, ``events``.
        """
        clip_guid = payload.get("clip_guid")
        frame_val = payload.get("frame", 0)
        fps = payload.get("fps", 25.0)
        events_raw = payload.get("events", [])

        media_clip = self.plugin.sync_manager._object_map.get(clip_guid) if clip_guid else None
        if not isinstance(media_clip, otio.schema.Clip):
            return
        ref = media_clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
            return
        media_path = _media_path(ref.target_url)

        # Convert clip-local OTIO frame (0-indexed) to the RV paint frame.
        # In RV's OTIO-imported sequences the paint frame IS the source timecode
        # frame (e.g. 110), which equals source_range.start + clip_local.  For
        # native no-timecode media (source_range.start ≤ 1) the paint frame is
        # 1-indexed clip-local, so fall back to clip_local + 1.
        clip_local = int(frame_val)
        _sr = media_clip.source_range
        if _sr is not None and int(_sr.start_time.value) > 1:
            rv_frame = int(_sr.start_time.value) + clip_local
        else:
            rv_frame = clip_local + 1

        try:
            otio.schema.schemadef.module_from_name('SyncEvent')
        except Exception:
            pass

        for ev_dict in events_raw:
            try:
                if isinstance(ev_dict, dict):
                    ev_dict = otio.adapters.read_from_string(
                        otio.adapters.write_to_string(ev_dict, "otio_json"), "otio_json"
                    )
                if not isinstance(ev_dict, otio.schemadef.SyncEvent.PaintStart):
                    continue
                stroke_uuid = getattr(ev_dict, "uuid", None)
                if not stroke_uuid:
                    continue
            except Exception:
                continue

            # Find corresponding PaintPoints event in the list
            pts_ev = None
            for other in events_raw:
                try:
                    if isinstance(other, dict):
                        other = otio.adapters.read_from_string(
                            otio.adapters.write_to_string(other, "otio_json"), "otio_json"
                        )
                    if (isinstance(other, otio.schemadef.SyncEvent.PaintPoints)
                            and getattr(other, "uuid", None) == stroke_uuid):
                        pts_ev = other
                        break
                except Exception:
                    continue

            if not pts_ev:
                continue

            points_flat = [v for pair in zip(pts_ev.points.x, pts_ev.points.y) for v in pair]
            node = self._find_paint_node_for_media(media_path, rv_frame, clip_guid)
            if not node:
                continue

            existing_pen = self._partial_pen_nodes.get(stroke_uuid)
            if existing_pen and rv.commands.propertyExists(f"{node}.{existing_pen}.points"):
                # Update points in-place for an already-started partial stroke.
                try:
                    rv.commands.setFloatProperty(
                        f"{node}.{existing_pen}.points", points_flat, True
                    )
                    widths = list(pts_ev.points.size) if pts_ev.points.size else [2.0]
                    if len(widths) == 1:
                        widths = widths * (len(points_flat) // 2)
                    rv.commands.setFloatProperty(
                        f"{node}.{existing_pen}.width", widths, True
                    )
                    if QtCore:
                        QtCore.QTimer.singleShot(0, rv.commands.redraw)
                except Exception as e:
                    _log(f"_apply_partial_annotation: update failed: {e}")
            else:
                # First partial for this UUID — create a new pen node.
                color = list(ev_dict.rgba) if ev_dict.rgba else [1.0, 1.0, 1.0, 1.0]
                brush = ev_dict.brush or "circle"
                widths = list(pts_ev.points.size) if pts_ev.points.size else [2.0]
                mode = 1 if getattr(ev_dict, "type", "color") == "erase" else 0
                self._apply_annotation({
                    "media_path": media_path,
                    "frame": rv_frame,
                    "node_name": None,
                    "points": points_flat,
                    "color": color,
                    "brush": brush,
                    "width": widths,
                    "join": 3,
                    "cap": 1,
                    "mode": mode,
                    "hold": int(bool(getattr(ev_dict, "hold", False))),
                    "ghost": int(bool(getattr(ev_dict, "ghost", False))),
                    "ghost_before": getattr(ev_dict, "ghost_before", 0) or 0,
                    "ghost_after": getattr(ev_dict, "ghost_after", 0) or 0,
                    "_stroke_uuid": stroke_uuid,
                })

    def _apply_annotation_render(self, ann_clip):
        """Render an annotation clip received via insert_child into RV paint.

        Reads the annotated frame from ``source_range.start_time`` (0-indexed
        clip-local) and the media reference from ``metadata["clip_guid"]``,
        making the receive path portable across tools.
        """
        clip_guid = ann_clip.metadata.get("clip_guid")
        events_data = ann_clip.metadata.get("annotation_commands", [])

        media_clip = self.plugin.sync_manager._object_map.get(clip_guid) if clip_guid else None
        if not isinstance(media_clip, otio.schema.Clip):
            _log(f"RECV annotation: no media Clip for guid={clip_guid}")
            return
        ref = media_clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
            _log(f"RECV annotation: clip {clip_guid} has no ExternalReference")
            return
        media_path = _media_path(ref.target_url)

        # Convert clip-local OTIO frame (0-indexed) to the RV paint frame.
        # RV's OTIO sequences store paint at the source timecode frame
        # (source_range.start + clip_local).  Native no-timecode falls back to
        # 1-indexed clip-local (source_range.start ≤ 1).
        _clip_local = int(ann_clip.source_range.start_time.value) if ann_clip.source_range else 0
        _sr = media_clip.source_range
        if _sr is not None and int(_sr.start_time.value) > 1:
            rv_frame = int(_sr.start_time.value) + _clip_local
        else:
            rv_frame = _clip_local + 1

        try:
            otio.schema.schemadef.module_from_name('SyncEvent')
        except Exception:
            pass

        # Group events by stroke UUID so that multi-stroke deltas (e.g. when
        # the user draws several strokes before the debounce fires) are all
        # rendered, not just the last PaintStart/PaintPoints pair.
        event_groups = {}
        rendered = 0
        # Cache the paint node once for the UUID-existence checks below.
        _paint_node_cache = self._find_paint_node_for_media(media_path, rv_frame, clip_guid)
        for ev in events_data:
            try:
                if isinstance(ev, (dict, collections.abc.Mapping)):
                    ev = otio.adapters.read_from_string(otio.adapters.write_to_string(ev, "otio_json"), "otio_json")
                if isinstance(ev, otio.schemadef.SyncEvent.TextAnnotation):
                    uuid_val = ev.uuid or ""
                    text_val = ev.text or ""
                    if not text_val.strip():
                        continue
                    # Snapshot replay sends the full clip as insert_child; if the
                    # node was already painted by _rebuild_rv_session, skip it.
                    if _paint_node_cache and self._text_uuid_exists_in_rv(_paint_node_cache, rv_frame, uuid_val):
                        position = list(ev.position) if getattr(ev, "position", None) else [0.0, 0.0]
                        color = list(ev.rgba) if getattr(ev, "rgba", None) else [1.0, 1.0, 1.0, 1.0]
                        rv_size = float(ev.font_size) / 5000.0 if getattr(ev, "font_size", None) else 0.01
                        order_prop = f"{_paint_node_cache}.frame:{rv_frame}.order"
                        updated = False
                        if rv.commands.propertyExists(order_prop):
                            for item in rv.commands.getStringProperty(order_prop):
                                if not item.startswith("text:"):
                                    continue
                                uuid_prop = f"{_paint_node_cache}.{item}.uuid"
                                if not rv.commands.propertyExists(uuid_prop):
                                    continue
                                existing_uuid = rv.commands.getStringProperty(uuid_prop)
                                if existing_uuid and existing_uuid[0] == uuid_val:
                                    rv.commands.setStringProperty(f"{_paint_node_cache}.{item}.text", [text_val], True)
                                    rv.commands.setFloatProperty(f"{_paint_node_cache}.{item}.position", position, True)
                                    rv.commands.setFloatProperty(f"{_paint_node_cache}.{item}.color", color, True)
                                    rv.commands.setFloatProperty(f"{_paint_node_cache}.{item}.size", [rv_size], True)
                                    _log(f"RECV annotation: updated dup text uuid={uuid_val[:8]!r} in place (text={text_val!r})")
                                    updated = True
                                    break
                        if updated:
                            if QtCore:
                                QtCore.QTimer.singleShot(0, rv.commands.redraw)
                            rendered += 1
                        else:
                            _log(f"RECV annotation: skip dup text uuid={uuid_val[:8]!r} (already in RV, but could not update)")
                        continue
                    rv_size = float(ev.font_size) / 5000.0 if getattr(ev, "font_size", None) else 0.01
                    _log(f"RECV TextAnnotation font_size={getattr(ev, 'font_size', None)!r} → rv_size={rv_size!r}")
                    self._apply_text_annotation({
                        "media_path": media_path,
                        "frame": rv_frame,
                        "clip_guid": clip_guid,
                        "node_name": None,
                        "position": list(ev.position) if getattr(ev, "position", None) else [0.0, 0.0],
                        "color": list(ev.rgba) if getattr(ev, "rgba", None) else [1.0, 1.0, 1.0, 1.0],
                        "spacing": float(ev.spacing) if getattr(ev, "spacing", None) is not None else 0.8,
                        "size": rv_size,
                        "scale": float(ev.scale) if getattr(ev, "scale", None) is not None else 1.0,
                        "rotation": float(ev.rotation) if getattr(ev, "rotation", None) is not None else 0.0,
                        "font": ev.font or "",
                        "text": ev.text or "",
                        "uuid": uuid_val,
                    })
                    rendered += 1
                else:
                    ev_uuid = getattr(ev, "uuid", None) or str(id(ev))
                    if ev_uuid not in event_groups:
                        event_groups[ev_uuid] = {"start": None, "points": None}
                    if isinstance(ev, otio.schemadef.SyncEvent.PaintStart):
                        event_groups[ev_uuid]["start"] = ev
                    elif isinstance(ev, otio.schemadef.SyncEvent.PaintPoints):
                        event_groups[ev_uuid]["points"] = ev
            except Exception as e:
                _log(f"RECV annotation: failed to deserialise event: {e}")

        for grp in event_groups.values():
            start_event = grp["start"]
            points_event = grp["points"]
            if not start_event or not points_event:
                continue
            ev_uuid = getattr(start_event, "uuid", None)
            if ev_uuid and ev_uuid in self._partial_pen_nodes:
                # A partial render already placed this stroke; update its final
                # points in-place rather than creating a duplicate pen node.
                node = _paint_node_cache or self._find_paint_node_for_media(media_path, rv_frame, clip_guid)
                pen_node = self._partial_pen_nodes.pop(ev_uuid)
                if node and rv.commands.propertyExists(f"{node}.{pen_node}.points"):
                    points_flat = [v for pair in zip(points_event.points.x, points_event.points.y) for v in pair]
                    rv.commands.setFloatProperty(f"{node}.{pen_node}.points", points_flat, True)
                    widths = list(points_event.points.size)
                    if len(widths) == 1:
                        widths = widths * (len(points_flat) // 2)
                    rv.commands.setFloatProperty(f"{node}.{pen_node}.width", widths, True)
                    if QtCore:
                        QtCore.QTimer.singleShot(0, rv.commands.redraw)
                    rendered += 1
                continue
            points_flat = [v for pair in zip(points_event.points.x, points_event.points.y) for v in pair]
            self._apply_annotation({
                "media_path": media_path,
                "frame": rv_frame,
                "clip_guid": clip_guid,
                "node_name": None,
                "points": points_flat,
                "color": list(start_event.rgba),
                "brush": start_event.brush,
                "width": list(points_event.points.size),
                "join": 3,
                "cap": 1,
                "mode": 1 if getattr(start_event, "type", "color") == "erase" else 0,
                "hold": int(bool(getattr(start_event, "hold", False))),
                "ghost": int(bool(getattr(start_event, "ghost", False))),
                "ghost_before": getattr(start_event, "ghost_before", 0) or 0,
                "ghost_after": getattr(start_event, "ghost_after", 0) or 0,
            })
            rendered += 1

        if rendered == 0:
            _log("RECV annotation: no valid annotation events found")

    def _apply_annotation_replace(self, ann_clip):
        """Apply a full annotation_commands replacement to RV paint.

        Called when a peer sends ``annotation_commands_replaced`` (e.g. a text
        edit or drag-move in xStudio).  For each ``TextAnnotation`` command in
        the replacement, the method finds the existing RV text node by UUID and
        updates its ``text``, ``position``, ``color``, and ``size`` properties in
        place.  This avoids the duplicate-text artefact that would result from
        calling ``_apply_text_annotation`` (which always creates a new node).

        Stroke commands (``PaintStart`` / ``PaintPoints``) are skipped because
        they are already painted in RV and have not changed.

        Falls back to ``_apply_text_annotation`` when no node with the matching
        UUID is found (e.g. if the first broadcast was dropped).
        """
        clip_guid = ann_clip.metadata.get("clip_guid")
        events_data = ann_clip.metadata.get("annotation_commands", [])
        incoming_text_uuids = set()

        media_clip = self.plugin.sync_manager._object_map.get(clip_guid) if clip_guid else None
        if not isinstance(media_clip, otio.schema.Clip):
            _log(f"RECV annotation replace: no media Clip for guid={clip_guid}")
            return
        ref = media_clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
            return

        _clip_local = int(ann_clip.source_range.start_time.value) if ann_clip.source_range else 0
        _sr = media_clip.source_range
        if _sr is not None and int(_sr.start_time.value) > 1:
            rv_frame = int(_sr.start_time.value) + _clip_local
        else:
            rv_frame = _clip_local + 1
        media_path = _media_path(ref.target_url)

        node = self._find_paint_node_for_media(media_path, rv_frame, clip_guid)
        if not node:
            _log(f"RECV annotation replace: no paint node for media_path={media_path} frame={rv_frame}")
            return

        order_prop = f"{node}.frame:{rv_frame}.order"

        for ev in events_data:
            try:
                if isinstance(ev, (dict, collections.abc.Mapping)):
                    ev = otio.adapters.read_from_string(otio.adapters.write_to_string(ev, "otio_json"), "otio_json")
            except Exception as e:
                _log(f"RECV annotation replace: failed to deserialise event: {e}")
                continue

            if not isinstance(ev, otio.schemadef.SyncEvent.TextAnnotation):
                continue  # strokes are already in RV — do not re-add

            uuid_val = ev.uuid or ""
            text_val = ev.text or ""
            position = list(ev.position) if getattr(ev, "position", None) else [0.0, 0.0]
            color = list(ev.rgba) if getattr(ev, "rgba", None) else [1.0, 1.0, 1.0, 1.0]
            rv_size = float(ev.font_size) / 5000.0 if getattr(ev, "font_size", None) else 0.01
            
            incoming_text_uuids.add(uuid_val)

            # Scan the frame's draw-order list for a text node with this UUID.
            found = False
            if rv.commands.propertyExists(order_prop):
                for item in rv.commands.getStringProperty(order_prop):
                    if not item.startswith("text:"):
                        continue
                    uuid_prop = f"{node}.{item}.uuid"
                    if not rv.commands.propertyExists(uuid_prop):
                        continue
                    existing_uuid = rv.commands.getStringProperty(uuid_prop)
                    if not existing_uuid or existing_uuid[0] != uuid_val:
                        continue
                    current_text = rv.commands.getStringProperty(f"{node}.{item}.text")
                    if not current_text or current_text[0] != text_val:
                        rv.commands.setStringProperty(f"{node}.{item}.text", [text_val], True)
                    
                    # For simplicity, we just overwrite floats since they don't crash the text editor
                    rv.commands.setFloatProperty(f"{node}.{item}.position", position, True)
                    rv.commands.setFloatProperty(f"{node}.{item}.color", color, True)
                    rv.commands.setFloatProperty(f"{node}.{item}.size", [rv_size], True)
                    _log(f"RECV annotation replace: updated {item} text={text_val!r} uuid={uuid_val[:8]!r}")
                    found = True
                    break

            if not found:
                # UUID not found — initial broadcast may have stored a null/empty uuid.
                # If exactly one text node exists on this frame, update it in place and
                # repair its uuid so subsequent replaces can find it by uuid.
                updated_orphan = False
                if rv.commands.propertyExists(order_prop):
                    text_items = [
                        i for i in rv.commands.getStringProperty(order_prop)
                        if i.startswith("text:")
                    ]
                    if len(text_items) == 1:
                        item = text_items[0]
                        current_text = rv.commands.getStringProperty(f"{node}.{item}.text")
                        if not current_text or current_text[0] != text_val:
                            rv.commands.setStringProperty(f"{node}.{item}.text", [text_val], True)
                        rv.commands.setFloatProperty(f"{node}.{item}.position", position, True)
                        rv.commands.setFloatProperty(f"{node}.{item}.color", color, True)
                        rv.commands.setFloatProperty(f"{node}.{item}.size", [rv_size], True)
                        if rv.commands.propertyExists(f"{node}.{item}.uuid"):
                            rv.commands.setStringProperty(f"{node}.{item}.uuid", [uuid_val], True)
                        _log(f"RECV annotation replace: repaired orphan {item} → uuid={uuid_val[:8]!r} text={text_val!r}")
                        updated_orphan = True
                if not updated_orphan:
                    _log(f"RECV annotation replace: UUID {uuid_val[:8]!r} not found, adding new node")
                    self._apply_text_annotation({
                        "media_path": media_path,
                        "frame": rv_frame,
                        "node_name": None,
                        "position": position,
                        "color": color,
                        "spacing": float(ev.spacing) if getattr(ev, "spacing", None) is not None else 0.8,
                        "size": rv_size,
                        "scale": float(ev.scale) if getattr(ev, "scale", None) is not None else 1.0,
                        "rotation": float(ev.rotation) if getattr(ev, "rotation", None) is not None else 0.0,
                        "font": ev.font or "",
                        "text": text_val,
                        "uuid": uuid_val,
                    })

        if rv.commands.propertyExists(order_prop):
            old_order = rv.commands.getStringProperty(order_prop) or []
            new_order = []
            for item in old_order:
                if item.startswith("text:"):
                    uuid_prop = f"{node}.{item}.uuid"
                    existing_uuid = ""
                    if rv.commands.propertyExists(uuid_prop):
                        eu = rv.commands.getStringProperty(uuid_prop)
                        existing_uuid = eu[0] if eu else ""
                    if existing_uuid and existing_uuid not in incoming_text_uuids:
                        _log(f"RECV annotation replace: removing deleted text {item} uuid={existing_uuid[:8]!r}")
                        continue
                new_order.append(item)
            if new_order != old_order:
                rv.commands.setStringProperty(order_prop, new_order, True)

        if QtCore:
            QtCore.QTimer.singleShot(0, rv.commands.redraw)

    def _find_paint_node_for_media(self, media_path, frame, clip_guid=None):
        """Find the local RVPaint node for a given media path and frame.

        Must use metaEvaluateClosestByType to get the sequence-level paint node
        (e.g. defaultSequence_p_sourceGroup000000) rather than the source-level
        node found inside the source group (e.g. sourceGroup000000_paint).  The
        source-level node is invisible in sequence view, so strokes written there
        never appear when the user is watching a sequence.

        When *clip_guid* is the specific OTIO clip occurrence (not just the first
        path-match), pass it here so the sequence-frame calculation uses that
        clip's range_in_parent.start rather than the first occurrence's (which
        can differ by many frames in a cut sequence with repeated media).
        frameStart() — 0 for OTIO sequences, 1 for native — replaces the
        historic hardcoded +1 so the formula is correct in both contexts.
        """
        seq_frame = frame
        if self.plugin.sync_manager:
            if clip_guid:
                # Derive the sequence-position frame for metaEvaluateClosestByType.
                # The caller's `frame` is the STORAGE frame (source timecode, e.g. 110),
                # which is out of range for the sequence EDL (0-19).  We need the
                # SEQUENCE POSITION (e.g. 10) so metaEval finds the right slot.
                # Formula: seq_frame = rip.start + clip_local + frameStart
                # where clip_local = frame - source_range.start (timecode) or frame-1 (native).
                try:
                    clip = self.plugin.sync_manager._object_map.get(clip_guid)
                    if clip and clip.parent():
                        rip = clip.trimmed_range_in_parent()
                        if rip is not None:
                            sr = clip.source_range
                            frame_base = self.plugin.playback._frame_base()
                            if sr is not None and int(sr.start_time.value) > 1:
                                clip_local = frame - int(sr.start_time.value)
                            else:
                                clip_local = frame - 1
                            seq_frame = int(rip.start_time.value) + clip_local + frame_base
                except Exception as e:
                    _log(f"  _find_paint_node: could not derive seq_frame from clip_guid: {e}")
            else:
                lookup_guid = self.plugin.playback._clip_guid_for_media_path(media_path)
                if lookup_guid:
                    clip = self.plugin.sync_manager._object_map.get(lookup_guid)
                    if clip and clip.parent():
                        try:
                            range_in_parent = clip.trimmed_range_in_parent()
                            if range_in_parent:
                                start_val = range_in_parent.start_time.value
                                seq_frame = int(start_val + (frame - 1)) + 1
                        except Exception as e:
                            _log(f"  _find_paint_node: could not get sequence frame: {e}")

        eval_infos = rv.commands.metaEvaluateClosestByType(seq_frame, "RVPaint")
        _log(f"  _find_paint_node: metaEval local_frame={frame} seq_frame={seq_frame} → {[e.get('node') for e in eval_infos] if eval_infos else None}")
        if eval_infos:
            return eval_infos[0]['node']
        # Fallback for source-view contexts (no sequence in the graph).
        source_group = self.plugin.sequence._path_to_source_group_map().get(media_path)
        if source_group:
            for n in rv.commands.nodesInGroup(source_group):
                try:
                    if rv.commands.nodeType(n) == "RVPaint":
                        _log(f"  _find_paint_node: fallback source-level node {n}")
                        return n
                except Exception:
                    pass
        return None

    def _apply_annotation(self, data):
        try:
            frame = data.get("frame")
            points = data.get("points")
            color = data.get("color")
            brush = data.get("brush")
            width = data.get("width", [2.0])
            join = data.get("join", 3)
            cap = data.get("cap", 1)
            node_name = data.get("node_name")
            media_path = data.get("media_path")
            ann_clip_guid = data.get("clip_guid")
            _log(f"RECV annotation frame={frame} brush={brush} node={node_name} npts={len(points) // 2 if points else 0}")
            node = self._find_paint_node_for_media(media_path, frame, ann_clip_guid)
            _log(f"  _apply_annotation: using node={node}")
            if not node:
                # Last resort: sender's node name verbatim
                if node_name and rv.commands.nodeExists(node_name):
                    node = node_name
                else:
                    _log(f"RECV annotation dropped: no paint node for media_path={media_path} frame={frame}")
                    return
            paint_prop = f"{node}.paint"
            next_id = rv.commands.getIntProperty(f"{paint_prop}.nextId")[0]
            pen_node = f"pen:{next_id}:{frame}:remote"
            full_pen = f"{node}.{pen_node}"
            order_prop = f"{node}.frame:{frame}.order"

            rv.commands.newProperty(f"{full_pen}.color", rv.commands.FloatType, 4)
            rv.commands.newProperty(f"{full_pen}.width", rv.commands.FloatType, 1)
            rv.commands.newProperty(f"{full_pen}.brush", rv.commands.StringType, 1)
            rv.commands.newProperty(f"{full_pen}.points", rv.commands.FloatType, 2)
            rv.commands.newProperty(f"{full_pen}.debug", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.join", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.cap", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.splat", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.startFrame", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.duration", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.mode", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.hold", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.ghost", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.ghostBefore", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.ghostAfter", rv.commands.IntType, 1)
            rv.commands.setIntProperty(f"{full_pen}.mode", [data.get("mode", 0)], True)
            rv.commands.setIntProperty(f"{full_pen}.hold", [data.get("hold", 0)], True)
            rv.commands.setIntProperty(f"{full_pen}.ghost", [data.get("ghost", 0)], True)
            rv.commands.setIntProperty(f"{full_pen}.ghostBefore", [data.get("ghost_before", 0)], True)
            rv.commands.setIntProperty(f"{full_pen}.ghostAfter", [data.get("ghost_after", 0)], True)
            rv.commands.setIntProperty(f"{full_pen}.debug", [0], True)
            rv.commands.setIntProperty(f"{full_pen}.join", [join], True)
            rv.commands.setIntProperty(f"{full_pen}.cap", [cap], True)
            rv.commands.setIntProperty(f"{full_pen}.startFrame", [frame], True)
            rv.commands.setIntProperty(f"{full_pen}.duration", [1], True)
            rv.commands.setFloatProperty(f"{full_pen}.color", list(color), True)
            rv.commands.insertFloatProperty(f"{full_pen}.width", list(width))
            rv.commands.setStringProperty(f"{full_pen}.brush", [brush], True)
            rv.commands.setIntProperty(f"{full_pen}.splat", [1 if brush == "gauss" else 0], True)
            rv.commands.insertFloatProperty(f"{full_pen}.points", list(points))
            if not rv.commands.propertyExists(order_prop):
                rv.commands.newProperty(order_prop, rv.commands.StringType, 1)
            rv.commands.insertStringProperty(order_prop, [pen_node])
            _log(f"  _apply_annotation: wrote {pen_node} to {order_prop}")
            rv.commands.setIntProperty(f"{paint_prop}.nextId", [next_id + 1], True)
            # Record UUID→pen_node so partial updates can find this node,
            # and so the final INSERT_CHILD render can skip re-creating it.
            stroke_uuid = data.get("_stroke_uuid")
            if stroke_uuid:
                self._partial_pen_nodes[stroke_uuid] = pen_node
            if QtCore:
                QtCore.QTimer.singleShot(0, rv.commands.redraw)
        except Exception as e:
            _log_exc(f"Failed to apply remote annotation: {e}")

    def _text_uuid_exists_in_rv(self, node, frame, uuid_val):
        """Return True if a text node with *uuid_val* already exists in *node*'s draw-order for *frame*."""
        if not uuid_val or not node:
            return False
        order_prop = f"{node}.frame:{frame}.order"
        if not rv.commands.propertyExists(order_prop):
            return False
        for item in rv.commands.getStringProperty(order_prop):
            if not item.startswith("text:"):
                continue
            uuid_prop = f"{node}.{item}.uuid"
            if not rv.commands.propertyExists(uuid_prop):
                continue
            existing = rv.commands.getStringProperty(uuid_prop)
            if existing and existing[0] == uuid_val:
                return True
        return False

    def _apply_text_annotation(self, data):
        try:
            frame = data.get("frame")
            position = data.get("position", [0.0, 0.0])
            color = data.get("color", [1.0, 1.0, 1.0, 1.0])
            spacing = data.get("spacing", 0.8)
            size = data.get("size", 0.01)
            scale = data.get("scale", 1.0)
            rotation = data.get("rotation", 0.0)
            font = data.get("font", "")
            text = data.get("text", "")
            origin = data.get("origin", "")
            debug = data.get("debug", 0)
            duration = data.get("duration", 1)
            mode = data.get("mode", 0)
            uuid_val = data.get("uuid", "")
            soft_deleted = data.get("softDeleted", 0)
            node_name = data.get("node_name")
            media_path = data.get("media_path")
            ann_clip_guid = data.get("clip_guid")

            _log(f"RECV text annotation frame={frame} text={text} uuid={uuid_val}")
            node = self._find_paint_node_for_media(media_path, frame, ann_clip_guid)
            _log(f"  _apply_text_annotation: using node={node}")
            if not node:
                if node_name and rv.commands.nodeExists(node_name):
                    node = node_name
                else:
                    _log(f"RECV text annotation dropped: no paint node for media_path={media_path} frame={frame}")
                    return

            paint_prop = f"{node}.paint"
            next_id = rv.commands.getIntProperty(f"{paint_prop}.nextId")[0]
            text_node = f"text:{next_id}:{frame}:remote"
            full_text = f"{node}.{text_node}"
            order_prop = f"{node}.frame:{frame}.order"

            rv.commands.newProperty(f"{full_text}.position", rv.commands.FloatType, 2)
            rv.commands.newProperty(f"{full_text}.color", rv.commands.FloatType, 4)
            rv.commands.newProperty(f"{full_text}.spacing", rv.commands.FloatType, 1)
            rv.commands.newProperty(f"{full_text}.size", rv.commands.FloatType, 1)
            rv.commands.newProperty(f"{full_text}.scale", rv.commands.FloatType, 1)
            rv.commands.newProperty(f"{full_text}.rotation", rv.commands.FloatType, 1)
            rv.commands.newProperty(f"{full_text}.font", rv.commands.StringType, 1)
            rv.commands.newProperty(f"{full_text}.text", rv.commands.StringType, 1)
            rv.commands.newProperty(f"{full_text}.origin", rv.commands.StringType, 1)
            rv.commands.newProperty(f"{full_text}.debug", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_text}.startFrame", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_text}.duration", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_text}.mode", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_text}.uuid", rv.commands.StringType, 1)
            rv.commands.newProperty(f"{full_text}.softDeleted", rv.commands.IntType, 1)

            rv.commands.setFloatProperty(f"{full_text}.position", list(position), True)
            rv.commands.setFloatProperty(f"{full_text}.color", list(color), True)
            rv.commands.setFloatProperty(f"{full_text}.spacing", [spacing], True)
            rv.commands.setFloatProperty(f"{full_text}.size", [size], True)
            rv.commands.setFloatProperty(f"{full_text}.scale", [scale], True)
            rv.commands.setFloatProperty(f"{full_text}.rotation", [rotation], True)
            rv.commands.setStringProperty(f"{full_text}.font", [font], True)
            rv.commands.setStringProperty(f"{full_text}.text", [text], True)
            rv.commands.setStringProperty(f"{full_text}.origin", [origin], True)
            rv.commands.setIntProperty(f"{full_text}.debug", [debug], True)
            rv.commands.setIntProperty(f"{full_text}.startFrame", [frame], True)
            rv.commands.setIntProperty(f"{full_text}.duration", [duration], True)
            rv.commands.setIntProperty(f"{full_text}.mode", [mode], True)
            rv.commands.setStringProperty(f"{full_text}.uuid", [uuid_val], True)
            rv.commands.setIntProperty(f"{full_text}.softDeleted", [soft_deleted], True)

            if not rv.commands.propertyExists(order_prop):
                rv.commands.newProperty(order_prop, rv.commands.StringType, 1)
            rv.commands.insertStringProperty(order_prop, [text_node])
            _log(f"  _apply_text_annotation: wrote {text_node} to {order_prop}")
            rv.commands.setIntProperty(f"{paint_prop}.nextId", [next_id + 1], True)
            if QtCore:
                QtCore.QTimer.singleShot(0, rv.commands.redraw)
        except Exception as e:
            _log_exc(f"Failed to apply remote text annotation: {e}")

    def _stop_stroke_timers(self):
        if self._stroke_timer and self._stroke_timer.isActive():
            self._stroke_timer.stop()

    def _send_partial_stroke(self):
        """Repeating timer callback: broadcast current points without persisting to timeline."""
        if not self._pending_stroke:
            if self._stroke_timer:
                self._stroke_timer.stop()
            return
        node_name, component, stroke_uuid = self._pending_stroke
        full_prop = f"{node_name}.{component}"
        if not rv.commands.propertyExists(f"{full_prop}.points"):
            return
        pts = rv.commands.getFloatProperty(f"{full_prop}.points")
        if len(pts) == self._last_partial_point_count:
            return  # no new points since last broadcast
        self._last_partial_point_count = len(pts)
        self._broadcast_annotation(node_name, component, partial=True, stroke_uuid=stroke_uuid)

    def _on_pen_up(self):
        """Pen-up: stop partial timer and send final stroke."""
        if self._stroke_timer:
            self._stroke_timer.stop()
        self._flush_pending_stroke()

    def _flush_pending_stroke(self):
        if not self._pending_stroke:
            return
        node_name, component, stroke_uuid = self._pending_stroke
        self._pending_stroke = None
        self._broadcast_annotation(node_name, component, partial=False, stroke_uuid=stroke_uuid)

    def _construct_annotation_events(self, node_name, component, stroke_uuid=None):
        full_prop = f"{node_name}.{component}"
        is_text = component.startswith("text:")
        events = []
        
        if is_text:
            text_prop = f"{full_prop}.text"
            if not rv.commands.propertyExists(text_prop):
                return []
            text = rv.commands.getStringProperty(text_prop)
            text_val = text[0] if text else ""
            text_val = text_val.replace("\x01", "")
            
            # Skip soft-deleted text nodes
            soft_deleted_prop = f"{full_prop}.softDeleted"
            if rv.commands.propertyExists(soft_deleted_prop):
                if rv.commands.getIntProperty(soft_deleted_prop)[0]:
                    return []

            color = rv.commands.getFloatProperty(f"{full_prop}.color")
            position = rv.commands.getFloatProperty(f"{full_prop}.position")
            size = rv.commands.getFloatProperty(f"{full_prop}.size")
            spacing = rv.commands.getFloatProperty(f"{full_prop}.spacing")
            scale = rv.commands.getFloatProperty(f"{full_prop}.scale")
            rotation = rv.commands.getFloatProperty(f"{full_prop}.rotation")
            font = rv.commands.getStringProperty(f"{full_prop}.font")

            # Check for uuid or generate one
            uuid_prop = f"{full_prop}.uuid"
            if rv.commands.propertyExists(uuid_prop):
                ann_uuid = rv.commands.getStringProperty(uuid_prop)[0]
            else:
                ann_uuid = str(uuid.uuid4())
                rv.commands.newProperty(uuid_prop, rv.commands.StringType, 1)
                rv.commands.setStringProperty(uuid_prop, [ann_uuid], True)

            if not text_val.strip():
                return []

            # Map size: font_size in xstudio is around 50.0, RV size is around 0.01.
            # So: font_size = size[0] * 5000.0 if size else 50.0.
            r_size = size[0] if size else 0.01
            font_size = r_size * 5000.0

            try:
                otio.schema.schemadef.module_from_name('SyncEvent')
                text_event = otio.schemadef.SyncEvent.TextAnnotation(
                    rgba=list(color) if color else [1.0, 1.0, 1.0, 1.0],
                    position=list(position) if position else [0.0, 0.0],
                    spacing=spacing[0] if spacing else 0.0,
                    friendly_name=component.split(':')[-1],
                    font_size=float(font_size),
                    font=font[0] if font else "",
                    text=text_val,
                    rotation=rotation[0] if rotation else 0.0,
                    scale=scale[0] if scale else 1.0,
                    uuid=ann_uuid
                )
                event_data = json.loads(otio.adapters.write_to_string(text_event, "otio_json", indent=-1))
                events = [event_data]
            except Exception as e:
                _log(f"SEND annotation skipped: SyncEvent TextAnnotation serialisation failed: {e}")
                return []
        else:
            points = rv.commands.getFloatProperty(f"{full_prop}.points")
            if not points:
                return []
            color = rv.commands.getFloatProperty(f"{full_prop}.color")
            brush = rv.commands.getStringProperty(f"{full_prop}.brush")[0]
            width = rv.commands.getFloatProperty(f"{full_prop}.width")
            
            try:
                otio.schema.schemadef.module_from_name('SyncEvent')
                penuuid = stroke_uuid if stroke_uuid else str(uuid.uuid4())

                def _int_prop(prop, default=0):
                    try:
                        return rv.commands.getIntProperty(prop)[0]
                    except Exception:
                        return default

                hold         = bool(_int_prop(f"{full_prop}.hold"))
                ghost        = bool(_int_prop(f"{full_prop}.ghost"))
                ghost_before = _int_prop(f"{full_prop}.ghostBefore")
                ghost_after  = _int_prop(f"{full_prop}.ghostAfter")

                start_event = otio.schemadef.SyncEvent.PaintStart(
                    brush=brush,
                    rgba=list(color),
                    friendly_name=component.split(':')[-1],
                    uuid=penuuid,
                    hold=hold,
                    ghost=ghost,
                    ghost_before=ghost_before,
                    ghost_after=ghost_after,
                )
                mode_prop = f"{full_prop}.mode"
                if rv.commands.propertyExists(mode_prop) and rv.commands.getIntProperty(mode_prop)[0] == 1:
                    start_event.type = 'erase'

                x = [i for i in points[::2]]
                y = [i for i in points[1::2]]
                if len(width) == 1:
                    w = [width[0]] * (len(points) // 2)
                else:
                    w = [i for i in width]
                p = otio.schemadef.SyncEvent.PaintVertices(x, y, w)
                points_event = otio.schemadef.SyncEvent.PaintPoints(uuid=penuuid, points=p)

                start_event_data = json.loads(otio.adapters.write_to_string(start_event, "otio_json", indent=-1))
                points_event_data = json.loads(otio.adapters.write_to_string(points_event, "otio_json", indent=-1))
                events = [start_event_data, points_event_data]
            except Exception as e:
                _log(f"SEND annotation skipped: SyncEvent serialisation failed: {e}")
                return []
        
        return events

    def _broadcast_frame_annotations_replace(self, node_name, frame):
        try:
            order_prop = f"{node_name}.frame:{frame}.order"
            _log(f"REPLACE TEXT: node={node_name} frame={frame} order_prop={order_prop}")
            if not rv.commands.propertyExists(order_prop):
                _log("REPLACE TEXT: order_prop does not exist!")
                return
            items = rv.commands.getStringProperty(order_prop)
            _log(f"REPLACE TEXT: items={items}")
            all_events = []
            for component in items:
                events = self._construct_annotation_events(node_name, component)
                if events:
                    all_events.extend(events)
            
            _log(f"REPLACE TEXT: all_events count = {len(all_events)}")
            if not all_events:
                return

            media_path = self._resolve_media_path_for_paint_node(node_name)
            if not media_path:
                _log("REPLACE TEXT: no media_path")
                return
            clip_guid = self.plugin.playback._clip_guid_for_media_and_frame(media_path, frame)
            if not clip_guid:
                _log("REPLACE TEXT: no clip_guid")
                return

            clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
            sr = getattr(clip_obj, 'source_range', None)
            if sr is not None and int(sr.start_time.value) > 1:
                otio_frame = max(0, frame - int(sr.start_time.value))
            else:
                otio_frame = frame - 1 if frame > 0 else 0
            ann_clip_guid = self.plugin.sync_manager.annotation_clip_guid_at(clip_guid, otio_frame)
            _log(f"REPLACE TEXT: ann_clip_guid={ann_clip_guid}")
            if ann_clip_guid:
                current_sig = json.dumps(all_events, sort_keys=True)
                if self._last_sent_replace_sig.get(ann_clip_guid) == current_sig:
                    return
                self._last_sent_replace_sig[ann_clip_guid] = current_sig
                self.plugin.sync_manager.broadcast_replace_annotation_commands(ann_clip_guid, all_events)
            else:
                annotation_track_guid = self.plugin.sync_manager.annotation_track_guid_for_clip(
                    clip_guid, preferred_timeline_guid=self.plugin.sync_manager.active_timeline_guid
                )
                if annotation_track_guid:
                    fps = rv.commands.fps()
                    clip_local_time = otio.opentime.RationalTime(otio_frame, fps)
                    _log("REPLACE TEXT: broadcast_add_annotation (new clip)")
                    self.plugin.sync_manager.broadcast_add_annotation(
                        annotation_track_guid=annotation_track_guid,
                        clip_guid=clip_guid,
                        clip_local_time=clip_local_time,
                        events=all_events,
                    )
        except Exception as e:
            _log_exc(f"Failed to broadcast text replace: {e}")

    def _broadcast_annotation(self, node_name, component, partial=False, stroke_uuid=None):
        _log(f"SEND annotation node={node_name} component={component} partial={partial}")
        try:
            events = self._construct_annotation_events(node_name, component, stroke_uuid)
            if not events:
                return
            
            frame = int(component.split(":")[2])

            # Frame numbers in RV pen properties are clip-local, not global sequence
            # frames, so metaEvaluateClosestByType(frame) would land on the wrong clip.
            # Parse the node name instead to find the real source group.
            media_path = self._resolve_media_path_for_paint_node(node_name)

            if not events:
                _log("SEND annotation skipped: no events constructed")
                return
            if not media_path:
                _log("SEND annotation skipped: could not resolve media_path")
                return

            clip_guid = self.plugin.playback._clip_guid_for_media_and_frame(media_path, frame)
            if not clip_guid:
                _log(f"SEND annotation skipped: no clip_guid for media_path={media_path} frame={frame}")
                return
            annotation_track_guid = self.plugin.sync_manager.annotation_track_guid_for_clip(
                clip_guid,
                preferred_timeline_guid=self.plugin.sync_manager.active_timeline_guid,
            )
            if not annotation_track_guid:
                _log(f"SEND annotation skipped: no annotation track for clip {clip_guid}")
                return

            fps = rv.commands.fps()
            # Convert the paint-node media frame to a clip-local OTIO frame.
            # For timecode media the paint frame is the absolute media frame (e.g. 110)
            # and source_range.start is also 110, so clip-local = frame - start.
            # For native no-timecode (source_range.start in [0,1]), fall back to
            # frame - 1 (the existing correct behaviour).
            clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
            sr = getattr(clip_obj, 'source_range', None)
            if sr is not None and int(sr.start_time.value) > 1:
                otio_frame = max(0, frame - int(sr.start_time.value))
            else:
                otio_frame = frame - 1 if frame > 0 else 0
            if partial:
                self.plugin.sync_manager.broadcast_partial_annotation(
                    clip_guid=clip_guid,
                    frame=float(otio_frame),
                    fps=float(fps),
                    events=events,
                )
            else:
                clip_local_time = otio.opentime.RationalTime(otio_frame, fps)
                self.plugin.sync_manager.broadcast_add_annotation(
                    annotation_track_guid=annotation_track_guid,
                    clip_guid=clip_guid,
                    clip_local_time=clip_local_time,
                    events=events,
                )
        except Exception as e:
            _log_exc(f"Failed to broadcast annotation: {e}")

    def on_graph_state_change(self, event):
        contents = event.contents()
        if self._ignore_annotations_until > time.time():
            event.reject()
            return
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        # Channel change: RVDisplayColor.color.channelFlood written by r/g/b/a keys.
        # Broadcast immediately rather than waiting for the next poll tick.
        if "channelFlood" in contents:
            self.plugin.display._broadcast_display_state()
            event.reject()
            return

        # New stroke: paint.nextId incremented — flush the previous stroke (if
        # any) and prepare a fresh UUID.  The matching .points event that follows
        # will start the partial-broadcast timer.
        if re.search(r"\.paint\.nextId$", contents):
            if self._pending_stroke:
                self._stop_stroke_timers()
                self._flush_pending_stroke()
            self._next_stroke_uuid = str(uuid.uuid4())
            self._last_partial_point_count = 0
            event.reject()
            return

        # Pen point or text change: node.pen:N:F:user.points / node.text:N:F:user.prop
        is_pen = ".pen:" in contents and contents.endswith(".points")
        is_text = ".text:" in contents and not contents.endswith(".order")
        
        if is_text:
            parts = contents.split(".")
            if len(parts) >= 2:
                node_name, component = parts[0], parts[1]
                try:
                    frame = int(component.split(":")[2])
                    self._broadcast_frame_annotations_replace(node_name, frame)
                except Exception:
                    _log_exc(f"Failed to parse frame from text update: {contents}")
            event.reject()
            return
            
        if is_pen:
            parts = contents.split(".")
            if len(parts) >= 2:
                node_name, component = parts[0], parts[1]
                # Consume the UUID prepared by paint.nextId
                stroke_uuid = self._next_stroke_uuid or str(uuid.uuid4())
                self._next_stroke_uuid = None
                self._pending_stroke = (node_name, component, stroke_uuid)
                # Repeating partial broadcast (50 ms) — fires while user is drawing.
                if self._stroke_timer is None:
                    self._stroke_timer = QtCore.QTimer()
                    self._stroke_timer.timeout.connect(self._send_partial_stroke)
                if not self._stroke_timer.isActive():
                    self._stroke_timer.start(50)
        else:
            # Log unhandled graph-state-change contents so we can identify
            # what RV fires for sequence reorders and other structural changes.
            _log(f"graph-state-change (unhandled): {contents!r}")
        event.reject()
