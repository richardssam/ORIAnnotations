import os
import time
import json
import copy
import collections.abc
from collections import Counter
import rv.commands

try:
    import opentimelineio as otio
except ImportError:
    otio = None

try:
    from otio_sync_core.manager import STATE_SYNCED
except ImportError:
    STATE_SYNCED = "synced"

try:
    from otio_sync_core.protocol_messages import ORIGIN_NATIVE, ORIGIN_OTIO_IMPORT
except ImportError:
    ORIGIN_NATIVE = "native"
    ORIGIN_OTIO_IMPORT = "otio_import"

from otio_sync_core.rv_annotation_codec import font_size_to_rv

# RV's native OTIO reader/writer (the `otio_reader` rv-package). Imported
# defensively: a build without the package must degrade to native handling
# rather than crash, so OTIO-origin timelines are simply not snapshot-synced.
try:
    import otio_reader as rv_otio_reader
    import otio_writer as rv_otio_writer
except Exception:  # pragma: no cover - depends on the RV runtime
    rv_otio_reader = None
    rv_otio_writer = None

from utils import _log, _log_exc, _media_path, _is_media_track

#: RV's built-in default containers, never treated as shared sync timelines.
_DEFAULT_CONTAINERS = ("defaultSequence", "defaultStack")
#: ``otio`` component properties RV's otio_reader stamps on an imported Stack.
#: ``timeline_name`` is written whenever the timeline has a name (so it is
#: present even when the source .otio carried empty metadata), making it the
#: most reliable OTIO-origin marker; the metadata variants appear only when the
#: source objects had non-empty metadata.
_OTIO_STACK_MARKERS = ("otio.timeline_name", "otio.timeline_metadata", "otio.metadata")


