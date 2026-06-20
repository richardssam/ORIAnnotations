import os
import time
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

from utils import _log, _log_exc, _media_path, _is_media_track


class SequenceSyncController:
    def __init__(self, plugin):
        self.plugin = plugin
        self._rv_node_to_timeline_guid = {}
        self._sequence_input_order = {}
        self._sg_to_path_cache = {}
        self._sequence_settle_until = {}
        self._active_media_track_guid = None
        self._track = None

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

    def _init_timelines_from_sequences(self, seq_groups, fps):
        """Create one OTIO timeline per RVSequenceGroup and register each."""
        try:
            current_view = rv.commands.viewNode()
        except Exception:
            current_view = None

        source_groups_set = set(rv.commands.nodesOfType("RVSourceGroup"))

        for seq_group in seq_groups:
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
        """Re-scan source groups after the RV node graph has had time to settle."""
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
            if not path.startswith(("http://", "https://", "file://")) and not os.path.isabs(path):
                path = os.path.abspath(path)
            # Prefer the fps stored in the media itself over the session fps;
            # rv.commands.fps() can return 24 at init time before media is read.
            try:
                media_fps = rv.commands.getFloatProperty(f"{file_source_node}.media.fps")[0]
                if media_fps and media_fps > 0:
                    fps = media_fps
            except Exception:
                pass
            if num_frames is None:
                num_frames = int(fps)  # 1-second fallback
            duration = otio.opentime.RationalTime(num_frames, fps)
            time_range = otio.opentime.TimeRange(otio.opentime.RationalTime(0, fps), duration)
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
                        if path:
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
                if hasattr(clip.media_reference, "target_url") and clip.media_reference.target_url
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

        # Collect ordered media paths from the timeline's video tracks.
        all_paths = []
        for track in tl.tracks:
            if not _is_media_track(track):
                continue
            for child in track:
                if not isinstance(child, otio.schema.Clip):
                    continue
                ref = child.media_reference
                if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                    all_paths.append(_media_path(ref.target_url))

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
        if not seq_sources:
            _log(f"_create_rv_sequence_for_timeline: no source groups mapped for '{tl.name}'")
            return

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
            except Exception as e:
                _log_exc(
                    f"_delete_rv_sequence_for_timeline: failed to delete "
                    f"'{seq_group}': {e}"
                )
            self._rv_node_to_timeline_guid.pop(seq_group, None)
            self._sequence_input_order.pop(seq_group, None)
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

    def rebuild_rv_session(self):
        self._rebuild_rv_session()

    def _rebuild_rv_session(self):
        """Clear and rebuild the RV session based on the current OTIO timelines."""
        _log("Rebuilding RV session from OTIO snapshot...")
        timelines = [
            tl for tl in self.plugin.sync_manager._timelines.values()
            if not tl.metadata.get("xs_flat_playlist")
        ]
        if not timelines:
            return

        # Pass 1: load every unique path once.
        # addSource in RV may be deferred, so we scan for source groups in a
        # separate pass after all loads are done.
        already_loaded = {p for p in self._path_to_source_group_map()}
        all_paths_ordered = []   # preserves per-timeline clip order
        seen = set()
        for timeline in timelines:
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
                                if isinstance(event, otio.schemadef.SyncEvent.TextAnnotation):
                                    if not (event.text or "").strip():
                                        continue
                                    rv_size = float(event.font_size) / 5000.0 if getattr(event, "font_size", None) else 0.01
                                    uuid_val = event.uuid or ""
                                    # Guard against duplicates when INSERT_CHILD already painted
                                    # this node before the snapshot arrived.
                                    paint_node = self.plugin.annotation._find_paint_node_for_media(media_path, frame) if media_path else None
                                    if paint_node and self.plugin.annotation._text_uuid_exists_in_rv(paint_node, frame, uuid_val):
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
                                elif hasattr(event, "uuid"):
                                    if event.uuid not in event_groups:
                                        event_groups[event.uuid] = {"start": None, "points": None}
                                    if isinstance(event, otio.schemadef.SyncEvent.PaintStart):
                                        event_groups[event.uuid]["start"] = event
                                    elif isinstance(event, otio.schemadef.SyncEvent.PaintPoints):
                                        event_groups[event.uuid]["points"] = event

                            for uuid, grp in event_groups.items():
                                start_event = grp["start"]
                                points_event = grp["points"]
                                if not start_event or not points_event:
                                    continue
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
