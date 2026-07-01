import rv.commands

try:
    import opentimelineio as otio
except ImportError:
    otio = None

try:
    from otio_sync_core.manager import STATE_SYNCED
except ImportError:
    STATE_SYNCED = "synced"

from utils import _log, _log_exc, _media_path


class PlaybackSyncController:
    def __init__(self, plugin):
        self.plugin = plugin
        self._last_broadcast_frame = -1
        self._last_selection = []
        # Current local view (what we are broadcasting); mirrors the xStudio
        # plugin's _cur_view_mode/_cur_clip_guid so every PLAYBACK_SETTINGS
        # message — including pure position updates — carries the view too.
        self._cur_view_mode = "sequence"
        self._cur_clip_guid = None
        # Last loop state received from a peer — used to restart RV when the
        # clip ends naturally (play-once boundary) but the peer said to loop.
        self._cur_looping = True
        # Last view-state actually applied from a remote message, used to
        # detect real transitions (mode/clip/timeline change) vs. a clip
        # changing under continuous playhead motion in sequence mode, which
        # must NOT re-trigger a view switch (position is authoritative there).
        self._last_applied_view_mode = None
        self._last_applied_clip_guid = None
        self._last_applied_tl_guid = None

    def _frame_base(self):
        """Return the RV frame that corresponds to protocol position 0.

        The wire protocol carries a **0-based offset into the current view**
        (xStudio's playhead position).  RV's equivalent is ``frame - frameStart``,
        where ``frameStart`` is the first frame of the view's range:

        * a normal no-timecode source/sequence starts at frame 1 (the historic
          hard-coded ``- 1``);
        * an OTIO-imported sequence is built from an EDL whose ``frame`` array
          starts at 0;
        * a source view of timecode-bearing media (e.g. ``seq_D.mov`` spanning
          frames 100-119) starts at 100.

        Reading ``frameStart()`` makes the conversion correct in every case and
        is identical to the old ``- 1`` for the no-timecode media the native
        tests use.  Falls back to 1 if the range can't be read.
        """
        try:
            return int(rv.commands.frameStart())
        except Exception:
            return 1

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
        base = self._frame_base()
        _log(
            f"SEND playback playing={playing} frame={current_frame} base={base}"
            f" fps={fps} view={view} tl={timeline_guid}"
            f" mode={self._cur_view_mode} clip={(self._cur_clip_guid or '')[:8]}"
        )
        state = {
            "playing": playing,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(current_frame - base),
                "rate": float(fps),
            },
            "looping": looping,
            "timeline_guid": timeline_guid,
            # Carry the current view alongside every position update (not just
            # explicit view-state changes) so a peer receiving a pure scrub/play
            # update also keeps the right mode/clip — single broadcast path (D4).
            "view_mode": self._cur_view_mode,
            "clip_guid": self._cur_clip_guid,
        }
        self.plugin.sync_manager.broadcast_playback_state(state)
        self._last_broadcast_frame = current_frame

    def broadcast_view_state(self, clip_guid, view_mode):
        """Broadcast an explicit view-state change (clip and/or mode switch).

        This is the single source of a view-affecting broadcast: every local
        event that changes what we're viewing (RV's view-changed/selection-
        changed events) funnels through here, mirroring the xStudio plugin's
        ``broadcast_view_state``.  Position-only updates ride ``_cur_view_mode``/
        ``_cur_clip_guid`` via ``_broadcast_playback`` instead of duplicating
        this logic.
        """
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            return
        self._cur_view_mode = view_mode
        self._cur_clip_guid = clip_guid or None
        self._broadcast_playback()

    def _apply_playback(self, data):
        """Apply an incoming unified view-state message (SELECTION_1.0 retired).

        Applies, atomically:
        * the **view** — when ``view_mode``/``clip_guid``/``timeline_guid``
          actually transition, switch the RV view node; a clip-only change
          while the timeline is unchanged in sequence mode is NOT a transition
          (position is authoritative there — see D2), so it does not re-switch.
        * the **position** — ``current_time`` always wins once any view switch
          above has landed, so the frame is never raced against a separate
          selection-apply (one apply path — D4).
        """
        view_mode = data.get("view_mode")
        clip_guid = data.get("clip_guid")
        timeline_guid = data.get("timeline_guid")

        if view_mode is not None:
            mode_changed = view_mode != self._last_applied_view_mode
            clip_changed = clip_guid != self._last_applied_clip_guid
            tl_changed = timeline_guid != self._last_applied_tl_guid
            try:
                if view_mode == "sequence":
                    if mode_changed or tl_changed:
                        self._switch_to_sequence_view(timeline_guid)
                elif view_mode == "source":
                    if mode_changed or clip_changed:
                        self._switch_to_source_view(clip_guid)
            except Exception:
                _log_exc("apply view-state: view switch failed")
            self._last_applied_view_mode = view_mode
            self._last_applied_clip_guid = clip_guid
            self._last_applied_tl_guid = timeline_guid
            # Sync _cur_* immediately so on_rv_frame_changed / on_rv_play_start
            # callbacks that fire during the frame/play calls below broadcast the
            # right mode instead of echoing stale "sequence" state back to peers.
            self._cur_view_mode = view_mode
            self._cur_clip_guid = clip_guid or None

        playing = data.get("playing", False)
        looping = data.get("looping", True)
        self._cur_looping = looping
        current_time = data.get("current_time", {})

        # Resolve the frame base AFTER any view switch so frameStart() reflects
        # the view the frame actually targets (a timecode source starts at 100,
        # an OTIO sequence at 0, a normal view at 1).  Protocol value is a 0-based
        # offset into that view.
        base = self._frame_base()
        target_frame = int(current_time.get("value", 0)) + base
        _log(
            f"RECV playback playing={playing} looping={looping} frame={target_frame} base={base}"
            f" value={current_time.get('value')} tl={timeline_guid} mode={view_mode}"
        )

        # Suppress on_rv_frame_changed / on_rv_play_start during mechanical apply
        # so they don't echo back to peers — _broadcast_playback checks _rv_updating.
        self.plugin._rv_updating = True
        try:
            # Sync RV's loop mode so it doesn't fire spurious playing=False at the
            # clip boundary and cause the peer to stop — playMode 0 = loop, 1 = once.
            target_play_mode = 0 if looping else 1
            try:
                if rv.commands.playMode() != target_play_mode:
                    rv.commands.setPlayMode(target_play_mode)
                    _log(f"RECV playback: set playMode={target_play_mode} (looping={looping})")
            except Exception:
                _log_exc("RECV playback: setPlayMode failed")
            if rv.commands.frame() != target_frame:
                rv.commands.setFrame(target_frame)
            is_playing = rv.commands.isPlaying()
            if playing and not is_playing:
                rv.commands.play()
            elif not playing and is_playing:
                rv.commands.stop()
        finally:
            self.plugin._rv_updating = False

    def _switch_to_sequence_view(self, timeline_guid):
        """Switch the RV view to the RVSequenceGroup for *timeline_guid*.

        No seek happens here — in sequence mode position is authoritative and
        is applied right after this returns, by the caller (D2).
        """
        seq_node = None
        if timeline_guid:
            for rv_node, tl_guid_map in self.plugin.sequence._rv_node_to_timeline_guid.items():
                if (tl_guid_map == timeline_guid
                        and rv.commands.nodeType(rv_node) != "RVSourceGroup"):
                    seq_node = rv_node
                    break
            if seq_node is None:
                # OTIO-origin timelines are not in _rv_node_to_timeline_guid —
                # they are tracked in _otio_guid_to_root (Stack → Sequence).
                # Use the inner RVSequenceGroup so setViewNode/setFrame work.
                root = self.plugin.sequence._otio_guid_to_root.get(timeline_guid)
                if root and rv.commands.nodeType(root) == "RVStackGroup":
                    inputs = self.plugin.sequence._get_sequence_inputs(root)
                    seq_node = next(
                        (n for n in inputs
                         if rv.commands.nodeType(n) == "RVSequenceGroup"),
                        root,
                    )
        if seq_node is None:
            # Fallback: first non-source-group node (single-sequence sessions).
            seq_node = next(
                (n for n in self.plugin.sequence._rv_node_to_timeline_guid
                 if rv.commands.nodeType(n) != "RVSourceGroup"),
                None
            )
        if seq_node is None:
            _log(f"apply view-state: no seq_node found for timeline {timeline_guid}")
            return
        seq_tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(seq_node)
        if seq_tl_guid:
            self.plugin.sync_manager.active_timeline_guid = seq_tl_guid
        self.plugin._rv_updating = True
        try:
            rv.commands.setViewNode(seq_node)
            _log(f"apply view-state: sequence → {seq_node} ({(timeline_guid or '')[:8]})")
        finally:
            self.plugin._rv_updating = False

    def _switch_to_source_view(self, clip_guid):
        """Switch the RV view to the source group for *clip_guid* (source mode)."""
        if not clip_guid:
            _log("apply view-state: source mode with empty clip_guid — ignoring")
            return
        clip = self.plugin.sync_manager._object_map.get(clip_guid) if self.plugin.sync_manager else None
        if clip is None or not isinstance(clip, otio.schema.Clip):
            # Bin clip guid: xStudio sometimes sends the flat-playlist clip guid
            # (which is purged from object_map when the playlist is replaced by a
            # real sequence).  Fall back to the cached bin guid → path mapping.
            media_path = self.plugin.sequence._bin_guid_to_path.get(clip_guid)
            if not media_path:
                _log(f"apply view-state: clip_guid={clip_guid} not found in object_map or bin cache")
                return
            _log(f"apply view-state: resolving bin clip_guid={clip_guid[:8]} via bin cache → {media_path}")
        else:
            ref = clip.media_reference
            if not isinstance(ref, otio.schema.ExternalReference):
                return
            media_path = _media_path(ref.target_url)
        source_group = self.plugin.sequence._path_to_source_group_map().get(media_path)
        if not source_group:
            _log(f"apply view-state: no source group for {media_path}")
            return
        clip_tl_guid = self.plugin.sync_manager.get_or_create_clip_timeline(clip_guid)
        if clip_tl_guid:
            self.plugin.sync_manager.active_timeline_guid = clip_tl_guid
        self.plugin._rv_updating = True
        try:
            rv.commands.setViewNode(source_group)
            rv.commands.setFrame(1)  # jump to first frame of this source
            _log(f"apply view-state: source → {source_group} (clip {clip_guid[:8]})")
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

    def _clip_guid_for_media_and_frame(self, media_path, media_frame):
        """Return the OTIO clip guid for the occurrence of media_path covering media_frame.

        For OTIO cut sequences the same file can appear at multiple positions with
        different source_range values.  We pick the clip whose source_range contains
        media_frame (the absolute media/timecode frame stored in RV paint node names).
        Falls back to the first path-match for native single-occurrence timelines or
        when no source_range covers the frame.
        """
        import os
        norm = os.path.abspath(_media_path(media_path))
        fallback = None
        for guid, obj in self.plugin.sync_manager._object_map.items():
            if not isinstance(obj, otio.schema.Clip):
                continue
            ref = obj.media_reference
            if not isinstance(ref, otio.schema.ExternalReference):
                continue
            ref_norm = os.path.abspath(_media_path(ref.target_url))
            if ref.target_url != media_path and ref_norm != norm:
                continue
            if fallback is None:
                fallback = guid
            sr = obj.source_range
            if sr is not None:
                start = int(sr.start_time.value)
                end = start + int(sr.duration.value) - 1
                if start <= int(media_frame) <= end:
                    _log(f"LOOKUP CLIP (frame={media_frame}): matched occurrence {guid} [{start}..{end}]")
                    return guid
        if fallback:
            _log(f"LOOKUP CLIP (frame={media_frame}): no source_range match, using fallback {fallback}")
        else:
            _log(f"LOOKUP CLIP (frame={media_frame}): NO MATCH FOUND")
        return fallback

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
        # first one as a highlight (the view mode doesn't change — RV's native
        # "selection" is a loop/highlight concept, not a view switch; see
        # design.md's resolved highlight-only note).
        sg_to_path = {v: k for k, v in self.plugin.sequence._path_to_source_group_map().items()}
        for node in selection:
            media_path = sg_to_path.get(node)
            if media_path:
                clip_guid = self._clip_guid_for_media_path(media_path)
                if clip_guid:
                    _clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
                    _clip_label = getattr(_clip_obj, "name", None) or clip_guid[:8]
                    _log(f"SEND view-state [selection-change]: clip '{_clip_label}' guid={clip_guid[:8]} node={node}")
                    self.broadcast_view_state(clip_guid, self._cur_view_mode)
                    break
        event.reject()

    def on_view_changed(self, event):
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        view = rv.commands.viewNode()
        tl_guid = self.plugin.sequence._rv_node_to_timeline_guid.get(view)
        if tl_guid and rv.commands.nodeType(view) != "RVSourceGroup":
            # Sequence/timeline view — covers both switching to a different
            # sequence and returning from a source (clip) view.
            if tl_guid != self.plugin.sync_manager.active_timeline_guid or self._cur_view_mode != "sequence":
                self.plugin.sync_manager.active_timeline_guid = tl_guid
                _log(f"SEND view-state [sequence]: view={view} tl={tl_guid}")
                self.broadcast_view_state(None, "sequence")
        elif rv.commands.nodeType(view) == "RVSourceGroup":
            # Clip selection: user double-clicked into a source group.
            # Map source group → media path → OTIO clip GUID and broadcast.
            sg_to_path = {v: k for k, v in self.plugin.sequence._path_to_source_group_map().items()}
            media_path = sg_to_path.get(view)
            if media_path:
                clip_guid = self._clip_guid_for_media_path(media_path)
                if clip_guid and clip_guid != self._cur_clip_guid:
                    _clip_obj = self.plugin.sync_manager._object_map.get(clip_guid)
                    _clip_label = getattr(_clip_obj, "name", None) or clip_guid[:8]
                    _log(f"SEND view-state [source]: clip '{_clip_label}' guid={clip_guid[:8]} view={view}")
                    is_new = clip_guid not in self.plugin.sync_manager._clip_timelines
                    clip_tl_guid = self.plugin.sync_manager.get_or_create_clip_timeline(clip_guid)
                    if clip_tl_guid:
                        if is_new:
                            self.plugin.sync_manager.broadcast_clip_timeline(clip_tl_guid)
                        self.plugin.sync_manager.active_timeline_guid = clip_tl_guid
                    self.broadcast_view_state(clip_guid, "source")
        event.reject()