class SequenceSyncController:
    def __init__(self, plugin):
        self.plugin = plugin
        self._rv_node_to_timeline_guid = {}
        self._sequence_input_order = {}
        self._sg_to_path_cache = {}
        self._sequence_settle_until = {}
        self._active_media_track_guid = None
        self._track = None
        # OTIO-origin snapshot sync state (§4):
        #   _otio_stack_to_guid : RVStackGroup node -> timeline guid it maps to
        #   _otio_last_export   : timeline guid -> last-broadcast serialized OTIO
        #   _otio_guid_to_root  : timeline guid -> applied root node (peer side)
        #   _otio_dirty         : set by STACKS/SEQUENCES graph events to gate diffs
        self._otio_stack_to_guid = {}
        self._otio_last_export = {}
        self._otio_guid_to_root = {}
        self._otio_dirty = True
        # Bin clip guid → media_path, populated from flat-playlist ADD_TIMELINE
        # payloads and kept across REMOVE_TIMELINE so _switch_to_source_view can
        # still resolve a bin clip guid after the flat playlist is replaced by a
        # real sequence (which has different per-clip guids).
        self._bin_guid_to_path = {}

    @staticmethod
    def _normalize_seq_name(name):
        """Normalise a sequence name for tolerant cross-app matching.

        Mirrors ``state_projection.normalize_clip_name`` so adoption here agrees
        with how the validator unifies names: case-folded, spaces removed, and
        the word ``sequence`` stripped (xStudio's "Default" vs RV's
        "Default Sequence" both reduce to "default").
        """
        return str(name or "").replace(" ", "").lower().replace("sequence", "")

    def _session_timeline_guid_for_name(self, seq_name):
        """Return the guid of an already-known sequence timeline matching *seq_name*.

        When this peer joins a session that already established a sequence with
        this name (e.g. xStudio's "Default Sequence" playlist, received verbatim
        via STATE_SNAPSHOT), its local RVSequenceGroup must **adopt** that
        timeline's identity rather than register a second timeline under a
        different (deterministic) guid. Holding the same logical sequence under
        two guids is the root cause of the cross-app convergence flakiness: the
        deterministic guid scheme cannot unify the apps because xStudio assigns
        random guids and does not participate in it.

        Returns ``None`` when no peer timeline matches (two fresh peers each
        auto-creating the sequence — the deterministic guid is the fallback that
        lets *those* converge), or when the only match is already mapped to
        another local node.
        """
        mgr = self.plugin.sync_manager
        if not mgr:
            return None
        target = self._normalize_seq_name(seq_name)
        already_mapped = set(self._rv_node_to_timeline_guid.values())
        for guid, tl in mgr._timelines.items():
            if tl.metadata.get("clip_timeline_for"):
                continue  # single-clip view timeline, not a shared sequence
            if guid in already_mapped:
                continue
            if self._normalize_seq_name(getattr(tl, "name", None)) == target:
                return guid
        return None

    # ------------------------------------------------------------------
    # OTIO-origin detection (RV's otio_reader expands an imported .otio into
    # a `tracks` RVStackGroup → RVSequenceGroup graph; these helpers tell that
    # graph apart from native ad-hoc clip lists so the two are routed to
    # different sync models).
    # ------------------------------------------------------------------

    @staticmethod
    def _is_default_container(node):
        """True if *node* is one of RV's built-in default containers."""
        return node in _DEFAULT_CONTAINERS

    @staticmethod
    def _is_placeholder_movie(movie):
        """True if *movie* is the transient ``blank,otioFile=…`` import proxy.

        RV's otio_reader first loads an imported ``.otio`` as a blank movieproc
        placeholder, then expands it.  The placeholder (and any leftover ``blank,``
        proc) is never real media and must never become a clip/timeline.
        """
        return bool(movie) and movie.startswith("blank,")

    @staticmethod
    def _is_otio_stack(node):
        """True if *node* is an RVStackGroup produced by an OTIO import.

        Detected by the ``otio`` component RV's reader stamps on the stack —
        most reliably ``otio.timeline_name`` (written whenever the timeline has
        a name, so present even when the source ``.otio`` had empty metadata).
        ``defaultStack`` is excluded.
        """
        try:
            if rv.commands.nodeType(node) != "RVStackGroup" or node == "defaultStack":
                return False
            return any(
                rv.commands.propertyExists(f"{node}.{p}") for p in _OTIO_STACK_MARKERS
            )
        except Exception:
            return False

    def _otio_stack_groups(self):
        """Return all RVStackGroups that originated from an OTIO import."""
        try:
            return [n for n in rv.commands.nodesOfType("RVStackGroup") if self._is_otio_stack(n)]
        except Exception:
            return []

    def _node_otio_metadata(self, node):
        """Return the OTIO metadata dict RV stored on *node*, or ``{}``.

        Reads the JSON the reader serialized into ``<node>.otio.timeline_metadata``
        (stack/timeline level) or ``<node>.otio.metadata`` (track/clip level).
        Carries the ``sync`` block — including ``guid`` and ``origin`` — back out
        across a round-trip.
        """
        for prop in ("otio.timeline_metadata", "otio.metadata"):
            full = f"{node}.{prop}"
            try:
                if rv.commands.propertyExists(full):
                    val = rv.commands.getStringProperty(full)
                    if val and val[0]:
                        return json.loads(val[0])
            except Exception:
                pass
        return {}

    def _is_otio_origin_sequence(self, seq_group):
        """True if *seq_group* is a track inside an OTIO-imported Stack.

        Such sequences (e.g. the ``Video`` track of an imported timeline) are
        synced via the whole-OTIO snapshot model, not the native per-child path,
        so the native scans skip them.
        """
        for stack in self._otio_stack_groups():
            if seq_group in self._get_sequence_inputs(stack):
                return True
        return False

    def _is_syncable_native_sequence(self, seq_group):
        """True if *seq_group* should be synced through the native patch model.

        Excludes OTIO-origin track sequences (handled by the snapshot model) and
        RV's ``defaultSequence`` whenever an OTIO import is present (there it
        holds only the blank import placeholder; the real content lives in the
        Stack).  With no OTIO import, ``defaultSequence`` is the legitimate
        native working timeline and is kept.
        """
        if self._is_default_container(seq_group):
            return not self._otio_stack_groups()
        return not self._is_otio_origin_sequence(seq_group)

    def _otio_expansion_pending(self):
        """True if an OTIO import is mid-expansion and not yet ready to snapshot.

        RV loads an imported ``.otio`` asynchronously (a ``blank,otioFile=…``
        placeholder source appears first, then ``after_progressive_loading``
        expands it into the ``tracks`` Stack).  Expansion is pending while a
        placeholder source exists but no OTIO Stack with a populated track has
        materialized yet — callers must defer snapshotting until it clears.
        """
        try:
            sources = rv.commands.nodesOfType("RVFileSource")
        except Exception:
            return False
        has_placeholder = False
        for n in sources:
            try:
                movie = rv.commands.getStringProperty(f"{n}.media.movie")[0]
            except Exception:
                continue
            if self._is_placeholder_movie(movie):
                has_placeholder = True
                break
        if not has_placeholder:
            return False
        # Placeholder present — expansion is done only once an OTIO Stack exists
        # whose track sequence already has source inputs.
        for stack in self._otio_stack_groups():
            for seq in self._get_sequence_inputs(stack):
                if self._get_sequence_inputs(seq):
                    return False
        return True

    # ------------------------------------------------------------------
    # OTIO-origin snapshot sync (§4): export via RV's otio_writer, diff and
    # push whole-OTIO replacements on topology change, apply via otio_reader.
    # ------------------------------------------------------------------

    @staticmethod
    def _otio_rw_available():
        """True if RV's native OTIO reader and writer both imported."""
        return rv_otio_reader is not None and rv_otio_writer is not None

    def _stamp_sync_identity(self, tl, root_node):
        """Stamp deterministic sync GUIDs + origin marker onto an exported OTIO.

        A fresh import (from a ``.otio`` with empty metadata) carries no sync
        GUIDs, so we derive them from stable keys both peers compute identically:
        the timeline name, the track position, and — for clips — the normalized
        media path plus the clip's position and in-point (the same media appears
        at several cuts, so path alone is not unique). Media ``target_url``s are
        normalized to absolute paths so peers can resolve them without the
        import's relative-path context. Pre-existing GUIDs (from a prior
        round-trip) are preserved.
        """
        mgr = self.plugin.sync_manager
        derive = mgr._derive_guid
        name = tl.name or root_node
        sync = tl.metadata.setdefault("sync", {})
        sync.setdefault("guid", derive(f"rv_otio:{name}"))
        sync["origin"] = ORIGIN_OTIO_IMPORT
        # The RV writer sets global_start_time to the first clip's source in-point
        # (e.g., 100 for media with embedded timecode starting at frame 100).
        # xStudio respects this and counts its internal playhead from 100, making
        # xStudio's frame 0 correspond to sequence position 100 — a ~100-frame sync
        # offset.  For sequence frame sync the timeline always starts at position 0,
        # regardless of where in the source media the clips read from.
        tl.global_start_time = None
        for ti, track in enumerate(tl.tracks):
            track.metadata.setdefault("sync", {}).setdefault(
                "guid", derive(f"rv_otio_track:{name}:{ti}:{track.name}")
            )
            for ci, child in enumerate(track):
                if not isinstance(child, otio.schema.Clip):
                    continue
                ref = child.media_reference
                if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                    norm = _media_path(ref.target_url)
                    ref.target_url = norm
                    start = int(child.source_range.start_time.value) if child.source_range else 0
                    child.metadata.setdefault("sync", {}).setdefault(
                        "guid", derive(f"rv_clip:{norm}:{ti}:{ci}:{start}")
                    )
        # Give the timeline a logical Annotations track (matching native
        # timelines) so annotations have a home: annotation_track_guid_for_clip
        # looks for a track named "Annotations" in the clip's timeline.  This
        # track holds annotation clips only — it is stripped before
        # create_rv_node_from_otio (see apply_otio_snapshot) because it carries
        # no real media and must not be rebuilt as an RV node; annotations render
        # onto RV paint nodes instead.
        if not any("annotation" in (t.name or "").lower() for t in tl.tracks):
            ann_track = otio.schema.Track("Annotations")
            ann_track.metadata["sync"] = {"guid": derive(f"rv_otio_ann:{name}")}
            tl.tracks.append(ann_track)

    def _export_otio_stack(self, stack_node):
        """Export an OTIO-origin RVStackGroup to a guid-stamped OTIO Timeline."""
        if not rv_otio_writer:
            return None
        try:
            tl = rv_otio_writer.create_timeline_from_node(stack_node)
        except Exception as e:
            # sourceMediaInfo may not be ready yet if called during initial load;
            # caller retries on the next dirty tick — log without stack trace.
            _log(f"_export_otio_stack: create_timeline_from_node({stack_node}) not ready: {e}")
            return None
        if not tl:
            return None
        self._stamp_sync_identity(tl, stack_node)
        return tl

    @staticmethod
    def _serialize_otio(tl):
        """Serialize an OTIO timeline to its canonical wire string for diffing."""
        try:
            return otio.adapters.write_to_string(tl, "otio_json", indent=-1)
        except Exception:
            return None

    def init_otio_timelines(self):
        """Register (and broadcast) every OTIO-imported Stack as a snapshot timeline.

        Called once expansion has settled (§2 wait). Each imported Stack becomes
        one OTIO-origin timeline synced via the whole-OTIO model; its node graph
        is left intact (RV already built it correctly) — we only mirror it into
        the sync manager.
        """
        if not self._otio_rw_available():
            _log("init_otio_timelines: RV otio reader/writer unavailable — skipping")
            return
        mgr = self.plugin.sync_manager
        for stack in self._otio_stack_groups():
            tl = self._export_otio_stack(stack)
            if not tl:
                continue
            guid = tl.metadata["sync"]["guid"]
            if guid in mgr._timelines:
                continue
            mgr.register_timeline(tl)
            self._otio_stack_to_guid[stack] = guid
            self._otio_guid_to_root[guid] = stack
            self._otio_last_export[guid] = self._serialize_otio(tl)
            mgr.broadcast_add_timeline(guid)
            _log(f"init_otio_timelines: registered '{tl.name}' {guid[:8]} from {stack}")

    def check_otio_snapshots(self):
        """Diff each OTIO Stack against its last export; push on topology change.

        Only the master runs this. Peers must never broadcast OTIO changes — they
        only receive and apply. Without this gate, a peer that imported an OTIO
        file would echo ADD_TIMELINE back to the master, which would re-broadcast
        it, causing duplicate Stack/Sequence nodes from repeated
        ``create_rv_node_from_otio`` calls.

        Gated on the ``_otio_dirty`` flag (set by STACKS/SEQUENCES graph events)
        so the whole-timeline serialization does not run every poll tick. A clip
        insert/remove/large re-edit changes the serialized structure and triggers
        a ``REPLACE_TIMELINE`` push; a brand-new Stack triggers ``ADD_TIMELINE``.
        """
        mgr = self.plugin.sync_manager
        if not mgr or mgr.status != STATE_SYNCED or not self._otio_rw_available():
            return
        if not mgr.is_master:
            self._otio_dirty = False  # clear flag but never broadcast as peer
            return
        if not self._otio_dirty:
            return
        self._otio_dirty = False
        for stack in self._otio_stack_groups():
            tl = self._export_otio_stack(stack)
            if not tl:
                self._otio_dirty = True  # media not ready yet — retry next tick
                continue
            guid = tl.metadata["sync"]["guid"]
            wire = self._serialize_otio(tl)
            if guid not in mgr._timelines:
                mgr.register_timeline(tl)
                self._otio_stack_to_guid[stack] = guid
                self._otio_guid_to_root[guid] = stack
                self._otio_last_export[guid] = wire
                mgr.broadcast_add_timeline(guid)
                _log(f"check_otio_snapshots: new OTIO timeline {guid[:8]} broadcast")
            elif self._otio_last_export.get(guid) != wire:
                self._otio_last_export[guid] = wire
                # Remap object_map to the new structure (preserves persisting
                # clip guids), then push the wholesale replacement to peers.
                mgr._replace_timeline_local(guid, tl)
                mgr.broadcast_replace_timeline(guid)
                _log(f"check_otio_snapshots: topology change → replace push {guid[:8]}")

    def apply_otio_snapshot(self, tl):
        """Build/replace RV nodes for a received OTIO-origin timeline.

        Uses RV's native ``create_rv_node_from_otio`` so the Stack→Sequence→EDL
        graph (and any CDL/retime/annotation the writer captured) is reconstructed
        with full fidelity. On replace, the previously-applied root is torn down
        first. Runs under ``addSourceBegin/End`` like RV's own importer.
        """
        if not self._otio_rw_available():
            _log("apply_otio_snapshot: RV otio_reader unavailable — cannot apply")
            return
        guid = tl.metadata.get("sync", {}).get("guid")
        old_root = self._otio_guid_to_root.get(guid)
        if old_root:
            try:
                # Also delete the sequence nodes that were inputs to the stack.
                # deleteNode on the stack alone leaves RVSequenceGroup children
                # orphaned in the node graph; they accumulate as Video, Video000002,
                # … on each REPLACE_TIMELINE apply.  Source groups are NOT deleted
                # here — they may be shared and are managed by addSource/deleteNode
                # elsewhere.
                try:
                    inputs, _ = rv.commands.nodeConnections(old_root)
                    for child in (inputs or []):
                        if rv.commands.nodeType(child) == "RVSequenceGroup":
                            rv.commands.deleteNode(child)
                except Exception:
                    pass
                rv.commands.deleteNode(old_root)
                self._otio_stack_to_guid.pop(old_root, None)
            except Exception as e:
                _log(f"apply_otio_snapshot: could not delete old root {old_root}: {e}")
        # Strip the logical Annotations track before the reader: it carries only
        # annotation clips (no media), so create_rv_node_from_otio would try to
        # build source nodes for them.  The full timeline (with the track) stays
        # registered in the manager for annotation_track_guid_for_clip; here we
        # only rebuild the media tracks into RV nodes.
        reader_tl = tl
        try:
            if any("annotation" in (t.name or "").lower() for t in tl.tracks):
                reader_tl = copy.deepcopy(tl)
                keep = [t for t in reader_tl.tracks if "annotation" not in (t.name or "").lower()]
                reader_tl.tracks[:] = keep
        except Exception as e:
            _log(f"apply_otio_snapshot: could not strip annotation track: {e}")
            reader_tl = tl
        try:
            rv.commands.addSourceBegin()
            try:
                root = rv_otio_reader.create_rv_node_from_otio(reader_tl, {"otio_file": None})
            finally:
                rv.commands.addSourceEnd()
        except Exception as e:
            _log_exc(f"apply_otio_snapshot: create_rv_node_from_otio failed: {e}")
            return
        if not root:
            _log("apply_otio_snapshot: reader returned no root node")
            return
        self._otio_guid_to_root[guid] = root
        self._otio_stack_to_guid[root] = guid
        # Mark this as the active timeline so state reporters (get_openrv_state)
        # report the OTIO name consistently across master and peer.
        self.plugin.sync_manager.active_timeline_guid = guid
        # Replay any annotations that were already bound to clips in this
        # timeline. Suppress the echo guard first so the replayed strokes
        # aren't re-broadcast as new local events (same pattern used by
        # _rebuild_rv_session for native timelines).
        self._replay_otio_annotations(tl)
        # Establish the diff baseline from this peer's own re-export of the new
        # nodes, so the graph change we just caused does not echo a push back.
        exported = self._export_otio_stack(root)
        self._otio_last_export[guid] = (
            self._serialize_otio(exported) if exported else self._serialize_otio(tl)
        )
        try:
            rv.commands.setViewNode(root)
            rv.commands.redraw()
        except Exception:
            pass
        _log(f"apply_otio_snapshot: built nodes for {guid[:8]} root={root}")

    def _replay_otio_annotations(self, tl):
        """Re-apply any persisted annotations for clips in *tl* onto new RV nodes.

        Called from :meth:`apply_otio_snapshot` after ``create_rv_node_from_otio``
        builds a fresh node graph.  The paint nodes are empty at that point; this
        replays annotation commands from every clip timeline whose media clip
        belongs to *tl*, so annotations survive a ``REPLACE_TIMELINE`` apply.

        Echo suppression (:attr:`~AnnotationSyncController._ignore_annotations_until`)
        is set before replaying so the strokes are not re-broadcast as new local
        events.
        """
        mgr = self.plugin.sync_manager
        try:
            self.plugin.annotation._ignore_annotations_until = (
                __import__("time").time() + 1.5
            )
            for track in tl.tracks:
                for clip in track:
                    if not isinstance(clip, otio.schema.Clip):
                        continue
                    clip_guid = clip.metadata.get("sync", {}).get("guid")
                    if not clip_guid:
                        continue
                    ann_tl_guid = mgr._clip_timelines.get(clip_guid)
                    if not ann_tl_guid:
                        continue
                    ann_tl = mgr._timelines.get(ann_tl_guid)
                    if not ann_tl:
                        continue
                    for ann_track in ann_tl.tracks:
                        for ann_clip in ann_track:
                            if (isinstance(ann_clip, otio.schema.Clip)
                                    and "annotation_commands" in ann_clip.metadata):
                                try:
                                    self.plugin.annotation._apply_annotation_render(ann_clip)
                                except Exception as e:
                                    _log(f"_replay_otio_annotations: render failed: {e}")
        except Exception as e:
            _log(f"_replay_otio_annotations: {e}")

    def _init_timelines_from_sequences(self, seq_groups, fps):
        """Create one OTIO timeline per RVSequenceGroup and register each."""
        try:
            current_view = rv.commands.viewNode()
        except Exception:
            current_view = None

        source_groups_set = set(rv.commands.nodesOfType("RVSourceGroup"))

        for seq_group in seq_groups:
            # OTIO-origin track sequences are synced via the snapshot model and
            # RV's default containers are not shared timelines — skip both here.
            if not self._is_syncable_native_sequence(seq_group):
                _log(f"Skipping non-native sequence '{seq_group}' (OTIO-origin or default)")
                continue
            try:
                seq_name = rv.commands.getStringProperty(f"{seq_group}.ui.name")[0]
            except Exception:
                seq_name = seq_group

            # Adopt a peer's existing same-name sequence rather than registering a
            # parallel one (see _session_timeline_guid_for_name). Covers the case
            # where a peer is already present when RV starts up.
            adopted = self._session_timeline_guid_for_name(seq_name)
            if adopted:
                self._rv_node_to_timeline_guid[seq_group] = adopted
                self._sequence_input_order[seq_group] = self._get_sequence_inputs(seq_group)
                # Track the adopted timeline's media track so reorder/delete
                # detection maps RV node changes onto the shared track guid.
                _adopted_tl = self.plugin.sync_manager._timelines.get(adopted)
                _media_trk = next(
                    (t for t in (_adopted_tl.tracks if _adopted_tl else []) if _is_media_track(t)),
                    None,
                )
                _media_trk_guid = (
                    _media_trk.metadata.get("sync", {}).get("guid") if _media_trk else None
                )
                if self._active_media_track_guid is None and _media_trk_guid:
                    self._active_media_track_guid = _media_trk_guid
                    self._track = _media_trk
                if seq_group == current_view:
                    self.plugin.sync_manager.active_timeline_guid = adopted
                    if _media_trk_guid:
                        self._active_media_track_guid = _media_trk_guid
                        self._track = _media_trk
                _log(f"Adopted peer timeline {adopted[:8]} for RVSequenceGroup '{seq_name}'")
                continue

            timeline = otio.schema.Timeline(seq_name)
            timeline.tracks = otio.schema.Stack("tracks")

            media_track = otio.schema.Track("Media")
            timeline.tracks.append(media_track)
            annotations_track = otio.schema.Track("Annotations")
            timeline.tracks.append(annotations_track)
            _log(f"init tracks for {seq_group}: {[t.name for t in timeline.tracks]}")

            edl_counts = self._edl_frame_counts(seq_group)
            _log(f"EDL frame counts for {seq_group}: {edl_counts}")
            seq_inputs = [sg for sg in self._get_sequence_inputs(seq_group) if sg in source_groups_set]
            _log(f"Sequence inputs in edit order for {seq_group}: {seq_inputs}")
            for idx, sg in enumerate(seq_inputs):
                num_frames = edl_counts[idx] if idx < len(edl_counts) else None
                try:
                    for n in rv.commands.nodesInGroup(sg):
                        if rv.commands.nodeType(n) == "RVFileSource":
                            clip = self._make_clip(n, fps, num_frames)
                            if clip:
                                media_track.append(clip)
                                _log(f"Imported '{clip.name}' ({num_frames}f) into '{seq_name}'")
                except Exception as e:
                    _log(f"Skipping source group {sg}: {e}")

            # Deterministic guid from the sequence name so two peers that each
            # auto-create the same sequence (RV's defaultSequence after add_media)
            # converge on one identity instead of random per-instance guids.
            # ensure_guid_and_map (in register_timeline) preserves a pre-set guid.
            timeline.metadata.setdefault("sync", {})["guid"] = (
                self.plugin.sync_manager._derive_guid(f"rv_sequence:{seq_name}")
            )
            # Route marker: these are native ad-hoc clip lists; OTIO-imported
            # timelines are built from the Stack and marked otio_import (§4).
            timeline.metadata["sync"]["origin"] = ORIGIN_NATIVE
            self.plugin.sync_manager.register_timeline(timeline)
            # Read UUIDs back after registration (assigned by _ensure_guid_and_map)
            track_guid = media_track.metadata["sync"]["guid"]
            tl_guid = timeline.metadata["sync"]["guid"]
            self._rv_node_to_timeline_guid[seq_group] = tl_guid
            self._sequence_input_order[seq_group] = self._get_sequence_inputs(seq_group)

            if self._active_media_track_guid is None:
                self._active_media_track_guid = track_guid
                self._track = media_track

            if seq_group == current_view:
                self.plugin.sync_manager.active_timeline_guid = tl_guid
                self._active_media_track_guid = track_guid
                self._track = media_track

    def _init_single_timeline(self, fps):
        """Fallback: one timeline containing all open RVFileSource nodes."""
        timeline = otio.schema.Timeline("Sync Demo Timeline")
        timeline.tracks = otio.schema.Stack("tracks")

        media_track = otio.schema.Track("Media")
        timeline.tracks.append(media_track)
        annotations_track = otio.schema.Track("Annotations")
        timeline.tracks.append(annotations_track)

        for source_node in rv.commands.nodesOfType("RVFileSource"):
            clip = self._make_clip(source_node, fps)
            if clip:
                media_track.append(clip)
                _log(f"Auto-imported existing source: {clip.name}")

        # Deterministic guid (see _init_timelines_from_sequences) so peers agree.
        timeline.metadata.setdefault("sync", {})["guid"] = (
            self.plugin.sync_manager._derive_guid(f"rv_sequence:{timeline.name}")
        )
        timeline.metadata["sync"]["origin"] = ORIGIN_NATIVE
        self.plugin.sync_manager.register_timeline(timeline)
        self._active_media_track_guid = media_track.metadata["sync"]["guid"]
        self._track = media_track
        try:
            tl_guid = timeline.metadata["sync"]["guid"]
            view = rv.commands.viewNode()
            self._rv_node_to_timeline_guid[view] = tl_guid
            self._sequence_input_order[view] = self._get_sequence_inputs(view)
        except Exception:
            pass

    def _retry_init_timelines(self):
        """Re-scan source groups after the RV node graph has had time to settle.

        This is the native fast-load retry (ad-hoc clip lists whose media had
        not finished loading at init). It is a no-op for OTIO-imported sessions:
        their timelines are owned by the snapshot model, and the destructive
        ``reset_timelines`` below must not clobber them.
        """
        if self._otio_stack_groups():
            _log("Retry init skipped — OTIO import present (snapshot model owns it)")
            return
        try:
            fps = rv.commands.fps()
        except Exception:
            fps = 24.0

        seq_groups = rv.commands.nodesOfType("RVSequenceGroup")
        if not seq_groups:
            return

        seq_sources = self._source_groups_for_sequences(seq_groups)
        total = sum(len(v) for v in seq_sources.values())
        _log(f"Retry source counts: { {k: len(v) for k, v in seq_sources.items()} }")
        if total == 0:
            return  # still not ready — don't overwrite with empty data

        # Re-register timelines with the now-populated source groups
        self.plugin.sync_manager.reset_timelines()
        self._rv_node_to_timeline_guid.clear()
        self._sequence_input_order.clear()
        self._sg_to_path_cache.clear()
        self._active_media_track_guid = None
        self._track = None

        self._init_timelines_from_sequences(seq_groups, fps)
        self.plugin.annotation._import_existing_rv_annotations()
        _log("Retry init complete")

    def _make_otio_clip_for_sg(self, sg):
        """Create an OTIO Clip for a source group node, or None on failure."""
        try:
            fps = rv.commands.fps()
            for n in rv.commands.nodesInGroup(sg):
                if rv.commands.nodeType(n) == "RVFileSource":
                    return self._make_clip(n, fps)
        except Exception as e:
            _log(f"_make_otio_clip_for_sg failed for {sg}: {e}")
        return None

    def _make_clip(self, file_source_node, fps, num_frames=None):
        """Return an otio.schema.Clip for an RVFileSource node, or None on failure."""
        try:
            path = rv.commands.getStringProperty(f"{file_source_node}.media.movie")[0]
            if not path:
                return None
            # Never turn the transient OTIO import placeholder into a clip.
            if self._is_placeholder_movie(path):
                _log(f"_make_clip: skipping OTIO import placeholder {file_source_node}")
                return None
            if not path.startswith(("http://", "https://", "file://")) and not os.path.isabs(path):
                path = os.path.abspath(path)
            # Prefer the fps stored in the media itself over the session fps;
            # rv.commands.fps() can return 24 at init time before media is read.
            # Not all sources expose this property (e.g. image sequences), so
            # guard with propertyExists rather than a bare try/except -- RV logs
            # an ERROR to the console for any exception thrown across the
            # command boundary, caught or not.
            fps_prop = f"{file_source_node}.media.fps"
            if rv.commands.propertyExists(fps_prop):
                media_fps = rv.commands.getFloatProperty(fps_prop)[0]
                if media_fps and media_fps > 0:
                    fps = media_fps
            # The media's available_range must reflect its embedded timecode,
            # not a hardcoded frame 0. sourceMediaInfo reports startFrame/endFrame
            # derived from the QuickTime's timecode track (a movie whose timecode
            # starts at 01:00:00:00 @24fps reports startFrame=86400). Hardcoding 0
            # made peers (xStudio) place the clip at frame 0 and mis-size the
            # timeline. This mirrors RV's own otio_writer (_create_media_reference).
            start_frame = 0
            try:
                info = rv.commands.sourceMediaInfo(file_source_node)
                src_start = info.get("startFrame")
                src_end = info.get("endFrame")
                if src_start is not None:
                    start_frame = int(src_start)
                # Prefer the true media extent for the available_range duration;
                # fall back to the EDL frame count only when endFrame is unknown.
                if src_start is not None and src_end is not None:
                    num_frames = int(src_end) - int(src_start) + 1
            except Exception:
                pass
            # RV reports startFrame=1 as its own internal convention for media
            # with NO real embedded timecode (see playback_sync.py::_frame_base:
            # "a normal no-timecode source/sequence starts at frame 1"). Genuine
            # embedded timecode essentially never lands on exactly frame 1 (it's
            # values like 86400 for 01:00:00:00@24fps). Embedding RV's synthetic
            # "1" literally into available_range made xStudio treat frame 1 as
            # the start and skip the true first frame — confirmed live as an
            # off-by-one on non-timecode media. Normalise to the OTIO-conventional
            # 0 for untimed media; leave any other (real timecode) value as-is.
            if start_frame == 1:
                start_frame = 0
            if num_frames is None:
                num_frames = int(fps)  # 1-second fallback
            duration = otio.opentime.RationalTime(num_frames, fps)
            time_range = otio.opentime.TimeRange(
                otio.opentime.RationalTime(start_frame, fps), duration
            )
            clip = otio.schema.Clip(
                name=os.path.basename(path),
                media_reference=otio.schema.ExternalReference(target_url=path, available_range=time_range)
            )
            # Give the clip a cross-peer-stable guid so two peers that each
            # (re)build a sequence for the same media converge on one clip
            # identity instead of minting random per-instance guids. Prefer an
            # already-synced guid for this media; otherwise derive deterministically
            # from the media path (at build time the media isn't in the object map
            # yet, so reuse alone races — the derivation makes both peers agree).
            # ensure_guid_and_map preserves a pre-set guid.
            try:
                _mp = _media_path(path)
                _guid = (self.plugin.playback._clip_guid_for_media_path(_mp)
                         or self.plugin.sync_manager._derive_guid(f"rv_clip:{_mp}"))
                clip.metadata["sync"] = {"guid": _guid}
            except Exception:
                pass
            return clip
        except Exception as e:
            _log(f"_make_clip failed for {file_source_node}: {e}")
            return None

    def _edl_frame_counts(self, seq_group):
        """Return an ordered list of frame counts (one per source) read from the
        sequence EDL, or an empty list if the EDL isn't readable.

        The EDL lives on the inner RVSequence node, not the RVSequenceGroup.
        """
        try:
            # Find the RVSequence node inside the group
            seq_node = None
            for n in rv.commands.nodesInGroup(seq_group):
                if rv.commands.nodeType(n) == "RVSequence":
                    seq_node = n
                    break
            if seq_node is None:
                _log(f"No RVSequence found in {seq_group}")
                return []
            frames = rv.commands.getIntProperty(f"{seq_node}.edl.frame")
            if not frames:
                _log(f"edl.frame empty for {seq_node}")
                return []
            # Total sequence length from the global frame range of this view.
            try:
                fr = rv.commands.frameRange()
                total = fr[1] - fr[0] + 1
            except Exception:
                total = None
            counts = []
            for i, start_f in enumerate(frames):
                if i + 1 < len(frames):
                    counts.append(frames[i + 1] - start_f)
                elif total is not None:
                    counts.append(total - start_f + 1)
                else:
                    counts.append(None)  # unknown last clip
            _log(f"EDL frame counts for {seq_group} (via {seq_node}): {counts}")
            return counts
        except Exception as e:
            _log(f"_edl_frame_counts failed for {seq_group}: {e}")
            return []

    def _source_groups_for_sequences(self, seq_groups):
        """Return {seq_group: [RVSourceGroup, ...]} by querying connections from the source side.

        Calls nodeConnections on each RVSourceGroup and checks what it connects to,
        avoiding the ambiguity of input/output ordering when querying the sequence directly.
        """
        seq_set = set(seq_groups)
        mapping = {sg: [] for sg in seq_groups}
        for source_group in rv.commands.nodesOfType("RVSourceGroup"):
            try:
                connected = rv.commands.nodeConnections(source_group)
                # Flatten one level — handles both flat list and [[a],[b]] formats
                if connected and isinstance(connected[0], (list, tuple)):
                    flat = [n for sub in connected for n in sub]
                else:
                    flat = list(connected)
                for cn in flat:
                    if cn in seq_set:
                        mapping[cn].append(source_group)
            except Exception as e:
                _log(f"nodeConnections({source_group}): {e}")
        return mapping

    def _get_sequence_inputs(self, seq_group):
        """Return the ordered list of source group inputs for a sequence group."""
        try:
            connections = rv.commands.nodeConnections(seq_group)
            if connections and len(connections) >= 1:
                inputs = connections[0]
                if isinstance(inputs, (list, tuple)):
                    return list(inputs)
        except Exception:
            pass
        return []

    def _path_to_source_group_map(self):
        """Return {path: source_group_node_name} for all currently loaded RVSourceGroups."""
        mapping = {}
        for sg in rv.commands.nodesOfType("RVSourceGroup"):
            try:
                for n in rv.commands.nodesInGroup(sg):
                    if rv.commands.nodeType(n) == "RVFileSource":
                        path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                        if path and not self._is_placeholder_movie(path):
                            mapping[_media_path(path)] = sg
            except Exception:
                pass
        return mapping

    def _check_sequence_reorders(self):
        """Detect clip deletions and reorders in any tracked sequence and broadcast patches."""
        if not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            return
        path_to_sg = self._path_to_source_group_map()
        for path, sg in path_to_sg.items():
            self._sg_to_path_cache[sg] = path
        sg_to_path = {v: k for k, v in path_to_sg.items()}
        source_groups_set = set(rv.commands.nodesOfType("RVSourceGroup"))
        for seq_group, tl_guid in list(self._rv_node_to_timeline_guid.items()):
            # Use nodeConnections order — this is what changes when the user drags clips
            # (nodesOfType order is stable and does not reflect drag reorders).
            current = [
                sg for sg in self._get_sequence_inputs(seq_group)
                if sg in source_groups_set
            ]
            stored = self._sequence_input_order.get(seq_group)
            if stored is None or current == stored:
                continue

            # After a rebuild or programmatic setNodeInputs the RVSequenceGroup's
            # connection order can take a few seconds to settle to the true EDL order.
            # Suppress broadcasts during this window, but do NOT update the baseline
            # order — that way any user-initiated reorders that happen during settle
            # are still detected and broadcast once the window expires.
            settle_until = self._sequence_settle_until.get(seq_group, 0)
            if settle_until:
                if time.time() < settle_until:
                    continue
                # Settle window just expired: clear the flag and fall through to
                # normal detection so any changes accumulated during settle are
                # broadcast now (e.g. user reordered before a second peer connected).
                self._sequence_settle_until[seq_group] = 0

            _log(f"Sequence changed in {seq_group}: {stored} -> {current}")

            # If any newly-added source groups aren't in sg_to_path yet their
            # media hasn't finished loading (media.movie not set).  Defer the
            # stored-order update so the change re-fires on the next tick once
            # the path is readable, rather than silently dropping the broadcast.
            stored_set = set(stored or [])
            new_sgs = [sg for sg in current if sg not in stored_set]
            unresolved = [sg for sg in new_sgs if sg not in sg_to_path]
            if unresolved:
                _log(f"Sequence {seq_group}: {len(unresolved)} source(s) still loading, deferring")
                continue

            self._sequence_input_order[seq_group] = current

            timeline = self.plugin.sync_manager._timelines.get(tl_guid)
            if not timeline:
                _log(f"Sequence {seq_group}: no timeline for tl_guid={tl_guid[:8] if tl_guid else None} (timelines={list(self.plugin.sync_manager._timelines.keys())[:3]})")
                continue
            all_tracks = list(timeline.tracks)
            track_info = [(t.name, repr(t.kind), type(t.kind).__name__, _is_media_track(t)) for t in all_tracks]
            _log(f"Sequence {seq_group}: tracks+check = {track_info}")
            media_track = next((t for t in all_tracks if _is_media_track(t)), None)
            if media_track is None:
                _log(f"Sequence {seq_group}: no media track in timeline")
                continue
            track_guid = media_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                _log(f"Sequence {seq_group}: media track has no sync guid")
                continue

            def _build_path_to_guid():
                result = {}
                for clip in media_track:
                    if not isinstance(clip, otio.schema.Clip):
                        continue
                    ref = clip.media_reference
                    if hasattr(ref, "target_url") and ref.target_url:
                        result[_media_path(ref.target_url)] = clip.metadata.get("sync", {}).get("guid")
                return result

            # --- Deletions: source groups present in stored but gone from current ---
            current_set = set(current)
            for sg in stored:
                if sg not in current_set:
                    path = self._sg_to_path_cache.get(sg)
                    if not path:
                        continue
                    child_guid = _build_path_to_guid().get(path)
                    if not child_guid:
                        _log(f"Delete: no guid for removed sg={sg}")
                        continue
                    _log(f"Delete: broadcasting remove_child sg={sg} child={child_guid}")
                    self.plugin.sync_manager.broadcast_remove_child(track_guid, child_guid)

            # --- Additions: source groups whose path count exceeds the OTIO track count ---
            # Uses a Counter so that adding a duplicate of an existing clip is detected.
            otio_path_counts = Counter(
                _media_path(clip.media_reference.target_url)
                for clip in media_track
                if isinstance(clip, otio.schema.Clip)
                and hasattr(clip.media_reference, "target_url")
                and clip.media_reference.target_url
            )
            seen_counts = Counter()
            valid_sgs_before = 0  # count of path-resolved source groups before current position
            for sg in current:
                path = sg_to_path.get(sg)
                if not path:
                    # Non-source-group nodes (e.g. RVSequenceGroup like 'defaultSequence')
                    # must be skipped and must NOT consume an OTIO index — the OTIO track
                    # only contains real media clips, so using enumerate() would give an
                    # inflated index that exceeds the track length and raises in C++.
                    continue
                seen_counts[path] += 1
                if seen_counts[path] > otio_path_counts[path]:
                    clip = self._make_otio_clip_for_sg(sg)
                    if clip:
                        _log(f"Add: broadcasting insert_child sg={sg} at index={valid_sgs_before} track={track_guid[:8]}")
                        self.plugin.sync_manager.insert_child(track_guid, clip, valid_sgs_before)
                        _log(f"Add: insert_child done (object_map has track: {track_guid in self.plugin.sync_manager.patcher.object_map})")
                    else:
                        _log(f"Add: FAILED to make otio clip for sg={sg}")
                        otio_path_counts[path] += 1
                valid_sgs_before += 1  # only increment for resolved source groups

            # --- Reorders: among clips still present, detect position changes ---
            ptcg = _build_path_to_guid()  # rebuild after any additions above
            new_clip_guids = [
                ptcg[sg_to_path[sg]]
                for sg in current
                if sg in sg_to_path and sg_to_path[sg] in ptcg
            ]
            # Simulate current OTIO order: stored clips still in current_set, in old order
            current_order = [
                ptcg.get(sg_to_path.get(sg))
                for sg in stored
                if sg in current_set and sg in sg_to_path and sg_to_path[sg] in ptcg
            ]
            for target_idx, child_guid in enumerate(new_clip_guids):
                if not child_guid:
                    continue
                try:
                    cur_idx = current_order.index(child_guid)
                except ValueError:
                    continue
                if cur_idx != target_idx:
                    _log(f"Reorder: broadcast_move_child child={child_guid} to={target_idx}")
                    self.plugin.sync_manager.broadcast_move_child(track_guid, child_guid, target_idx)
                    current_order.pop(cur_idx)
                    current_order.insert(target_idx, child_guid)

    def _poll_new_sequences(self):
        """Detect newly created RVSequenceGroups and broadcast them as new timelines."""
        if not self.plugin.sync_manager:
            return
        if self.plugin.sync_manager.status != STATE_SYNCED:
            return
        try:
            seq_groups = rv.commands.nodesOfType("RVSequenceGroup")
            fps = rv.commands.fps() or 24.0
        except Exception:
            return
        for seq_group in seq_groups:
            if seq_group in self._rv_node_to_timeline_guid:
                continue
            # OTIO-origin track sequences and RV default containers are not
            # native timelines — the snapshot model owns the former.
            if not self._is_syncable_native_sequence(seq_group):
                continue
            # New sequence group not yet tracked — register and broadcast it.
            try:
                seq_name = rv.commands.getStringProperty(f"{seq_group}.ui.name")[0]
            except Exception:
                seq_name = seq_group
            # If a peer already established a sequence with this name (e.g.
            # xStudio's playlist received via STATE_SNAPSHOT), adopt its guid
            # instead of registering a duplicate timeline. This is what makes the
            # two apps converge on one identity; registering a parallel
            # deterministic-guid timeline here is what caused the intermittent
            # "missing timeline" consensus failures.
            adopted = self._session_timeline_guid_for_name(seq_name)
            if adopted:
                self._rv_node_to_timeline_guid[seq_group] = adopted
                self._sequence_input_order[seq_group] = self._get_sequence_inputs(seq_group)
                _log(f"Adopted peer timeline {adopted[:8]} for RVSequenceGroup '{seq_name}'")
                continue
            timeline = otio.schema.Timeline(seq_name)
            timeline.tracks = otio.schema.Stack("tracks")
            media_track = otio.schema.Track("Media")
            timeline.tracks.append(media_track)
            ann_track = otio.schema.Track("Annotations")
            timeline.tracks.append(ann_track)
            seq_sources = self._source_groups_for_sequences([seq_group])
            edl_counts = self._edl_frame_counts(seq_group)
            for idx, sg in enumerate(seq_sources.get(seq_group, [])):
                num_frames = edl_counts[idx] if idx < len(edl_counts) else None
                try:
                    for n in rv.commands.nodesInGroup(sg):
                        if rv.commands.nodeType(n) == "RVFileSource":
                            clip = self._make_clip(n, fps, num_frames)
                            if clip:
                                media_track.append(clip)
                except Exception as e:
                    _log(f"_poll_new_sequences: error reading {sg}: {e}")
            # Derive a deterministic GUID from the sequence name so two peers
            # that each auto-create the same sequence (e.g. RV's defaultSequence
            # after add_media) independently arrive at the *same* GUID instead of
            # random ones — otherwise they hold the same media under different
            # timeline identities and never converge. ensure_guid_and_map (called
            # by register_timeline) preserves a pre-set guid.
            timeline.metadata.setdefault("sync", {})["guid"] = (
                self.plugin.sync_manager._derive_guid(f"rv_sequence:{seq_name}")
            )
            timeline.metadata["sync"]["origin"] = ORIGIN_NATIVE
            self.plugin.sync_manager.register_timeline(timeline)
            tl_guid = timeline.metadata["sync"]["guid"]
            self._rv_node_to_timeline_guid[seq_group] = tl_guid
            self._sequence_input_order[seq_group] = self._get_sequence_inputs(seq_group)
            self.plugin.sync_manager.broadcast_add_timeline(tl_guid)
            _log(f"New RVSequenceGroup '{seq_name}' → timeline {tl_guid[:8]} broadcast")

    def _poll_sequence_renames(self):
        """Detect and broadcast RVSequenceGroup name changes."""
        if not self.plugin.sync_manager:
            return
        if self.plugin.sync_manager.status != STATE_SYNCED:
            return
        for seq_group, tl_guid in list(self._rv_node_to_timeline_guid.items()):
            tl = self.plugin.sync_manager._timelines.get(tl_guid)
            if tl is None:
                continue
            try:
                current_name = rv.commands.getStringProperty(f"{seq_group}.ui.name")[0]
            except Exception:
                continue
            if current_name and current_name != (tl.name or ""):
                _log(f"Sequence rename: '{tl.name}' → '{current_name}' (node={seq_group})")
                self.plugin.sync_manager.broadcast_timeline_rename(tl_guid, current_name)

    def _poll_deleted_sequences(self):
        """Detect RVSequenceGroups the user deleted and broadcast their removal.

        Counterpart to :meth:`_poll_new_sequences`: when a previously-tracked
        RVSequenceGroup is gone from the node graph, broadcast a
        ``REMOVE_TIMELINE`` so peers drop the timeline and tear down their
        containers. RV moves the on-screen view off a deleted sequence on its
        own, satisfying the host ordering contract (switch on-screen source,
        then remove).
        """
        if not self.plugin.sync_manager:
            return
        if self.plugin.sync_manager.status != STATE_SYNCED:
            return
        try:
            seq_groups = set(rv.commands.nodesOfType("RVSequenceGroup"))
        except Exception:
            return
        for seq_group, tl_guid in list(self._rv_node_to_timeline_guid.items()):
            if seq_group in seq_groups:
                continue
            # Node is gone — the user deleted this sequence.
            del self._rv_node_to_timeline_guid[seq_group]
            self._sequence_input_order.pop(seq_group, None)
            # Another node may still represent the same timeline (e.g. adopted
            # identity); only remove the timeline when nothing maps to it.
            if tl_guid in self._rv_node_to_timeline_guid.values():
                continue
            self.plugin.sync_manager.broadcast_remove_timeline(tl_guid)
            _log(
                f"Deleted RVSequenceGroup '{seq_group}' → timeline "
                f"{tl_guid[:8]} removal broadcast"
            )

    def _set_sequence_ui_name(self, seq_node, name):
        """Set an RVSequenceGroup's ``ui.name`` to *name*.

        Used right after :func:`newNode`, whose name argument is sanitised into
        the node name (spaces truncated); without this the ui.name defaults to
        the truncation and :meth:`_poll_sequence_renames` broadcasts a spurious
        rename. Best-effort: a failure here must not abort sequence creation.
        """
        if not name:
            return
        try:
            rv.commands.setStringProperty(f"{seq_node}.ui.name", [name], True)
        except Exception as e:
            _log(f"_set_sequence_ui_name: could not set ui.name for {seq_node}: {e}")

    def _create_rv_sequence_for_timeline(self, tl):
        """Create an RVSequenceGroup for a remotely-received OTIO timeline."""
        tl_guid = tl.metadata.get("sync", {}).get("guid") if tl else None
        if not tl_guid:
            _log("_create_rv_sequence_for_timeline: no GUID on timeline")
            return
        if tl_guid in self._rv_node_to_timeline_guid.values():
            # Already have a local node for this guid (e.g. the default sequence
            # that couldn't be deleted on REMOVE_TIMELINE).  Don't create a
            # duplicate; the existing mapping is still valid.
            _log(f"_create_rv_sequence_for_timeline: {tl_guid[:8]} already mapped — skipping")
            return

        # Collect ordered media paths from the timeline's video tracks.
        # For flat-playlist timelines, also cache clip guid → path so
        # _switch_to_source_view can resolve bin clip guids after the playlist
        # is replaced by a real sequence (which uses different per-clip guids).
        all_paths = []
        for track in tl.tracks:
            if not _is_media_track(track):
                continue
            for child in track:
                if not isinstance(child, otio.schema.Clip):
                    continue
                ref = child.media_reference
                if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                    norm = _media_path(ref.target_url)
                    all_paths.append(norm)
                    guid = child.metadata.get("sync", {}).get("guid")
                    if guid and norm and tl.metadata.get("xs_flat_playlist"):
                        self._bin_guid_to_path[guid] = norm

        if not all_paths:
            _log(f"_create_rv_sequence_for_timeline: no media in '{tl.name}'")

        # Load any sources not yet present in the RV session.
        already = set(self._path_to_source_group_map())
        for path in all_paths:
            if path not in already:
                rv.commands.addSource(path)
                _log(f"  addSource: {path}")

        # Rescan after addSource calls.
        path_to_sg = self._path_to_source_group_map()
        seq_sources = [path_to_sg[p] for p in all_paths if p in path_to_sg]
        # Note: an empty seq_sources is valid here — a peer can create an empty
        # sequence (e.g. xStudio's "Sequence 1" with no clips yet). We still
        # create the RVSequenceGroup so the sequence appears in RV; clips arrive
        # later via INSERT_CHILD (_apply_insert_child). Returning early here was
        # the cause of empty peer sequences never showing up in RV.

        try:
            seq_node = rv.commands.newNode("RVSequenceGroup", tl.name)
            # newNode sanitises the node name (truncates at spaces, e.g.
            # "Default Sequence" -> node "Default"), so the ui.name defaults to
            # that truncation. Set ui.name to the full timeline name explicitly,
            # otherwise _poll_sequence_renames sees ui.name != tl.name and
            # broadcasts a spurious RENAME_TIMELINE that corrupts the name on
            # every peer.
            self._set_sequence_ui_name(seq_node, tl.name)
            rv.commands.setNodeInputs(seq_node, seq_sources)
            self._rv_node_to_timeline_guid[seq_node] = tl_guid
            self._sequence_input_order[seq_node] = list(seq_sources)
            _log(
                f"RECV add_timeline: created RVSequenceGroup '{tl.name}' "
                f"({len(seq_sources)} sources) for {tl_guid[:8]}"
            )
            rv.commands.redraw()
        except Exception as e:
            _log_exc(f"_create_rv_sequence_for_timeline: failed for '{tl.name}': {e}")

    def _delete_rv_sequence_for_timeline(self, tl):
        """Delete the RVSequenceGroup for a remotely-removed OTIO timeline.

        Symmetric to :meth:`_create_rv_sequence_for_timeline`. No-op when no
        local container maps to the timeline's GUID.
        """
        tl_guid = tl.metadata.get("sync", {}).get("guid") if tl else None
        if not tl_guid:
            _log("_delete_rv_sequence_for_timeline: no GUID on timeline")
            return
        targets = [
            sg for sg, g in self._rv_node_to_timeline_guid.items() if g == tl_guid
        ]
        if not targets:
            _log(f"RECV remove_timeline: no RVSequenceGroup for {tl_guid[:8]} (no-op)")
            return
        for seq_group in targets:
            try:
                rv.commands.deleteNode(seq_group)
                _log(
                    f"RECV remove_timeline: deleted RVSequenceGroup "
                    f"'{seq_group}' for {tl_guid[:8]}"
                )
                # Only unmap after a successful delete.  If deleteNode fails
                # (e.g. "can't delete default views"), keep the mapping so
                # _poll_new_sequences doesn't re-broadcast the node as a new
                # timeline on the next tick.
                self._rv_node_to_timeline_guid.pop(seq_group, None)
                self._sequence_input_order.pop(seq_group, None)
            except Exception as e:
                _log_exc(
                    f"_delete_rv_sequence_for_timeline: failed to delete "
                    f"'{seq_group}': {e} — keeping mapping to suppress re-broadcast"
                )
        try:
            rv.commands.redraw()
        except Exception:
            pass

    def _apply_insert_child(self, clip_obj):
        """Connect a newly-received source group to the right sequence group."""
        if not isinstance(clip_obj, otio.schema.Clip):
            return
        clip_guid = clip_obj.metadata.get("sync", {}).get("guid")
        for seq_group, tl_guid in self._rv_node_to_timeline_guid.items():
            timeline = self.plugin.sync_manager._timelines.get(tl_guid)
            if not timeline:
                continue
            for track in timeline.tracks:
                if not _is_media_track(track):
                    continue
                if not any(c.metadata.get("sync", {}).get("guid") == clip_guid for c in track):
                    continue
                path_to_sg = self._path_to_source_group_map()
                new_inputs = []
                for c in track:
                    if not isinstance(c, otio.schema.Clip):
                        continue
                    ref = c.media_reference
                    if hasattr(ref, "target_url") and ref.target_url:
                        sg = path_to_sg.get(_media_path(ref.target_url))
                        if sg:
                            new_inputs.append(sg)
                if new_inputs:
                    rv.commands.setNodeInputs(seq_group, new_inputs)
                    self._sequence_input_order[seq_group] = (
                        self._get_sequence_inputs(seq_group) or new_inputs
                    )
                    _log(f"RECV insert_child: {seq_group} now has {len(new_inputs)} inputs")
                    rv.commands.redraw()
                return

    def _apply_remove_child(self, data):
        """Apply a REMOVE_CHILD patch to the RV session after OTIO has already been updated."""
        parent_uuid = data.get("parent_uuid")
        for seq_group, tl_guid in self._rv_node_to_timeline_guid.items():
            timeline = self.plugin.sync_manager._timelines.get(tl_guid)
            if not timeline:
                continue
            for track in timeline.tracks:
                if not _is_media_track(track):
                    continue
                if track.metadata.get("sync", {}).get("guid") != parent_uuid:
                    continue
                path_to_sg = self._path_to_source_group_map()
                new_inputs = []
                for clip in track:
                    if not isinstance(clip, otio.schema.Clip):
                        continue
                    ref = clip.media_reference
                    if hasattr(ref, "target_url") and ref.target_url:
                        sg = path_to_sg.get(_media_path(ref.target_url))
                        if sg:
                            new_inputs.append(sg)
                rv.commands.setNodeInputs(seq_group, new_inputs)
                self._sequence_input_order[seq_group] = (
                    self._get_sequence_inputs(seq_group) or new_inputs
                )
                _log(f"RECV remove_child: {seq_group} now has {len(new_inputs)} inputs")
                rv.commands.redraw()
                return

    def _apply_move_child(self, data):
        """Apply a MOVE_CHILD patch to the RV session after OTIO has already been updated."""
        parent_uuid = data.get("parent_uuid")
        for seq_group, tl_guid in self._rv_node_to_timeline_guid.items():
            timeline = self.plugin.sync_manager._timelines.get(tl_guid)
            if not timeline:
                continue
            for track in timeline.tracks:
                if _is_media_track(track) and track.metadata.get("sync", {}).get("guid") == parent_uuid:
                    path_to_sg = self._path_to_source_group_map()
                    new_inputs = []
                    for clip in track:
                        if not isinstance(clip, otio.schema.Clip):
                            continue
                        ref = clip.media_reference
                        if hasattr(ref, "target_url") and ref.target_url:
                            sg = path_to_sg.get(_media_path(ref.target_url))
                            if sg:
                                new_inputs.append(sg)
                    if new_inputs:
                        rv.commands.setNodeInputs(seq_group, new_inputs)
                        # Store the nodesOfType order (what _check_sequence_reorders reads)
                        # not the connection order, so programmatic moves don't look like
                        # user drags on the next poll tick.
                        self._sequence_input_order[seq_group] = (
                            self._get_sequence_inputs(seq_group) or new_inputs
                        )
                        _log(f"RECV move_child: reordered {seq_group} → {new_inputs}")
                    rv.commands.redraw()
                    return
        _log(f"RECV move_child: no media track found for parent_uuid={parent_uuid}")

    def _load_sources_from_timelines(self, timelines):
        """Call addSource for every unique clip path across *timelines*."""
        already_loaded = {p for p in self._path_to_source_group_map()}
        seen = set()
        for tl in timelines:
            for item in tl.tracks:
                if not _is_media_track(item):
                    continue
                for child in item:
                    if not isinstance(child, otio.schema.Clip):
                        continue
                    ref = child.media_reference
                    if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
                        continue
                    norm = _media_path(ref.target_url)
                    guid = child.metadata.get("sync", {}).get("guid")
                    if guid and norm:
                        self._bin_guid_to_path[guid] = norm
                    if norm not in seen and norm not in already_loaded:
                        seen.add(norm)
                        rv.commands.addSource(norm)
                        _log(f"Loading source: {norm}")

    def rebuild_rv_session(self):
        self._rebuild_rv_session()

    def _rebuild_rv_session(self):
        """Clear and rebuild the RV session based on the current OTIO timelines."""
        _log("Rebuilding RV session from OTIO snapshot...")
        all_tls = list(self.plugin.sync_manager._timelines.values())
        flat_timelines = [tl for tl in all_tls if tl.metadata.get("xs_flat_playlist")]
        timelines = [tl for tl in all_tls if not tl.metadata.get("xs_flat_playlist")]

        if not timelines and not flat_timelines:
            return

        # OTIO-origin timelines are rebuilt with full fidelity via RV's native
        # reader (Stack/EDL/CDL), not the flat builder below. Apply them here and
        # drop them from the flat path.
        otio_origin = [
            tl for tl in timelines
            if tl.metadata.get("sync", {}).get("origin") == ORIGIN_OTIO_IMPORT
        ]
        for tl in otio_origin:
            self.apply_otio_snapshot(tl)
        timelines = [tl for tl in timelines if tl not in otio_origin]

        if not timelines:
            # Snapshot contains only xs_flat_playlist timelines (xStudio bin).
            # Load all clip sources so that source-mode view switching works, then
            # map the existing RV default sequence to the flat playlist's guid so
            # _poll_new_sequences doesn't broadcast it as a new empty timeline.
            self._load_sources_from_timelines(flat_timelines)
            path_to_sg = self._path_to_source_group_map()
            _log(f"Flat-playlist rebuild: {len(path_to_sg)} source(s) loaded")
            if flat_timelines:
                flat_tl = flat_timelines[0]
                flat_guid = flat_tl.metadata.get("sync", {}).get("guid")
                if flat_guid:
                    seq_groups = []
                    try:
                        seq_groups = rv.commands.nodesOfType("RVSequenceGroup")
                    except Exception:
                        pass
                    for sg in seq_groups:
                        if sg not in self._rv_node_to_timeline_guid:
                            self._rv_node_to_timeline_guid[sg] = flat_guid
                            self._sequence_input_order[sg] = self._get_sequence_inputs(sg)
                            # Align ui.name with the xStudio timeline name so
                            # _poll_sequence_renames doesn't see a mismatch and
                            # broadcast a spurious rename to peers.
                            self._set_sequence_ui_name(sg, flat_tl.name)
                            _log(f"Flat-playlist rebuild: mapped {sg} → {flat_guid[:8]} name='{flat_tl.name}'")
                            break
            return

        # Pass 1: load every unique path once.
        # addSource in RV may be deferred, so we scan for source groups in a
        # separate pass after all loads are done.
        already_loaded = {p for p in self._path_to_source_group_map()}
        all_paths_ordered = []   # preserves per-timeline clip order
        seen = set()
        for timeline in timelines + flat_timelines:
            for item in timeline.tracks:
                if not _is_media_track(item):
                    continue
                for child in item:
                    if not isinstance(child, otio.schema.Clip):
                        continue
                    ref = child.media_reference
                    if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
                        continue
                    norm = _media_path(ref.target_url)
                    if norm not in seen:
                        all_paths_ordered.append(norm)
                        seen.add(norm)

        for path in all_paths_ordered:
            if path not in already_loaded:
                rv.commands.addSource(path)
                _log(f"Loading source: {path}")

        # Pass 2: rescan now that all addSource calls have been issued.
        path_to_sg = self._path_to_source_group_map()
        _log(f"Source map: {len(path_to_sg)} entries")

        # Pass 3: create one RVSequenceGroup per OTIO timeline when there are
        # multiple, so the client mirrors the host's sequence structure.
        if len(timelines) > 1:
            for timeline in timelines:
                timeline_sgs = []
                for item in timeline.tracks:
                    if not _is_media_track(item):
                        continue
                    for child in item:
                        if not isinstance(child, otio.schema.Clip):
                            continue
                        ref = child.media_reference
                        if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                            sg = path_to_sg.get(_media_path(ref.target_url))
                            if sg:
                                timeline_sgs.append(sg)
                if timeline_sgs:
                    try:
                        seq_node = rv.commands.newNode("RVSequenceGroup", timeline.name)
                        # See _create_rv_sequence_for_timeline: set ui.name to the
                        # full timeline name so the rename poller does not echo a
                        # spurious RENAME from newNode's space-truncated node name.
                        self._set_sequence_ui_name(seq_node, timeline.name)
                        rv.commands.setNodeInputs(seq_node, list(timeline_sgs))
                        tl_guid = timeline.metadata.get("sync", {}).get("guid")
                        if tl_guid:
                            self._rv_node_to_timeline_guid[seq_node] = tl_guid
                        self._sequence_input_order[seq_node] = list(timeline_sgs)
                        _log(f"Created sequence '{timeline.name}' with {len(timeline_sgs)} sources")
                    except Exception as e:
                        _log(f"Could not create sequence '{timeline.name}': {e}")
        elif len(timelines) == 1:
            timeline = timelines[0]
            tl_guid = timeline.metadata.get("sync", {}).get("guid")
            if tl_guid:
                view = None
                try:
                    view = rv.commands.viewNode()
                except Exception:
                    pass
                if not view or rv.commands.nodeType(view) != "RVSequenceGroup":
                    seq_groups = rv.commands.nodesOfType("RVSequenceGroup")
                    if seq_groups:
                        view = seq_groups[0]
                if view:
                    self._rv_node_to_timeline_guid[view] = tl_guid
                    # Align the reused sequence's ui.name with the OTIO timeline
                    # name so the rename poller does not echo a spurious rename
                    # (RV's default view node is named e.g. "defaultSequence").
                    self._set_sequence_ui_name(view, timeline.name)
                    # Explicitly order the existing sequence to match OTIO, just
                    # as the multi-timeline path does for newly created sequences.
                    media_track = next(
                        (t for t in timeline.tracks if _is_media_track(t)), None
                    )
                    if media_track is not None:
                        timeline_sgs = []
                        for child in media_track:
                            if not isinstance(child, otio.schema.Clip):
                                continue
                            ref = child.media_reference
                            if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                                sg = path_to_sg.get(_media_path(ref.target_url))
                                if sg:
                                    timeline_sgs.append(sg)
                        if timeline_sgs:
                            rv.commands.setNodeInputs(view, timeline_sgs)
                            self._sequence_input_order[view] = self._get_sequence_inputs(view)
                            self._sequence_settle_until[view] = time.time() + 5.0
                            _log(f"Rebuild: mapped and ordered existing view '{view}' to timeline {tl_guid[:8]} ({len(timeline_sgs)} sources)")
                        else:
                            self._sequence_input_order[view] = self._get_sequence_inputs(view)
                            self._sequence_settle_until[view] = time.time() + 5.0
                            _log(f"Rebuild: mapped existing view '{view}' to timeline {tl_guid[:8]} (no sources to order)")
                    else:
                        self._sequence_input_order[view] = self._get_sequence_inputs(view)
                        _log(f"Rebuild: mapped existing view '{view}' to timeline {tl_guid[:8]}")

        # Pass 4: replay annotations.
        for timeline in timelines:
            tl_guid = timeline.metadata.get("sync", {}).get("guid")
            if tl_guid:
                for seq_node, node_tl_guid in self._rv_node_to_timeline_guid.items():
                    if node_tl_guid == tl_guid:
                        try:
                            if rv.commands.viewNode() != seq_node:
                                _log(f"Rebuild view change to '{seq_node}' to replay annotations for timeline '{timeline.name}'")
                                rv.commands.setViewNode(seq_node)
                        except Exception as e:
                            _log(f"Failed to set view to '{seq_node}' for replaying annotations: {e}")
                        break

            for item in timeline.tracks:
                is_annotation_track = (item.name and item.name.startswith("Annotations")) or any(
                    isinstance(c, otio.schema.Clip) and "annotation_commands" in c.metadata
                    for c in item
                )
                if is_annotation_track:
                    for child in item:
                        if isinstance(child, otio.schema.Clip):
                            if "annotation_commands" not in child.metadata:
                                continue
                            # Resolve media_path and frame from OTIO references.
                            # clip_guid → ExternalReference.target_url avoids
                            # storing RV-specific paths in the annotation clip.
                            # source_range.start_time is 0-indexed clip-local time;
                            # RV paint frames are 1-indexed.
                            clip_guid = child.metadata.get("clip_guid")
                            media_path = None
                            if clip_guid:
                                media_obj = self.plugin.sync_manager._object_map.get(clip_guid)
                                if isinstance(media_obj, otio.schema.Clip):
                                    ref = media_obj.media_reference
                                    if isinstance(ref, otio.schema.ExternalReference):
                                        media_path = _media_path(ref.target_url)
                            frame = (
                                int(child.source_range.start_time.value) + 1
                                if child.source_range else 1
                            )
                            node_name = child.metadata.get("annotated_clip_name", clip_guid or "unknown")

                            event_groups = {}
                            for event in child.metadata["annotation_commands"]:
                                if isinstance(event, (dict, collections.abc.Mapping)):
                                    try:
                                        event = otio.adapters.read_from_string(otio.adapters.write_to_string(event, "otio_json"), "otio_json")
                                    except Exception:
                                        pass
                                try:
                                    if isinstance(event, otio.schemadef.SyncEvent.TextAnnotation):
                                        if not (event.text or "").strip():
                                            continue
                                        rv_size = font_size_to_rv(event.font_size) if getattr(event, "font_size", None) else 0.01
                                        uuid_val = event.uuid or ""
                                        # Guard against duplicates when INSERT_CHILD already painted
                                        # this node before the snapshot arrived.
                                        paint_node, paint_native_frame = (
                                            self.plugin.annotation._find_paint_node_for_media(media_path, frame)
                                            if media_path else (None, frame)
                                        )
                                        if paint_node and self.plugin.annotation._text_uuid_exists_in_rv(paint_node, paint_native_frame, uuid_val):
                                            _log(f"  _rebuild_rv_session: skip dup text uuid={uuid_val[:8]!r}")
                                            continue
                                        text_data = {
                                            "frame": frame,
                                            "node_name": node_name,
                                            "media_path": media_path,
                                            "position": list(event.position) if getattr(event, "position", None) else [0.0, 0.0],
                                            "color": list(event.rgba) if getattr(event, "rgba", None) else [1.0, 1.0, 1.0, 1.0],
                                            "spacing": float(event.spacing) if getattr(event, "spacing", None) is not None else 0.8,
                                            "size": rv_size,
                                            "scale": float(event.scale) if getattr(event, "scale", None) is not None else 1.0,
                                            "rotation": float(event.rotation) if getattr(event, "rotation", None) is not None else 0.0,
                                            "font": event.font or "",
                                            "text": event.text or "",
                                            "uuid": uuid_val,
                                        }
                                        self.plugin.annotation._apply_text_annotation(text_data)
                                    elif isinstance(event, otio.schemadef.SyncEvent.EllipseAnnotation):
                                        self.plugin.annotation._apply_shape_annotation({
                                            "frame": frame,
                                            "node_name": node_name,
                                            "media_path": media_path,
                                            "type": "ellipse",
                                            "min": list(event.min),
                                            "max": list(event.max),
                                            "rgba": list(event.rgba),
                                            "size": event.size,
                                            "inner_rgba": list(event.inner_rgba),
                                            "uuid": event.uuid,
                                        })
                                    elif isinstance(event, otio.schemadef.SyncEvent.RectangleAnnotation):
                                        self.plugin.annotation._apply_shape_annotation({
                                            "frame": frame,
                                            "node_name": node_name,
                                            "media_path": media_path,
                                            "type": "rect",
                                            "min": list(event.min),
                                            "max": list(event.max),
                                            "rgba": list(event.rgba),
                                            "size": event.size,
                                            "inner_rgba": list(event.inner_rgba),
                                            "uuid": event.uuid,
                                        })
                                    elif isinstance(event, otio.schemadef.SyncEvent.ArrowAnnotation):
                                        self.plugin.annotation._apply_shape_annotation({
                                            "frame": frame,
                                            "node_name": node_name,
                                            "media_path": media_path,
                                            "type": "arrow",
                                            "start": list(event.start),
                                            "end": list(event.end),
                                            "rgba": list(event.rgba),
                                            "size": event.size,
                                            "uuid": event.uuid,
                                        })
                                    elif hasattr(event, "uuid"):
                                        if event.uuid not in event_groups:
                                            event_groups[event.uuid] = {"start": None, "points": None}
                                        if isinstance(event, otio.schemadef.SyncEvent.PaintStart):
                                            event_groups[event.uuid]["start"] = event
                                        elif isinstance(event, otio.schemadef.SyncEvent.PaintPoints):
                                            event_groups[event.uuid]["points"] = event
                                except Exception as e:
                                    _log(f"  _rebuild_rv_session: failed to replay {type(event).__name__} event "
                                         f"for clip '{node_name}': {e}")
                                    continue

                            for uuid, grp in event_groups.items():
                                start_event = grp["start"]
                                points_event = grp["points"]
                                if not start_event or not points_event:
                                    continue
                                try:
                                    data = {
                                        "frame": frame,
                                        "node_name": node_name,
                                        "media_path": media_path,
                                        "color": list(start_event.rgba),
                                        "brush": start_event.brush,
                                        "width": list(points_event.points.size),
                                        "points": [val for pair in zip(points_event.points.x, points_event.points.y) for val in pair],
                                        "join": 3,
                                        "cap": 1,
                                        "mode": 1 if getattr(start_event, "type", "color") == "erase" else 0,
                                    }
                                    self.plugin.annotation._apply_annotation(data)
                                except Exception as e:
                                    _log(f"  _rebuild_rv_session: failed to replay pen/erase event "
                                         f"uuid={uuid[:8]!r} for clip '{node_name}': {e}")
                                    continue

        # Set active media track so do_add_clip works on clients too
        active_tl = self.plugin.sync_manager._timelines.get(self.plugin.sync_manager.active_timeline_guid)
        if active_tl:
            for track in active_tl.tracks:
                if _is_media_track(track):
                    self._active_media_track_guid = track.metadata.get("sync", {}).get("guid")
                    self._track = track
                    break

        # Restore view to the active timeline
        active_tl_guid = self.plugin.sync_manager.active_timeline_guid
        if active_tl_guid:
            for rv_node, tl_guid in self._rv_node_to_timeline_guid.items():
                if tl_guid == active_tl_guid:
                    try:
                        if rv.commands.viewNode() != rv_node:
                            _log(f"Rebuild restoring active view to '{rv_node}' for timeline GUID '{active_tl_guid[:8]}'")
                            self.plugin._rv_updating = True
                            try:
                                rv.commands.setViewNode(rv_node)
                            finally:
                                self.plugin._rv_updating = False
                    except Exception as e:
                        _log(f"Failed to restore view to '{rv_node}': {e}")
                    break

        rv.commands.redraw()
