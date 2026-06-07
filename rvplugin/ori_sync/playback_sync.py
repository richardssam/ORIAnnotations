import time
import rv.commands

try:
    import opentimelineio as otio
except ImportError:
    otio = None

try:
    from otio_sync_core.manager import STATE_SYNCED
except ImportError:
    STATE_SYNCED = "synced"

from utils import _log, _media_path


class PlaybackSyncController:
    def __init__(self, plugin):
        self.plugin = plugin
        self._last_broadcast_frame = -1
        self._last_selection = []
        self._last_broadcast_clip_guid = None
        self._sequence_selection_applied_at = 0.0

    def _broadcast_playback(self):
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            return
        fps = rv.commands.fps()
        current_frame = rv.commands.frame()
        playing = rv.commands.isPlaying()
        try:
            looping = rv.commands.playMode() == 0
        except AttributeError:
            looping = True

        view = rv.commands.viewNode()
        timeline_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(view) or self.plugin.sync_manager.active_timeline_guid
        _log(f"SEND playback playing={playing} frame={current_frame} fps={fps} view={view} tl={timeline_guid}")
        state = {
            "playing": playing,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(current_frame - 1),
                "rate": float(fps),
            },
            "looping": looping,
            "timeline_guid": timeline_guid,
        }
        self.plugin.sync_manager.broadcast_playback(state)
        self._last_broadcast_frame = current_frame

    def _apply_playback(self, data):
        playing = data.get("playing", False)
        current_time = data.get("current_time", {})
        target_frame = int(current_time.get("value", 0)) + 1  # protocol is 0-indexed; RV is 1-based
        timeline_guid = data.get("timeline_guid")
        _log(f"RECV playback playing={playing} frame={target_frame} tl={timeline_guid}")

        # Determine whether this timeline_guid corresponds to a real RV node.
        # Virtual clip timelines (created by get_or_create_clip_timeline on the
        # sender side) have no RV node — they carry clip-local frame numbers
        # that must not overwrite a sequence-level frame set by _apply_selection.
        known_tl_guids = set(self.plugin.sequence._rv_node_to_timeline_guid.values())
        tl_is_real_node = (not timeline_guid or timeline_guid in known_tl_guids)

        if timeline_guid:
            current_view = rv.commands.viewNode()
            # Only switch timeline view when the current node is already a known
            # timeline/sequence node that maps to a *different* timeline.  If the
            # user has double-clicked into a source group (source view), do not
            # pull them back to the sequence — that would undo a SELECTION apply.
            current_is_source_group = (
                rv.commands.nodeType(current_view) == "RVSourceGroup"
            )
            if not current_is_source_group and tl_is_real_node:
                for rv_node, tl_guid in self.plugin.sequence._rv_node_to_timeline_guid.items():
                    if tl_guid == timeline_guid and current_view != rv_node:
                        _log(f"RECV view_change to {rv_node}")
                        rv.commands.setViewNode(rv_node)
                        break

        # Don't override a sequence-selection frame that was just applied — the
        # sender broadcasts source-local frame=0 immediately after selecting a clip
        # in sequence mode, which would reset RV to frame 1 instead of the clip's
        # sequence-global start frame.
        seq_sel_age = time.monotonic() - self._sequence_selection_applied_at
        if tl_is_real_node and rv.commands.frame() != target_frame and seq_sel_age > 0.5:
            rv.commands.setFrame(target_frame)
        elif not tl_is_real_node and rv.commands.frame() != target_frame:
            rv.commands.setFrame(target_frame)
        is_playing = rv.commands.isPlaying()
        if playing and not is_playing:
            rv.commands.play()
        elif not playing and is_playing:
            rv.commands.stop()

    def _apply_selection(self, data):
        clip_guid = data.get("clip_guid", "")

        view_mode = data.get("view_mode", "source")
        if not clip_guid:
            # Clear: return to sequence/timeline view.
            _log(f"RECV selection: clear → sequence view (mode={view_mode})")
            self._last_broadcast_clip_guid = None
            seq_node = next(
                (n for n in self.plugin.sequence._rv_node_to_timeline_guid
                 if rv.commands.nodeType(n) != "RVSourceGroup"),
                None
            )
            if seq_node:
                seq_tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(seq_node)
                if seq_tl_guid:
                    self.plugin.sync_manager.active_timeline_guid = seq_tl_guid
                self.plugin._rv_updating = True
                try:
                    rv.commands.setViewNode(seq_node)
                finally:
                    self.plugin._rv_updating = False
            return

        # Find the media path for this GUID then look up the local source group.
        clip = self.plugin.sync_manager._object_map.get(clip_guid) if self.plugin.sync_manager else None
        if clip is None or not isinstance(clip, otio.schema.Clip):
            _log(f"RECV selection: clip_guid={clip_guid} not found in object_map")
            return
        ref = clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference):
            return
        media_path = _media_path(ref.target_url)
        source_group = self.plugin.sequence._path_to_source_group_map().get(media_path)
        if not source_group:
            _log(f"RECV selection: no source group for {media_path}")
            return
        _log(f"RECV selection: clip '{clip.name}' guid={clip_guid[:8]} mode={view_mode} → source_group={source_group}")

        # sequence mode: stay in the sequence view and seek to the clip's start frame.
        if view_mode == "sequence":
            # Walk all OTIO timelines to find which one contains this clip and at
            # what frame offset.  Track the timeline GUID so we can pick the
            # matching RVSequenceGroup instead of arbitrarily grabbing the first one.
            start_frame = 1
            end_frame = 1
            target_tl_guid = None
            for tl_guid_iter, tl in self.plugin.sync_manager.timelines.items():
                found = False
                for track in tl.tracks:
                    if track.kind != otio.schema.TrackKind.Video:
                        continue
                    elapsed = 0
                    for child in track:
                        if child.metadata.get("sync", {}).get("guid") == clip_guid:
                            start_frame = elapsed + 1  # RV frames are 1-indexed
                            try:
                                clip_len = int(child.trimmed_range().duration.value)
                            except Exception:
                                clip_len = 0
                            end_frame = start_frame + max(clip_len - 1, 0)
                            found = True
                            break
                        try:
                            elapsed += int(child.trimmed_range().duration.value)
                        except Exception:
                            pass
                    if found:
                        break
                if found:
                    target_tl_guid = tl_guid_iter
                    break

            # Resolve the RVSequenceGroup that owns this timeline.
            seq_node = None
            if target_tl_guid:
                for rv_node, tl_guid_map in self.plugin.sequence._rv_node_to_timeline_guid.items():
                    if (tl_guid_map == target_tl_guid
                            and rv.commands.nodeType(rv_node) != "RVSourceGroup"):
                        seq_node = rv_node
                        break
            if seq_node is None:
                # Fallback: first non-source-group node (single-sequence sessions).
                seq_node = next(
                    (n for n in self.plugin.sequence._rv_node_to_timeline_guid
                     if rv.commands.nodeType(n) != "RVSourceGroup"),
                    None
                )

            _log(
                f"RECV selection seq: seq_node={seq_node} start_frame={start_frame}"
                f" end_frame={end_frame}"
                f" target_tl={target_tl_guid[:8] if target_tl_guid else None}"
            )
            if seq_node:
                seq_tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(seq_node)
                if seq_tl_guid:
                    self.plugin.sync_manager.active_timeline_guid = seq_tl_guid
                self._last_broadcast_clip_guid = clip_guid
                self._sequence_selection_applied_at = time.monotonic()
                self.plugin._rv_updating = True
                try:
                    rv.commands.setViewNode(seq_node)
                    current_frame = rv.commands.frame()
                    if start_frame <= current_frame <= end_frame:
                        # Already within this clip's range — stay at the current
                        # frame rather than jumping back to the clip's start.
                        # This prevents selection broadcasts during annotation from
                        # resetting the playhead to frame 1.
                        _log(
                            f"RECV selection seq: applied setViewNode={seq_node}"
                            f" (staying at frame {current_frame},"
                            f" within clip range {start_frame}-{end_frame})"
                        )
                    else:
                        rv.commands.setFrame(start_frame)
                        _log(
                            f"RECV selection seq: applied setViewNode={seq_node}"
                            f" setFrame={start_frame}"
                        )
                except Exception as e:
                    _log(f"RECV selection seq: error applying setViewNode/setFrame: {e}")
                finally:
                    self.plugin._rv_updating = False
            else:
                _log("RECV selection seq: no seq_node found — cannot seek")
            return

        # source mode: switch active_timeline_guid to the clip's own timeline.
        clip_tl_guid = self.plugin.sync_manager.get_or_create_clip_timeline(clip_guid)
        if clip_tl_guid:
            self.plugin.sync_manager.active_timeline_guid = clip_tl_guid

        # Set echo guard before setViewNode so after-graph-view-change doesn't
        # re-broadcast the remote-applied selection.
        self._last_broadcast_clip_guid = clip_guid
        self.plugin._rv_updating = True
        try:
            rv.commands.setViewNode(source_group)
            rv.commands.setFrame(1)  # jump to first frame of this source
        finally:
            self.plugin._rv_updating = False

    def _clip_guid_for_media_path(self, media_path):
        """Return the OTIO GUID of the Clip whose ExternalReference matches media_path."""
        import os
        norm_media_path = os.path.abspath(_media_path(media_path))
        _log(f"LOOKUP CLIP: media_path='{media_path}' -> norm='{norm_media_path}'")
        for guid, obj in self.plugin.sync_manager._object_map.items():
            if isinstance(obj, otio.schema.Clip):
                ref = obj.media_reference
                if isinstance(ref, otio.schema.ExternalReference):
                    ref_norm = os.path.abspath(_media_path(ref.target_url))
                    _log(f"  Checking clip {guid}: target_url='{ref.target_url}' -> norm='{ref_norm}'")
                    if ref.target_url == media_path or ref_norm == norm_media_path:
                        _log(f"  -> MATCHED clip {guid}")
                        return guid
        _log("LOOKUP CLIP: NO MATCH FOUND")
        return None

    def on_selection_changed(self, event):
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        selection = rv.commands.selection()
        if selection == self._last_selection:
            event.reject()
            return
        self._last_selection = selection
        # Map each selected source group to an OTIO clip GUID and broadcast the
        # first one.  RV's "selection" can be a list of source-group nodes; the
        # other peers only care about which clip the user is looping over.
        sg_to_path = {v: k for k, v in self.plugin.sequence._path_to_source_group_map().items()}
        for node in selection:
            media_path = sg_to_path.get(node)
            if media_path:
                clip_guid = self._clip_guid_for_media_path(media_path)
                if clip_guid:
                    _clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
                    _clip_label = getattr(_clip_obj, "name", None) or clip_guid[:8]
                    _log(f"SEND selection [selection-change]: clip '{_clip_label}' guid={clip_guid[:8]} node={node}")
                    self.plugin.sync_manager.broadcast_selection(clip_guid)
                    break
        event.reject()

    def on_view_changed(self, event):
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        view = rv.commands.viewNode()
        # Timeline switch: view node is a sequence group.
        tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(view)
        if tl_guid and tl_guid != self.plugin.sync_manager.active_timeline_guid:
            self.plugin.sync_manager.active_timeline_guid = tl_guid
            _log(f"SEND view_change view={view} tl={tl_guid}")
            self._broadcast_playback()
        # Clip selection: user double-clicked into a source group (source view).
        # Map source group → media path → OTIO clip GUID and broadcast.
        if rv.commands.nodeType(view) == "RVSourceGroup":
            sg_to_path = {v: k for k, v in self.plugin.sequence._path_to_source_group_map().items()}
            media_path = sg_to_path.get(view)
            if media_path:
                clip_guid = self._clip_guid_for_media_path(media_path)
                if clip_guid and clip_guid != self._last_broadcast_clip_guid:
                    _clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
                    _clip_label = getattr(_clip_obj, "name", None) or clip_guid[:8]
                    _log(f"SEND selection [view-change]: clip '{_clip_label}' guid={clip_guid[:8]} view={view}")
                    is_new = clip_guid not in self.plugin.sync_manager._clip_timelines
                    clip_tl_guid = self.plugin.sync_manager.get_or_create_clip_timeline(clip_guid)
                    if clip_tl_guid:
                        if is_new:
                            self.plugin.sync_manager.broadcast_clip_timeline(clip_tl_guid)
                        self.plugin.sync_manager.active_timeline_guid = clip_tl_guid
                    self.plugin.sync_manager.broadcast_selection(clip_guid)
                    self._last_broadcast_clip_guid = clip_guid
        elif view in self.plugin.sequence._rv_node_to_timeline_guid and self._last_broadcast_clip_guid:
            # Returned to sequence/timeline view — restore sequence active_timeline_guid
            # and broadcast clear so peers exit single-clip mode.
            _tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(view)
            _tl = self.plugin.sync_manager.timelines.get(_tl_guid) if _tl_guid else None
            _tl_name = getattr(_tl, "name", None) or view
            _log(f"SEND selection [view-change]: clear → sequence '{_tl_name}' (view={view})")
            seq_tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(view)
            if seq_tl_guid:
                self.plugin.sync_manager.active_timeline_guid = seq_tl_guid
            self.plugin.sync_manager.broadcast_selection("", view_mode="sequence")
            self._last_broadcast_clip_guid = None
        event.reject()
