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

from utils import _log, _log_exc, _media_path, _clip_effective_range
from otio_sync_core.manager import STATE_SYNCED
from otio_sync_core import rv_annotation_codec, rv_paint_applier


def _ensure_workspace_sync_event():
    import sys
    import os
    import importlib.util
    try:
        workspace_sync_event_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "otio_event_plugin", "schemadefs", "SyncEvent.py"))
        import opentimelineio as otio
        has_ellipse = hasattr(getattr(otio.schemadef, 'SyncEvent', None), 'EllipseAnnotation')
        if os.path.exists(workspace_sync_event_path) and not has_ellipse:
            spec = importlib.util.spec_from_file_location("opentimelineio.schemadef.SyncEvent", workspace_sync_event_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["opentimelineio.schemadef.SyncEvent"] = module
            otio.schemadef.SyncEvent = module
            spec.loader.exec_module(module)
        otio.schema.schemadef.module_from_name('SyncEvent')
    except Exception as e:
        _log(f"Failed to ensure workspace SyncEvent: {e}")


class AnnotationSyncController:
    #: Coalescing interval (ms) for mid-drag shape broadcasts, mirroring the
    #: pen stroke timer.  Without this, a shape drag (rect/ellipse/arrow) fires
    #: an immediate, unthrottled network broadcast on every mouse-move.
    SHAPE_BROADCAST_INTERVAL_MS = 50

    def __init__(self, plugin):
        self.plugin = plugin
        _ensure_workspace_sync_event()
        self._pending_stroke = None            # (node_name, pen_component, stroke_uuid)
        self._next_stroke_uuid = None          # set when paint.nextId fires; consumed on first .points
        self._stroke_timer = None              # repeating partial-broadcast timer during drawing
        self._last_partial_point_count = 0
        self._partial_pen_nodes = {}           # stroke_uuid → rv pen node name (e.g. "pen:3:42:remote")
        self._partial_pen_nodes_by_key = {}    # (clip_guid, rv_frame) → [pen node names created mid-gesture]
        self._live_stroke_node = {}            # (clip_guid, rv_frame) → in-progress pen node name
        self._last_sent_replace_sig = {}       # ann_clip_guid → JSON sig of last broadcast
        self._ignore_annotations_until = 0.0
        self._pending_shape = None             # (node_name, frame) awaiting a coalesced broadcast
        self._shape_timer = None               # repeating partial-broadcast timer during shape drag

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

    def _frame_base_for_paint_node(self, node_name):
        """Return the media start frame (TC-aware) for the source backing node_name.

        rv.commands.frameStart() returns the SEQUENCE start (1) when RV is
        viewing a sequence paint node — not the underlying media TC start (e.g.
        89899). sourceMediaInfo gives the correct per-source start frame
        regardless of what is currently displayed.
        """
        source_group = None
        if node_name.endswith("_paint"):
            source_group = node_name[:-len("_paint")]
        elif "_p_" in node_name:
            display_slot = node_name.split("_p_")[1]
            if re.match(r'^sourceGroup\d+$', display_slot):
                source_group = display_slot
        if source_group:
            try:
                for n in rv.commands.nodesInGroup(source_group):
                    if rv.commands.nodeType(n) == "RVFileSource":
                        info = rv.commands.sourceMediaInfo(n)
                        sf = info.get("startFrame")
                        if sf is not None:
                            return int(sf)
            except Exception:
                pass
        return self.plugin.playback._frame_base()

    def _media_frame_base(self, media_path):
        """Return the media start frame (TC-aware) for a given media path.

        Used in the receive path where we have media_path but not a paint node
        name.  Queries sourceMediaInfo on the RVFileSource directly so the result
        is always the real TC start (e.g. 89899), never the sequence start (1),
        which is what rv.commands.frameStart() returns when the sequence view is
        active in flat-playlist mode.
        """
        source_group = self.plugin.sequence._path_to_source_group_map().get(media_path)
        if source_group:
            try:
                for n in rv.commands.nodesInGroup(source_group):
                    if rv.commands.nodeType(n) == "RVFileSource":
                        info = rv.commands.sourceMediaInfo(n)
                        sf = info.get("startFrame")
                        if sf is not None:
                            return int(sf)
            except Exception:
                pass
        return self.plugin.playback._frame_base()

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

        The sender does not keep a stable ``uuid`` for a gesture across
        ticks — every partial broadcast mints a fresh one, even though the
        point list is cumulative from the same gesture start. Worse, RV does
        not allow mutating a dynamically-created pen node's properties from
        outside the call that created it (verified experimentally:
        ``commands.setFloatProperty`` throws ``"invalid property name"`` even
        long after creation, not just immediately after — a hard constraint,
        not a timing quirk), so true in-place updates aren't possible either.

        Continuity is instead tracked by ``(clip_guid, rv_frame)``: at most
        one live gesture is assumed in flight per clip+frame at a time
        (mirroring the sender's own assumption). Every tick creates a fresh
        pen node with this tick's full cumulative point list, then removes
        the *previous* tick's node for that key — so only the latest,
        longest version of the growing stroke is ever visible, instead of
        every tick's node piling up and overlapping. The OTIO timeline is not
        modified.

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
            rv_frame = self._media_frame_base(media_path) + clip_local

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

            key = (clip_guid, rv_frame)
            color = list(ev_dict.rgba) if ev_dict.rgba else [1.0, 1.0, 1.0, 1.0]
            brush = ev_dict.brush or "circle"
            widths = list(pts_ev.points.size) if pts_ev.points.size else [2.0]
            mode = 1 if getattr(ev_dict, "type", "color") == "erase" else 0
            self._apply_annotation({
                "media_path": media_path,
                "frame": rv_frame,
                "clip_guid": clip_guid,
                "node_name": None,
                "points": points_flat,
                "color": color,
                "brush": brush,
                "width": widths,
                "mode": mode,
                "_stroke_uuid": stroke_uuid,
            })
            new_pen_node = self._partial_pen_nodes.pop(stroke_uuid, None)
            if new_pen_node:
                self._live_stroke_node[key] = new_pen_node
                self._partial_pen_nodes_by_key.setdefault(key, []).append(new_pen_node)
                # Sweep away the previous tick's node(s) for this gesture now
                # that this longer one supersedes them.
                self._cleanup_partial_debris(node, clip_guid, rv_frame, keep=new_pen_node)

    def _cleanup_partial_debris(self, node, clip_guid, rv_frame, keep=None):
        """Remove stray mid-gesture pen nodes once a gesture's final stroke lands.

        Each partial tick for a drag mints its own pen node (see
        :meth:`_apply_partial_annotation`) because the sender's live-stroke
        broadcast does not reuse a stable uuid across a gesture — so without
        this sweep, every intermediate node would linger in the frame's
        ``order`` forever, piling up overlapping fragments at the gesture's
        start point. Mirrors xStudio's own ``refresh_annotation_bookmark``,
        which unconditionally rebuilds from the authoritative final state
        when a gesture completes.

        :param node: The paint node the gesture's frame lives on.
        :param clip_guid: The synced clip guid the gesture belongs to.
        :param rv_frame: The RV paint frame the gesture belongs to.
        :param keep: A pen node name to keep even if it was tracked as
            partial debris (e.g. it was reused/finalised in place).
        """
        stale = self._partial_pen_nodes_by_key.pop((clip_guid, rv_frame), [])
        if not stale or not node:
            return
        to_remove = set(stale)
        to_remove.discard(keep)
        if not to_remove:
            return
        order_prop = f"{node}.frame:{rv_frame}.order"
        # Not gated on rv.commands.propertyExists() first -- as elsewhere in
        # this file, that check has been observed to unreliably report False
        # for a property that was in fact just written; read/write it
        # directly and treat a genuine absence as a no-op via the exception.
        try:
            order = list(rv.commands.getStringProperty(order_prop) or [])
        except Exception:
            return
        new_order = [item for item in order if item not in to_remove]
        if len(new_order) != len(order):
            try:
                rv.commands.setStringProperty(order_prop, new_order, True)
            except Exception as e:
                _log(f"_cleanup_partial_debris: failed to prune order: {e}")

    def _finalize_pen_stroke_events(self, stroke_events, media_path, clip_guid, rv_frame,
                                     paint_node_cache=None) -> int:
        """Group PaintStart/PaintPoints pairs by uuid and apply each as the
        authoritative, fully-committed stroke.

        Called from the ``insert_child``/``annotation_commands_added`` delta
        path (:meth:`_apply_annotation_render`) — a true delta, each stroke
        appearing exactly once, never resent — so no idempotency bookkeeping
        is needed here beyond what :meth:`_cleanup_partial_debris` already
        does. (Deliberately NOT wired into :meth:`_apply_annotation_replace`
        too: that path resends the clip's entire cumulative
        ``annotation_commands`` on every call with fresh random uuids each
        time, which would make every resend look like a brand new stroke.)

        RV does not allow mutating a dynamically-created pen node's
        properties from outside the call that created it (a hard
        constraint — see :meth:`_apply_partial_annotation`), so every
        completed gesture here is applied as a brand-new node; any
        mid-gesture partial nodes tracked for it are then swept away via
        :meth:`_cleanup_partial_debris` rather than reused.

        :param stroke_events: Deserialised ``PaintStart``/``PaintPoints``/
            ``PaintEnd`` SyncEvent objects (other kinds are ignored).
        :returns: Number of strokes finalised.
        """
        event_groups = {}
        for ev in stroke_events:
            ev_uuid = getattr(ev, "uuid", None) or str(id(ev))
            if ev_uuid not in event_groups:
                event_groups[ev_uuid] = {"start": None, "points": None}
            if isinstance(ev, otio.schemadef.SyncEvent.PaintStart):
                event_groups[ev_uuid]["start"] = ev
            elif isinstance(ev, otio.schemadef.SyncEvent.PaintPoints):
                event_groups[ev_uuid]["points"] = ev

        rendered = 0
        for grp in event_groups.values():
            start_event = grp["start"]
            points_event = grp["points"]
            if not start_event or not points_event:
                continue
            ev_uuid = getattr(start_event, "uuid", None)
            node = paint_node_cache or self._find_paint_node_for_media(media_path, rv_frame, clip_guid)
            if ev_uuid:
                self._partial_pen_nodes.pop(ev_uuid, None)

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
                "mode": 1 if getattr(start_event, "type", "color") == "erase" else 0,
                "_stroke_uuid": ev_uuid,
            })
            rendered += 1
            new_pen_node = self._partial_pen_nodes.pop(ev_uuid, None) if ev_uuid else None
            self._cleanup_partial_debris(node, clip_guid, rv_frame, keep=new_pen_node)
            self._live_stroke_node.pop((clip_guid, rv_frame), None)
        return rendered

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
            rv_frame = self._media_frame_base(media_path) + _clip_local

        try:
            otio.schema.schemadef.module_from_name('SyncEvent')
        except Exception:
            pass

        # Stroke (PaintStart/PaintPoints) events are collected and finalised
        # together via _finalize_pen_stroke_events, so that multi-stroke
        # deltas (e.g. when the user draws several strokes before the
        # debounce fires) are all rendered, not just the last pair.
        stroke_events = []
        # TextAnnotation specs are collected and reconciled together, in one
        # apply_specs(mode="reconcile") call, for the same reason strokes are
        # batched above: reconcile mode prunes every existing node of a kind
        # not represented in *that call's* spec list, so calling it once per
        # event (with a single-item spec list each time) makes each text spec
        # prune away every *other* text node just written moments earlier by
        # the previous iteration — e.g. two captions on one bookmark would
        # each wipe the other out in turn instead of coexisting.
        text_specs = []
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
                    specs = rv_annotation_codec.sync_events_to_rv_specs([ev], {"frame": rv_frame})
                    for spec in specs:
                        spec["user"] = "remote"
                        spec["props"] = [
                            (name, ptype, [ev.font or ""] if name == "font" else val, dim)
                            for (name, ptype, val, dim) in spec["props"]
                        ]
                    text_specs.extend(specs)
                    _log(f"RECV annotation: queued text uuid={uuid_val[:8]!r} (text={text_val!r}) for reconcile")
                    rendered += 1
                elif isinstance(ev, otio.schemadef.SyncEvent.EllipseAnnotation):
                    self._apply_shape_annotation({
                        "media_path": media_path,
                        "frame": rv_frame,
                        "clip_guid": clip_guid,
                        "node_name": None,
                        "type": "ellipse",
                        "min": list(ev.min),
                        "max": list(ev.max),
                        "rgba": list(ev.rgba),
                        "size": ev.size,
                        "inner_rgba": list(ev.inner_rgba),
                        "uuid": ev.uuid or str(uuid.uuid4()),
                    })
                    rendered += 1
                elif isinstance(ev, otio.schemadef.SyncEvent.RectangleAnnotation):
                    self._apply_shape_annotation({
                        "media_path": media_path,
                        "frame": rv_frame,
                        "clip_guid": clip_guid,
                        "node_name": None,
                        "type": "rect",
                        "min": list(ev.min),
                        "max": list(ev.max),
                        "rgba": list(ev.rgba),
                        "size": ev.size,
                        "inner_rgba": list(ev.inner_rgba),
                        "uuid": ev.uuid or str(uuid.uuid4()),
                    })
                    rendered += 1
                elif isinstance(ev, otio.schemadef.SyncEvent.ArrowAnnotation):
                    self._apply_shape_annotation({
                        "media_path": media_path,
                        "frame": rv_frame,
                        "clip_guid": clip_guid,
                        "node_name": None,
                        "type": "arrow",
                        "start": list(ev.start),
                        "end": list(ev.end),
                        "rgba": list(ev.rgba),
                        "size": ev.size,
                        "uuid": ev.uuid or str(uuid.uuid4()),
                    })
                    rendered += 1
                else:
                    stroke_events.append(ev)
            except Exception as e:
                _log(f"RECV annotation: failed to deserialise event: {e}")
                pass

        if text_specs:
            node = _paint_node_cache or self._find_paint_node_for_media(media_path, rv_frame, clip_guid)
            if node:
                # prune=False: this is an insert_child delta (one more
                # annotation clip layered onto a frame that may already have
                # others), not the complete set of text for this frame — e.g.
                # a caption added afterwards on its own, delta-only clip must
                # not prune an earlier, unrelated caption still on this frame.
                rv_paint_applier.apply_specs(
                    text_specs, rv.commands, rv_node=node, frame=rv_frame, mode="reconcile", prune=False
                )
                _log(f"RECV annotation: reconciled {len(text_specs)} text spec(s) in one batch (prune=False)")
                if QtCore:
                    QtCore.QTimer.singleShot(0, rv.commands.redraw)
            else:
                _log("RECV annotation: no paint node for queued text specs")

        rendered += self._finalize_pen_stroke_events(
            stroke_events, media_path, clip_guid, rv_frame, _paint_node_cache
        )

        if rendered == 0:
            _log("RECV annotation: no valid annotation events found")

    def _apply_annotation_replace(self, ann_clip):
        """Apply a full annotation_commands replacement to RV paint.

        Called when a peer sends ``annotation_commands_replaced`` (e.g. a text
        edit or drag-move in xStudio). Uses the shared codec's reconcile mode:
        text/shape commands are matched by uuid and updated in place, added if
        new, and any existing managed node whose uuid is absent from this
        replacement is pruned (the "replace" semantics — this payload becomes
        the complete set of text/shape annotations for the frame).

        Stroke commands (``PaintStart``/``PaintPoints``/``PaintEnd``) are
        excluded — they are already painted in RV and are not part of a
        "replace" (text-edit / drag-move) broadcast. They also MUST NOT be
        routed through :meth:`_finalize_pen_stroke_events` here: unlike
        ``insert_child`` deltas, this payload's stroke uuids are not stable
        across repeated ``REPLACE_ANNOTATION_COMMANDS`` broadcasts for the
        same clip (each resend re-mints fresh random uuids for the same
        underlying strokes), so uuid-keyed finalisation would treat every
        resend as new strokes and duplicate them without bound.

        Note: reconcile-mode updates overwrite every property the codec
        writes (including ``softDeleted``, always 0), whereas the original
        surgical per-field update never touched ``softDeleted``. Nothing in
        this codebase ever sets it to 1, so this is a low-probability,
        accepted divergence rather than one worth extra machinery to avoid.
        """
        clip_guid = ann_clip.metadata.get("clip_guid")
        events_data = ann_clip.metadata.get("annotation_commands", [])

        media_clip = self.plugin.sync_manager._object_map.get(clip_guid) if clip_guid else None
        if not isinstance(media_clip, otio.schema.Clip):
            _log(f"RECV annotation replace: no media Clip for guid={clip_guid}")
            return
        ref = media_clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
            return

        _clip_local = int(ann_clip.source_range.start_time.value) if ann_clip.source_range else 0
        media_path = _media_path(ref.target_url)
        _sr = media_clip.source_range
        if _sr is not None and int(_sr.start_time.value) > 1:
            rv_frame = int(_sr.start_time.value) + _clip_local
        else:
            rv_frame = self._media_frame_base(media_path) + _clip_local

        node = self._find_paint_node_for_media(media_path, rv_frame, clip_guid)
        if not node:
            _log(f"RECV annotation replace: no paint node for media_path={media_path} frame={rv_frame}")
            return

        _STROKE_TYPES = (otio.schemadef.SyncEvent.PaintStart, otio.schemadef.SyncEvent.PaintPoints,
                          otio.schemadef.SyncEvent.PaintEnd)

        specs = []
        for ev in events_data:
            try:
                if isinstance(ev, (dict, collections.abc.Mapping)):
                    ev = otio.adapters.read_from_string(otio.adapters.write_to_string(ev, "otio_json"), "otio_json")
            except Exception as e:
                _log(f"RECV annotation replace: failed to deserialise event: {e}")
                continue
            if isinstance(ev, _STROKE_TYPES):
                continue

            ev_specs = rv_annotation_codec.sync_events_to_rv_specs([ev], {"frame": rv_frame})
            for spec in ev_specs:
                spec["user"] = "remote"
                if spec["kind"] == "text":
                    font_val = getattr(ev, "font", "") or ""
                    spec["props"] = [
                        (name, ptype, [font_val] if name == "font" else val, dim)
                        for (name, ptype, val, dim) in spec["props"]
                    ]
                _log(f"RECV annotation replace: reconciling {spec['kind']} uuid={spec['uuid'][:8]!r}")
            specs.extend(ev_specs)

        rv_paint_applier.apply_specs(specs, rv.commands, rv_node=node, frame=rv_frame, mode="reconcile")

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
                    if clip is None:
                        _log(f"  _find_paint_node: clip_guid={clip_guid} not in _object_map — "
                             f"seq_frame falls back to raw frame={frame}")
                    elif not clip.parent():
                        _log(f"  _find_paint_node: clip_guid={clip_guid} found but clip.parent() is falsy — "
                             f"seq_frame falls back to raw frame={frame}")
                    else:
                        rip = clip.trimmed_range_in_parent()
                        if rip is None:
                            _log(f"  _find_paint_node: clip_guid={clip_guid} trimmed_range_in_parent() "
                                 f"returned None — seq_frame falls back to raw frame={frame}")
                        else:
                            # clip.source_range is often None (a legitimate OTIO
                            # state meaning "use the whole available_range") —
                            # xStudio stores the real embedded-timecode start on
                            # media_reference.available_range in that case, so
                            # checking source_range alone would misdetect a
                            # genuine timecode clip as native/no-timecode.
                            effective = _clip_effective_range(clip)
                            frame_base = self.plugin.playback._frame_base()
                            if effective is not None and effective[0] > 1:
                                clip_local = frame - effective[0]
                            else:
                                clip_local = frame - 1
                            seq_frame = int(rip.start_time.value) + clip_local + frame_base
                            _log(f"  _find_paint_node: seq_frame computed OK — "
                                 f"rip.start={rip.start_time.value} clip_local={clip_local} "
                                 f"frame_base={frame_base} effective_start={effective[0] if effective else None} "
                                 f"-> seq_frame={seq_frame}")
                except Exception as e:
                    _log(f"  _find_paint_node: exception computing seq_frame for clip_guid={clip_guid}: {e} — "
                         f"seq_frame falls back to raw frame={frame}")
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
        """Write a received pen/erase stroke to RV paint via the shared codec.

        ``hold``/``ghost``/``ghostBefore``/``ghostAfter`` are local RV display
        properties, not part of the sync schema — they are always written as
        fixed defaults (0) here, never derived from network data (matching
        ``_construct_annotation_events``'s send-side note).
        """
        try:
            frame = data.get("frame")
            points = data.get("points")
            color = data.get("color")
            brush = data.get("brush")
            width = data.get("width", [2.0])
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

            # Build a synthetic PaintStart/PaintPoints pair (in-memory only —
            # never persisted) so the shared codec's tested pen-spec logic
            # (width scale, splat/gauss, erase mode) is reused verbatim.
            se = otio.schemadef.SyncEvent
            pairing_uuid = data.get("_stroke_uuid") or str(uuid.uuid4())
            start_event = se.PaintStart(brush=brush, rgba=list(color), friendly_name="remote", uuid=pairing_uuid)
            if data.get("mode", 0) == 1:
                start_event.type = "erase"
            x = [p for p in points[0::2]]
            y = [p for p in points[1::2]]
            points_event = se.PaintPoints(uuid=pairing_uuid, points=se.PaintVertices(x, y, list(width)))

            specs = rv_annotation_codec.sync_events_to_rv_specs([start_event, points_event], {"frame": frame})
            for spec in specs:
                spec["user"] = "remote"
            rv_paint_applier.apply_specs(specs, rv.commands, rv_node=node, frame=frame, mode="append")

            order_prop = f"{node}.frame:{frame}.order"
            pen_items = [i for i in rv.commands.getStringProperty(order_prop) if i.startswith(("pen:",))]
            if not pen_items:
                return
            pen_node = pen_items[-1]
            full_pen = f"{node}.{pen_node}"
            for prop_name in ("hold", "ghost", "ghostBefore", "ghostAfter"):
                if not rv.commands.propertyExists(f"{full_pen}.{prop_name}"):
                    rv.commands.newProperty(f"{full_pen}.{prop_name}", rv.commands.IntType, 1)
                rv.commands.setIntProperty(f"{full_pen}.{prop_name}", [0], True)
            _log(f"  _apply_annotation: wrote {pen_node} to {order_prop}")

            # Record UUID→pen_node so partial updates can find this node,
            # and so the final INSERT_CHILD render can skip re-creating it.
            stroke_uuid = data.get("_stroke_uuid")
            if stroke_uuid:
                self._partial_pen_nodes[stroke_uuid] = pen_node
            if QtCore:
                QtCore.QTimer.singleShot(0, rv.commands.redraw)
        except Exception as e:
            _log_exc(f"Failed to apply remote annotation: {e}")

    def _apply_text_annotation(self, data):
        """Write a received TextAnnotation to RV paint via the shared codec.

        ``font`` is passed through from the received value here — unlike the
        batch/load-plugin paths, which intentionally leave RV's text ``font``
        property blank (see ``rv_annotation_codec._text_spec``), live-sync has
        always relayed the real font name, so the codec's default is overridden
        for this call site to preserve that pre-existing behaviour.
        """
        try:
            frame = data.get("frame")
            node_name = data.get("node_name")
            media_path = data.get("media_path")
            ann_clip_guid = data.get("clip_guid")
            text = data.get("text", "")
            uuid_val = data.get("uuid", "")

            _log(f"RECV text annotation frame={frame} text={text} uuid={uuid_val}")
            node = self._find_paint_node_for_media(media_path, frame, ann_clip_guid)
            _log(f"  _apply_text_annotation: using node={node}")
            if not node:
                if node_name and rv.commands.nodeExists(node_name):
                    node = node_name
                else:
                    _log(f"RECV text annotation dropped: no paint node for media_path={media_path} frame={frame}")
                    return

            se = otio.schemadef.SyncEvent
            text_event = se.TextAnnotation(
                rgba=list(data.get("color", [1.0, 1.0, 1.0, 1.0])),
                position=list(data.get("position", [0.0, 0.0])),
                spacing=float(data.get("spacing", 0.8)),
                friendly_name="remote",
                font_size=rv_annotation_codec.rv_to_font_size(data.get("size", 0.01)),
                font=data.get("font", ""),
                text=text,
                rotation=float(data.get("rotation", 0.0)),
                scale=float(data.get("scale", 1.0)),
                uuid=uuid_val,
            )
            specs = rv_annotation_codec.sync_events_to_rv_specs([text_event], {"frame": frame})
            for spec in specs:
                spec["user"] = "remote"
                spec["props"] = [
                    (name, ptype, [data.get("font", "")] if name == "font" else val, dim)
                    for (name, ptype, val, dim) in spec["props"]
                ]
            rv_paint_applier.apply_specs(specs, rv.commands, rv_node=node, frame=frame, mode="append")
            _log(f"  _apply_text_annotation: wrote text node to {node}.frame:{frame}.order")
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

    def _send_partial_shape(self):
        """Repeating timer callback: coalesce rapid shape-drag graph-state-change
        events into a single throttled broadcast, mirroring the pen stroke timer."""
        if not self._pending_shape:
            if self._shape_timer:
                self._shape_timer.stop()
            return
        node_name, frame = self._pending_shape
        self._pending_shape = None
        self._broadcast_frame_annotations_replace(node_name, frame)

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

    def _apply_shape_annotation(self, data):
        """Write a received shape annotation (ellipse/rect/arrow) to RV paint
        via the shared codec, using reconcile mode: a duplicate broadcast
        (same uuid) updates the existing node in place instead of the manual
        skip-if-duplicate check this used to do (a safe superset — idempotent
        either way, and correctly applies an edit if the data did change)."""
        try:
            frame = data.get("frame")
            shape_type = data.get("type")
            media_path = data.get("media_path")
            ann_clip_guid = data.get("clip_guid")
            node_name = data.get("node_name")
            uuid_val = data.get("uuid")

            _log(f"RECV shape annotation frame={frame} type={shape_type} uuid={uuid_val}")
            node = self._find_paint_node_for_media(media_path, frame, ann_clip_guid)
            _log(f"  _apply_shape_annotation: using node={node}")
            if not node:
                if node_name and rv.commands.nodeExists(node_name):
                    node = node_name
                else:
                    _log(f"RECV shape annotation dropped: no paint node for media_path={media_path} frame={frame}")
                    return

            se = otio.schemadef.SyncEvent
            if shape_type == "arrow":
                shape_event = se.ArrowAnnotation(
                    start=list(data.get("start")), end=list(data.get("end")),
                    rgba=list(data.get("rgba")), size=float(data.get("size", 1.0)), uuid=uuid_val)
            else:
                cls = se.EllipseAnnotation if shape_type == "ellipse" else se.RectangleAnnotation
                shape_event = cls(
                    min=list(data.get("min")), max=list(data.get("max")),
                    rgba=list(data.get("rgba")), size=float(data.get("size", 1.0)),
                    inner_rgba=list(data.get("inner_rgba")), uuid=uuid_val)

            specs = rv_annotation_codec.sync_events_to_rv_specs([shape_event], {"frame": frame})
            for spec in specs:
                spec["user"] = "remote"
            # prune=False: only ever called from the insert_child path (an
            # incremental delta), never a true replace — see the matching
            # comment on the text batch call in _apply_annotation_render.
            rv_paint_applier.apply_specs(specs, rv.commands, rv_node=node, frame=frame, mode="reconcile", prune=False)
            _log(f"  _apply_shape_annotation: wrote {shape_type} node to {node}.frame:{frame}.order (prune=False)")
            if QtCore:
                QtCore.QTimer.singleShot(0, rv.commands.redraw)
        except Exception as e:
            _log_exc(f"Failed to apply remote shape annotation: {e}")

    #: For each order-item prefix, the RV property whose *existence* signals
    #: the node is fully written (guards against reading mid-creation).
    _DISCRIMINATOR_PROP = {
        "pen:": "points", "text:": "text",
        "rect:": "min", "ellipse:": "min", "arrow:": "startPos",
    }

    def _construct_annotation_events(self, node_name, component, stroke_uuid=None):
        """Read one RV paint child node and return its JSON-serialised SyncEvents.

        The live-sync counterpart to :func:`export_annotations`'s per-frame read
        (see ``rv_paint_applier.read_stroke`` / ``rv_annotation_codec``), but for
        a single named component and returning wire-ready dicts. Note fields
        ``hold``/``ghost``/``ghost_before``/``ghost_after`` are intentionally
        NOT read into the outbound event — these are local RV display concerns,
        not part of the sync schema (see ``_apply_annotation``'s matching note).
        """
        full_prop = f"{node_name}.{component}"
        prefix = next((p for p in self._DISCRIMINATOR_PROP if component.startswith(p)), None)
        if prefix is None:
            return []
        if not rv.commands.propertyExists(f"{full_prop}.{self._DISCRIMINATOR_PROP[prefix]}"):
            return []

        is_text = prefix == "text:"
        is_shape = prefix in ("rect:", "ellipse:", "arrow:")

        # Text/shape UUIDs must be stable across repeated broadcasts (e.g. a
        # text edit re-sends the same node), so persist one if missing. Pens
        # track their UUID in-memory instead (_pending_stroke/_partial_pen_nodes)
        # and never persist a `.uuid` property, matching pre-existing behaviour.
        if is_text or is_shape:
            uuid_prop = f"{full_prop}.uuid"
            if rv.commands.propertyExists(uuid_prop):
                ann_uuid = rv.commands.getStringProperty(uuid_prop)[0]
            else:
                ann_uuid = str(uuid.uuid4())
                rv.commands.newProperty(uuid_prop, rv.commands.StringType, 1)
                rv.commands.setStringProperty(uuid_prop, [ann_uuid], True)
        else:
            ann_uuid = stroke_uuid if stroke_uuid else str(uuid.uuid4())

        if is_text:
            soft_deleted_prop = f"{full_prop}.softDeleted"
            if rv.commands.propertyExists(soft_deleted_prop) and rv.commands.getIntProperty(soft_deleted_prop)[0]:
                return []

        stroke = rv_paint_applier.read_stroke(rv.commands, node_name, component)
        if stroke is None:
            return []
        stroke["uuid"] = ann_uuid

        if is_text:
            stroke["text"] = (stroke.get("text") or "").replace("\x01", "")
            if not stroke["text"].strip():
                return []

        try:
            events = rv_annotation_codec.rv_strokes_to_sync_events([stroke])
            if not events:
                return []
            return [json.loads(otio.adapters.write_to_string(ev, "otio_json", indent=-1)) for ev in events]
        except Exception as e:
            _log(f"SEND annotation skipped: SyncEvent serialisation failed: {e}")
            return []

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
                otio_frame = max(0, frame - self._frame_base_for_paint_node(node_name))
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
                if not annotation_track_guid:
                    is_new = clip_guid not in self.plugin.sync_manager._clip_timelines
                    clip_tl_guid = self.plugin.sync_manager.get_or_create_clip_timeline(clip_guid)
                    if clip_tl_guid:
                        if is_new:
                            self.plugin.sync_manager.broadcast_clip_timeline(clip_tl_guid)
                        self.plugin.sync_manager.active_timeline_guid = clip_tl_guid
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
                # No track yet (e.g. flat-playlist first annotation) — create the
                # clip timeline locally and broadcast it so xStudio registers the
                # Annotations track, then re-look up.
                is_new = clip_guid not in self.plugin.sync_manager._clip_timelines
                clip_tl_guid = self.plugin.sync_manager.get_or_create_clip_timeline(clip_guid)
                if clip_tl_guid:
                    if is_new:
                        self.plugin.sync_manager.broadcast_clip_timeline(clip_tl_guid)
                    self.plugin.sync_manager.active_timeline_guid = clip_tl_guid
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
            clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
            sr = getattr(clip_obj, 'source_range', None)
            if sr is not None and int(sr.start_time.value) > 1:
                otio_frame = max(0, frame - int(sr.start_time.value))
            else:
                otio_frame = max(0, frame - self._frame_base_for_paint_node(node_name))
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

        # Pen point or text/shape change: node.pen:N:F:user.points / node.text:N:F:user.prop
        is_pen = ".pen:" in contents and contents.endswith(".points")
        is_text = ".text:" in contents and not contents.endswith(".order")
        is_shape = (".rect:" in contents or ".ellipse:" in contents or ".arrow:" in contents) and not contents.endswith(".order")
        
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

        if is_shape:
            parts = contents.split(".")
            if len(parts) >= 2:
                node_name, component = parts[0], parts[1]
                try:
                    frame = int(component.split(":")[2])
                    # Coalesce rapid shape-drag updates instead of broadcasting
                    # on every mouse-move (mirrors the pen stroke timer).
                    self._pending_shape = (node_name, frame)
                    if self._shape_timer is None:
                        self._shape_timer = QtCore.QTimer()
                        self._shape_timer.timeout.connect(self._send_partial_shape)
                    if not self._shape_timer.isActive():
                        self._shape_timer.start(self.SHAPE_BROADCAST_INTERVAL_MS)
                except Exception:
                    _log_exc(f"Failed to parse frame from shape update: {contents}")
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
