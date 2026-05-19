import rv.commands
import rv.rvtypes
import logging as _logging
import json
import os
import time
import re


def _make_otio_logger():
    logger = _logging.getLogger("otio_sync")
    if logger.handlers:
        return logger
    logger.setLevel(_logging.DEBUG)
    logger.propagate = False
    ts_fmt = _logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S")
    if os.environ.get("DEBUG_OTIO_SYNC"):
        sh = _logging.StreamHandler()
        sh.setFormatter(_logging.Formatter("[OTIOSync] %(message)s"))
        logger.addHandler(sh)
    log_file = os.environ.get("RV_OTIO_SYNC_LOG_FILE")
    if log_file:
        fh = _logging.FileHandler(log_file)
        fh.setFormatter(ts_fmt)
        logger.addHandler(fh)
    return logger


_otio_logger = _make_otio_logger()


def _log(msg):
    if _otio_logger.handlers:
        _otio_logger.debug(msg)


def _log_exc(msg):
    if _otio_logger.handlers:
        _otio_logger.exception(msg)


def _install_excepthook():
    import sys
    import traceback
    _prev = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        _otio_logger.error(
            "Uncaught exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        _prev(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


if _otio_logger.handlers:
    _install_excepthook()


try:
    from otio_sync_core import SyncManager, RabbitMQNetwork
    from otio_sync_core.manager import STATE_DISCOVERING, STATE_SYNCED, STATE_JOINING
    import opentimelineio as otio
except ImportError as e:
    SyncManager = None
    RabbitMQNetwork = None
    _log(f"Import error: {e}")

try:
    from PySide2 import QtCore
except ImportError:
    try:
        from PySide6 import QtCore
    except ImportError:
        QtCore = None

SYNC_DEMO_TRACK_UUID = "otio-sync-demo-track-0"
SYNC_SESSION_ID = "otio-sync-demo"

class OpenRVSyncPlugin(rv.rvtypes.MinorMode):
    def __init__(self):
        rv.rvtypes.MinorMode.__init__(self)

        menus = [
            ("OTIO Sync", [
                ("Add Clip to Timeline...", self.do_add_clip, None, lambda: rv.commands.NeutralMenuState),
                ("_", None),
                ("Sync Status", self.do_show_status, None, lambda: rv.commands.NeutralMenuState),
            ])
        ]

        self.init("openrv_sync_plugin", [
            ("play-start", self.on_rv_play_start, "Broadcast Play"),
            ("play-stop", self.on_rv_play_stop, "Broadcast Stop"),
            ("frame-changed", self.on_rv_frame_changed, "Broadcast Frame"),
            ("selection-changed", self.on_rv_selection_changed, "Broadcast Selection"),
            ("graph-state-change", self.on_rv_graph_state_change, "Broadcast Annotation"),
            ("after-graph-view-change", self.on_rv_view_changed, "Broadcast View"),
        ], None, menus)

        self.sync_manager = None
        self._rv_updating = False
        self._track = None
        self._active_media_track_guid = None
        self._rv_node_to_timeline_guid = {}  # RV node name → timeline GUID
        self._sequence_input_order = {}      # RV node name → [source_group, ...]
        self._timer = None
        self._last_broadcast_frame = -1
        self._last_selection = []
        self._discovery_start_time = 0
        self._pending_stroke = None  # (node_name, pen_component)
        self._debounce_timer = None

        if SyncManager and RabbitMQNetwork:
            self._setup_sync()
        else:
            _log("SyncManager/RabbitMQNetwork not available")

    def _setup_sync(self):
        # Create manager first to get a GUID
        self.sync_manager = SyncManager(session_id=SYNC_SESSION_ID)
        
        # Pass that GUID to the network
        network = RabbitMQNetwork(host='localhost', session_id=SYNC_SESSION_ID, self_guid=self.sync_manager.self_guid)
        self.sync_manager.network = network

        # Start Discovery Handshake
        _log(f"Starting Master Discovery (ID: {self.sync_manager.self_guid})...")
        self.sync_manager.start_session()
        self._discovery_start_time = time.time()

        @self.sync_manager.on_property_changed
        def _on_property_changed(target_uuid, path, new_value):
            if not self._rv_updating:
                rv.commands.redraw()

        @self.sync_manager.on_hierarchy_changed
        def _on_hierarchy_changed(parent_uuid, action, child_uuid):
            # Only call addSource for remote inserts; local callers (do_add_clip)
            # already called addSource before insert_child.
            # Skip addSource for duplicate paths — the source group already exists.
            if action == "insert_child" and self.sync_manager._is_syncing:
                child = self.sync_manager._object_map.get(child_uuid)
                if isinstance(child, otio.schema.Clip):
                    ref = child.media_reference
                    if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                        if ref.target_url not in self._path_to_source_group_map():
                            rv.commands.addSource(ref.target_url)

        if QtCore:
            self._timer = QtCore.QTimer()
            self._timer.timeout.connect(self.poll_network)
            self._timer.start(100)

    def _init_as_master(self):
        """Initialise the session as the first participant (Master)."""
        self.sync_manager.is_master = True
        self.sync_manager.status = STATE_SYNCED

        try:
            fps = rv.commands.fps()
        except Exception:
            fps = 24.0

        seq_groups = rv.commands.nodesOfType("RVSequenceGroup")
        if seq_groups:
            self._init_timelines_from_sequences(seq_groups, fps)
            # If the graph wasn't fully wired yet all counts will be zero.
            # Schedule a retry so we pick up the clips once RV settles.
            total_clips = sum(
                len(list(tr))
                for tl in self.sync_manager._timelines.values()
                for tr in tl.tracks
            )
            if total_clips == 0:
                _log("No clips found on init — scheduling graph-settled retry")
                QtCore.QTimer.singleShot(500, self._retry_init_timelines)
        else:
            self._init_single_timeline(fps)

        self.sync_manager.broadcast_master_response()
        _log("Session Initialized as MASTER")

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
        self.sync_manager._timelines.clear()
        self.sync_manager._object_map.clear()
        self.sync_manager.active_timeline_guid = None
        self._rv_node_to_timeline_guid.clear()
        self._sequence_input_order.clear()
        self._active_media_track_guid = None
        self._track = None

        self._init_timelines_from_sequences(seq_groups, fps)
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
            return otio.schema.Clip(
                name=os.path.basename(path),
                media_reference=otio.schema.ExternalReference(target_url=path, available_range=time_range)
            )
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

    def _init_timelines_from_sequences(self, seq_groups, fps):
        """Create one OTIO timeline per RVSequenceGroup and register each."""
        try:
            current_view = rv.commands.viewNode()
        except Exception:
            current_view = None

        seq_sources = self._source_groups_for_sequences(seq_groups)
        _log(f"Sequence source counts: { {k: len(v) for k, v in seq_sources.items()} }")

        for seq_group in seq_groups:
            try:
                seq_name = rv.commands.getStringProperty(f"{seq_group}.ui.name")[0]
            except Exception:
                seq_name = seq_group

            timeline = otio.schema.Timeline(seq_name)
            stack = otio.schema.Stack("tracks")
            timeline.tracks = stack

            media_track = otio.schema.Track("Media")
            stack.append(media_track)
            annotations_track = otio.schema.Track("Annotations")
            stack.append(annotations_track)

            edl_counts = self._edl_frame_counts(seq_group)
            _log(f"EDL frame counts for {seq_group}: {edl_counts}")
            for idx, sg in enumerate(seq_sources.get(seq_group, [])):
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

            self.sync_manager.register_timeline(timeline)
            # Read UUIDs back after registration (assigned by _ensure_guid_and_map)
            track_guid = media_track.metadata["sync"]["guid"]
            tl_guid = timeline.metadata["sync"]["guid"]
            self._rv_node_to_timeline_guid[seq_group] = tl_guid
            self._sequence_input_order[seq_group] = self._get_sequence_inputs(seq_group)

            if self._active_media_track_guid is None:
                self._active_media_track_guid = track_guid
                self._track = media_track

            if seq_group == current_view:
                self.sync_manager.active_timeline_guid = tl_guid
                self._active_media_track_guid = track_guid
                self._track = media_track

    def _init_single_timeline(self, fps):
        """Fallback: one timeline containing all open RVFileSource nodes."""
        timeline = otio.schema.Timeline("Sync Demo Timeline")
        stack = otio.schema.Stack("tracks")
        timeline.tracks = stack

        media_track = otio.schema.Track("Media")
        stack.append(media_track)
        annotations_track = otio.schema.Track("Annotations")
        stack.append(annotations_track)

        for source_node in rv.commands.nodesOfType("RVFileSource"):
            clip = self._make_clip(source_node, fps)
            if clip:
                media_track.append(clip)
                _log(f"Auto-imported existing source: {clip.name}")

        self.sync_manager.register_timeline(timeline)
        self._active_media_track_guid = media_track.metadata["sync"]["guid"]
        self._track = media_track
        try:
            tl_guid = timeline.metadata["sync"]["guid"]
            view = rv.commands.viewNode()
            self._rv_node_to_timeline_guid[view] = tl_guid
            self._sequence_input_order[view] = self._get_sequence_inputs(view)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Network Polling & State Handshake
    # ------------------------------------------------------------------

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

    def _check_sequence_reorders(self):
        """Detect clip deletions and reorders in any tracked sequence and broadcast patches."""
        if not self.sync_manager or self.sync_manager.status != STATE_SYNCED:
            return
        sg_to_path = {v: k for k, v in self._path_to_source_group_map().items()}
        for seq_group, tl_guid in list(self._rv_node_to_timeline_guid.items()):
            current = self._get_sequence_inputs(seq_group)
            stored = self._sequence_input_order.get(seq_group)
            if stored is None or current == stored:
                continue
            _log(f"Sequence changed in {seq_group}: {stored} -> {current}")
            self._sequence_input_order[seq_group] = current

            timeline = self.sync_manager._timelines.get(tl_guid)
            if not timeline:
                continue
            media_track = next((t for t in timeline.tracks if t.name == "Media"), None)
            if not media_track:
                continue
            track_guid = media_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                continue

            def _build_path_to_guid():
                result = {}
                for clip in media_track:
                    ref = clip.media_reference
                    if hasattr(ref, "target_url") and ref.target_url:
                        result[ref.target_url] = clip.metadata.get("sync", {}).get("guid")
                return result

            # --- Deletions: source groups present in stored but gone from current ---
            current_set = set(current)
            for sg in stored:
                if sg not in current_set:
                    path = sg_to_path.get(sg)
                    if not path:
                        continue
                    child_guid = _build_path_to_guid().get(path)
                    if not child_guid:
                        _log(f"Delete: no guid for removed sg={sg}")
                        continue
                    _log(f"Delete: broadcasting remove_child sg={sg} child={child_guid}")
                    self.sync_manager.broadcast_remove_child(track_guid, child_guid)

            # --- Additions: source groups whose path count exceeds the OTIO track count ---
            # Uses a Counter so that adding a duplicate of an existing clip is detected.
            from collections import Counter
            otio_path_counts = Counter(
                clip.media_reference.target_url
                for clip in media_track
                if hasattr(clip.media_reference, "target_url") and clip.media_reference.target_url
            )
            seen_counts = Counter()
            for otio_idx, sg in enumerate(current):
                path = sg_to_path.get(sg)
                if not path:
                    continue
                seen_counts[path] += 1
                if seen_counts[path] > otio_path_counts[path]:
                    clip = self._make_otio_clip_for_sg(sg)
                    if clip:
                        _log(f"Add: broadcasting insert_child sg={sg} at index={otio_idx}")
                        self.sync_manager.insert_child(track_guid, clip, otio_idx)
                        otio_path_counts[path] += 1

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
                    self.sync_manager.broadcast_move_child(track_guid, child_guid, target_idx)
                    current_order.pop(cur_idx)
                    current_order.insert(target_idx, child_guid)

    def _apply_insert_child(self, clip_obj):
        """Connect a newly-received source group to the right sequence group."""
        if not isinstance(clip_obj, otio.schema.Clip):
            return
        clip_guid = clip_obj.metadata.get("sync", {}).get("guid")
        for seq_group, tl_guid in self._rv_node_to_timeline_guid.items():
            timeline = self.sync_manager._timelines.get(tl_guid)
            if not timeline:
                continue
            for track in timeline.tracks:
                if track.name != "Media":
                    continue
                if not any(c.metadata.get("sync", {}).get("guid") == clip_guid for c in track):
                    continue
                path_to_sg = self._path_to_source_group_map()
                new_inputs = []
                for c in track:
                    ref = c.media_reference
                    if hasattr(ref, "target_url") and ref.target_url:
                        sg = path_to_sg.get(ref.target_url)
                        if sg:
                            new_inputs.append(sg)
                if new_inputs:
                    rv.commands.setNodeInputs(seq_group, new_inputs)
                    self._sequence_input_order[seq_group] = new_inputs
                    _log(f"RECV insert_child: {seq_group} now has {len(new_inputs)} inputs")
                    rv.commands.redraw()
                return

    def _apply_remove_child(self, data):
        """Apply a REMOVE_CHILD patch to the RV session after OTIO has already been updated."""
        parent_uuid = data.get("parent_uuid")
        for seq_group, tl_guid in self._rv_node_to_timeline_guid.items():
            timeline = self.sync_manager._timelines.get(tl_guid)
            if not timeline:
                continue
            for track in timeline.tracks:
                if track.name != "Media":
                    continue
                if track.metadata.get("sync", {}).get("guid") != parent_uuid:
                    continue
                path_to_sg = self._path_to_source_group_map()
                new_inputs = []
                for clip in track:
                    ref = clip.media_reference
                    if hasattr(ref, "target_url") and ref.target_url:
                        sg = path_to_sg.get(ref.target_url)
                        if sg:
                            new_inputs.append(sg)
                rv.commands.setNodeInputs(seq_group, new_inputs)
                self._sequence_input_order[seq_group] = new_inputs
                _log(f"RECV remove_child: {seq_group} now has {len(new_inputs)} inputs")
                rv.commands.redraw()
                return

    def _apply_move_child(self, data):
        """Apply a MOVE_CHILD patch to the RV session after OTIO has already been updated."""
        timeline_guid = None
        parent_uuid = data.get("parent_uuid")
        # Find which timeline and sequence group own this track
        for seq_group, tl_guid in self._rv_node_to_timeline_guid.items():
            timeline = self.sync_manager._timelines.get(tl_guid)
            if not timeline:
                continue
            for track in timeline.tracks:
                if track.name == "Media" and track.metadata.get("sync", {}).get("guid") == parent_uuid:
                    timeline_guid = tl_guid
                    # Rebuild RV sequence inputs from the updated OTIO track order
                    path_to_sg = self._path_to_source_group_map()
                    new_inputs = []
                    for clip in track:
                        ref = clip.media_reference
                        if hasattr(ref, "target_url") and ref.target_url:
                            sg = path_to_sg.get(ref.target_url)
                            if sg:
                                new_inputs.append(sg)
                    if new_inputs:
                        rv.commands.setNodeInputs(seq_group, new_inputs)
                        self._sequence_input_order[seq_group] = new_inputs
                        _log(f"RECV move_child: reordered {seq_group} → {new_inputs}")
                    rv.commands.redraw()
                    return

    def poll_network(self):
        if not self.sync_manager: return

        # 1. Process Network First (important!)
        results = self.sync_manager.receive_and_apply_all()

        # 2. Check for sequence reorders (poll every tick, cheap)
        if not self._rv_updating:
            self._check_sequence_reorders()

        # 3. Check for Master Discovery Timeout
        if self.sync_manager.status == STATE_DISCOVERING:
            self.sync_manager.broadcast_master_discovery()
            if time.time() - self._discovery_start_time > 2.0:
                self._init_as_master()

        for action, data in results:
            self._rv_updating = True
            try:
                # 1. Session Handshake Logic
                if action == "master_found":
                    _log("master_found — requesting state")
                    self.sync_manager.request_state()

                elif action == "state_request_received":
                    _log("state_request_received — sending snapshot")
                    try:
                        fps = rv.commands.fps()
                        frame = rv.commands.frame()
                        playing = rv.commands.isPlaying()
                        view = rv.commands.viewNode()
                        tl_guid = self._rv_node_to_timeline_guid.get(view) or self.sync_manager.active_timeline_guid
                        playback_state = {
                            "playing": playing,
                            "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": float(frame - 1), "rate": float(fps)},
                            "looping": True,
                            "timeline_guid": tl_guid,
                        }
                    except Exception:
                        playback_state = None
                    self.sync_manager.send_state_snapshot(data, playback_state=playback_state)

                elif action == "state_snapshot_received":
                    _log("state_snapshot_received — applying snapshot")
                    replay_results = self.sync_manager.apply_snapshot(data)
                    self._rebuild_rv_session()
                    playback_state = data.get("playback_state")
                    if playback_state:
                        self._apply_playback(playback_state)
                    # Process any results that were buffered
                    for r_action, r_data in replay_results:
                        self._handle_action(r_action, r_data)

                # 2. Normal Sync Actions
                else:
                    self._handle_action(action, data)
            finally:
                self._rv_updating = False

    def _handle_action(self, action, data):
        """Common dispatcher for sync actions."""
        _log(f"RECV action={action}")
        if action == "playback_settings":
            self._apply_playback(data)
        elif action == "selection_changed":
            self._apply_selection(data)
        elif action == "annotation_stroke_release":
            self._apply_annotation(data)
        elif action == "insert_child":
            self._apply_insert_child(data)
        elif action == "remove_child":
            self._apply_remove_child(data)
        elif action == "move_child":
            self._apply_move_child(data)
        else:
            _log(f"RECV unhandled action={action}")

    def _path_to_source_group_map(self):
        """Return {path: source_group_node_name} for all currently loaded RVSourceGroups."""
        mapping = {}
        for sg in rv.commands.nodesOfType("RVSourceGroup"):
            try:
                for n in rv.commands.nodesInGroup(sg):
                    if rv.commands.nodeType(n) == "RVFileSource":
                        path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                        if path:
                            mapping[path] = sg
            except Exception:
                pass
        return mapping

    def _rebuild_rv_session(self):
        """Clear and rebuild the RV session based on the current OTIO timelines."""
        _log("Rebuilding RV session from OTIO snapshot...")
        if not self.sync_manager._timelines: return

        timelines = list(self.sync_manager._timelines.values())

        # Pass 1: load every unique path once.
        # addSource in RV may be deferred, so we scan for source groups in a
        # separate pass after all loads are done.
        already_loaded = {p for p in self._path_to_source_group_map()}
        all_paths_ordered = []   # preserves per-timeline clip order
        seen = set()
        for timeline in timelines:
            for item in timeline.tracks:
                if item.name != "Media":
                    continue
                for child in item:
                    if not isinstance(child, otio.schema.Clip):
                        continue
                    ref = child.media_reference
                    if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
                        continue
                    if ref.target_url not in seen:
                        all_paths_ordered.append(ref.target_url)
                        seen.add(ref.target_url)

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
                    if item.name != "Media":
                        continue
                    for child in item:
                        if not isinstance(child, otio.schema.Clip):
                            continue
                        ref = child.media_reference
                        if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                            sg = path_to_sg.get(ref.target_url)
                            if sg:
                                timeline_sgs.append(sg)
                if timeline_sgs:
                    try:
                        seq_node = rv.commands.newNode("RVSequenceGroup", timeline.name)
                        rv.commands.setNodeInputs(seq_node, list(timeline_sgs))
                        tl_guid = timeline.metadata.get("sync", {}).get("guid")
                        if tl_guid:
                            self._rv_node_to_timeline_guid[seq_node] = tl_guid
                        self._sequence_input_order[seq_node] = list(timeline_sgs)
                        _log(f"Created sequence '{timeline.name}' with {len(timeline_sgs)} sources")
                    except Exception as e:
                        _log(f"Could not create sequence '{timeline.name}': {e}")

        # Pass 4: replay annotations.
        for timeline in timelines:
            for item in timeline.tracks:
                if item.name and item.name.startswith("Annotations"):
                    for child in item:
                        if isinstance(child, otio.schema.Clip):
                            if "annotation_commands" not in child.metadata:
                                continue
                            frame = child.metadata.get("rv_frame", 1)
                            node_name = child.metadata.get("annotated_clip_name", "unknown")
                            media_path = child.metadata.get("media_path")

                            event_groups = {}
                            for event in child.metadata["annotation_commands"]:
                                if hasattr(event, "uuid"):
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
                                    "cap": 1
                                }
                                self._apply_annotation(data)

        # Set active media track so do_add_clip works on clients too
        active_tl = self.sync_manager._timelines.get(self.sync_manager.active_timeline_guid)
        if active_tl:
            for track in active_tl.tracks:
                if track.name == "Media":
                    self._active_media_track_guid = track.metadata.get("sync", {}).get("guid")
                    self._track = track
                    break

        rv.commands.redraw()

    # ------------------------------------------------------------------
    # RV Event Callbacks (Outgoing)
    # ------------------------------------------------------------------

    def _broadcast_playback(self):
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED: return
        fps = rv.commands.fps()
        current_frame = rv.commands.frame()
        playing = rv.commands.isPlaying()
        try:
            looping = rv.commands.playMode() == 0
        except AttributeError:
            looping = True

        view = rv.commands.viewNode()
        timeline_guid = self._rv_node_to_timeline_guid.get(view) or self.sync_manager.active_timeline_guid
        _log(f"SEND playback playing={playing} frame={current_frame} fps={fps} view={view} tl={timeline_guid}")
        state = {
            "playing": playing,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(current_frame - 1),  # 0-indexed to match OTIO track time
                "rate": float(fps)
            },
            "looping": looping,
            "muted": False,
            "scrubbing": False
        }
        self.sync_manager.broadcast_playback_state(state, timeline_guid=timeline_guid)

    def on_rv_view_changed(self, event):
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        view = rv.commands.viewNode()
        tl_guid = self._rv_node_to_timeline_guid.get(view)
        if tl_guid and tl_guid != self.sync_manager.active_timeline_guid:
            self.sync_manager.active_timeline_guid = tl_guid
            _log(f"SEND view_change view={view} tl={tl_guid}")
            self._broadcast_playback()
        event.reject()

    def on_rv_play_start(self, event):
        self._broadcast_playback()
        event.reject()

    def on_rv_play_stop(self, event):
        self._broadcast_playback()
        event.reject()

    def on_rv_frame_changed(self, event):
        if self._rv_updating: event.reject(); return
        current_frame = rv.commands.frame()
        if not rv.commands.isPlaying() and current_frame != self._last_broadcast_frame:
            self._broadcast_playback()
            self._last_broadcast_frame = current_frame
        event.reject()

    def on_rv_selection_changed(self, event):
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED: return
        selection = rv.commands.selection()
        if selection != self._last_selection:
            _log(f"SEND selection={selection}")
            self.sync_manager.broadcast_selection(selection)
            self._last_selection = selection
        event.reject()

    def on_rv_graph_state_change(self, event):
        contents = event.contents()
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        # Trigger on pen point changes: node.pen:N:F:user.points
        if ".pen:" in contents and contents.endswith(".points"):
            parts = contents.split(".")
            if len(parts) == 3:
                node_name, pen_component = parts[0], parts[1]
                _log(f"pen points updated: {node_name}.{pen_component}")
                if self._pending_stroke and self._pending_stroke[1] != pen_component:
                    if self._debounce_timer:
                        self._debounce_timer.stop()
                    self._flush_pending_stroke()
                self._pending_stroke = (node_name, pen_component)
                if self._debounce_timer is None:
                    self._debounce_timer = QtCore.QTimer()
                    self._debounce_timer.setSingleShot(True)
                    self._debounce_timer.timeout.connect(self._flush_pending_stroke)
                self._debounce_timer.start(150)
        event.reject()

    def _flush_pending_stroke(self):
        if not self._pending_stroke:
            return
        node_name, pen_component = self._pending_stroke
        self._pending_stroke = None
        self._broadcast_annotation(node_name, pen_component)

    def _broadcast_annotation(self, node_name, pen_component):
        _log(f"SEND annotation node={node_name} pen={pen_component}")
        try:
            full_prop = f"{node_name}.{pen_component}"
            points = rv.commands.getFloatProperty(f"{full_prop}.points")
            if not points:
                _log(f"SEND annotation skipped: no points on {full_prop}")
                return
            color = rv.commands.getFloatProperty(f"{full_prop}.color")
            brush = rv.commands.getStringProperty(f"{full_prop}.brush")[0]
            width = rv.commands.getFloatProperty(f"{full_prop}.width")
            join = rv.commands.getIntProperty(f"{full_prop}.join")[0]
            cap = rv.commands.getIntProperty(f"{full_prop}.cap")[0]
            frame = int(pen_component.split(":")[2])
            
            import uuid
            try:
                otio.schema.schemadef.module_from_name('SyncEvent')
                penuuid = str(uuid.uuid4())
                start_event = otio.schemadef.SyncEvent.PaintStart(
                    brush=brush,
                    rgba=list(color),
                    friendly_name=pen_component.split(':')[-1],
                    uuid=penuuid
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
            except Exception:
                start_event_data = None
                points_event_data = None

            # Resolve media_path from the paint node name.
            # Frame numbers in RV pen properties are clip-local, not global sequence
            # frames, so metaEvaluateClosestByType(frame) would land on the wrong clip.
            # Instead we parse the node name to find the real source group.
            #
            # Node name formats:
            #   {seq}_p_{display_slot}  — sequence context (e.g. Default_p_sourceGroup000005)
            #   {sg}_paint              — direct-view context (e.g. sourceGroup000004_paint)
            #
            # When a source group appears twice in a sequence, RV creates an autogenerated
            # display slot (e.g. sourceGroup000005) that is NOT itself a real source group.
            # In that case we extract the slot index from the name and look up the
            # actual source group from the sequence's input list.
            media_path = None

            if "_p_" in node_name:
                seq_name = node_name.split("_p_")[0]
                display_slot = node_name.split("_p_")[1]

                # First: direct lookup — display_slot is a real source group
                try:
                    for n in rv.commands.nodesInGroup(display_slot):
                        if rv.commands.nodeType(n) == "RVFileSource":
                            try:
                                path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                                if path:
                                    media_path = path
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass

                if not media_path:
                    # Fallback: display_slot is an autogenerated slot node inside the
                    # sequence whose numeric suffix IS the sequence input index.
                    m = re.match(r'^sourceGroup(\d+)$', display_slot)
                    if m:
                        slot_idx = int(m.group(1))
                        seq_inputs = self._get_sequence_inputs(seq_name)
                        if 0 <= slot_idx < len(seq_inputs):
                            actual_sg = seq_inputs[slot_idx]
                            for n in rv.commands.nodesInGroup(actual_sg):
                                if rv.commands.nodeType(n) == "RVFileSource":
                                    try:
                                        path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                                        if path:
                                            media_path = path
                                            break
                                    except Exception:
                                        pass

            elif node_name.endswith("_paint"):
                source_group = node_name[:-len("_paint")]
                try:
                    for n in rv.commands.nodesInGroup(source_group):
                        if rv.commands.nodeType(n) == "RVFileSource":
                            try:
                                path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                                if path:
                                    media_path = path
                                    break
                            except Exception:
                                pass
                except Exception:
                    pass

            data = {
                "media_path": media_path,
                "fps": rv.commands.fps(),
                "node_name": node_name,
                "frame": frame, "points": list(points), "color": list(color),
                "brush": brush, "width": list(width), "join": join, "cap": cap,
                "start_event_data": start_event_data,
                "points_event_data": points_event_data,
                "timeline_guid": self.sync_manager.active_timeline_guid,
                "sync_timestamp": time.time()
            }
            self.sync_manager.broadcast_annotation(data)
        except Exception as e:
            _log_exc(f"Failed to broadcast annotation: {e}")

    def _apply_playback(self, data):
        playing = data.get("playing", False)
        current_time = data.get("current_time", {})
        target_frame = int(current_time.get("value", 0)) + 1  # protocol is 0-indexed; RV is 1-based
        timeline_guid = data.get("timeline_guid")
        _log(f"RECV playback playing={playing} frame={target_frame} tl={timeline_guid}")

        if timeline_guid:
            for rv_node, tl_guid in self._rv_node_to_timeline_guid.items():
                if tl_guid == timeline_guid and rv.commands.viewNode() != rv_node:
                    _log(f"RECV view_change to {rv_node}")
                    rv.commands.setViewNode(rv_node)
                    break

        if rv.commands.frame() != target_frame:
            rv.commands.setFrame(target_frame)
        is_playing = rv.commands.isPlaying()
        if playing and not is_playing:
            rv.commands.play()
        elif not playing and is_playing:
            rv.commands.stop()

    def _apply_selection(self, data):
        nodes = data.get("nodes", [])
        if nodes:
            _log(f"RECV selection nodes={nodes}")
            rv.commands.setSelection(nodes)

    def _find_paint_node_for_media(self, media_path, frame):
        """Find the local RVPaint node for a given media path and frame."""
        sg = self._path_to_source_group_map().get(media_path)
        if sg:
            for n in rv.commands.nodesInGroup(sg):
                try:
                    if rv.commands.nodeType(n) == "RVPaint":
                        return n
                except Exception:
                    pass
        # Fallback: session evaluation at the given frame
        eval_infos = rv.commands.metaEvaluateClosestByType(frame, "RVPaint")
        if eval_infos:
            return eval_infos[0]['node']
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
            _log(f"RECV annotation frame={frame} brush={brush} node={node_name} npts={len(points) // 2 if points else 0}")
            # Always resolve via media_path so we target the local paint node,
            # not the sender's node name (which may match a stale or hidden node).
            node = self._find_paint_node_for_media(media_path, frame)
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
            rv.commands.setIntProperty(f"{full_pen}.mode", [0], True)  # RenderOverMode = 0
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
            rv.commands.setIntProperty(f"{paint_prop}.nextId", [next_id + 1], True)
            QtCore.QTimer.singleShot(0, rv.commands.redraw)
        except Exception as e:
            _log_exc(f"Failed to apply remote annotation: {e}")

    def _apply_insert(self, clip_obj):
        ref = clip_obj.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            rv.commands.addSource(ref.target_url)

    def do_add_clip(self, event=None):
        paths = rv.commands.openFileDialog(False, False, False, "mp4|Movie Files|mov|Movie Files|m4v|Movie Files|mkv|Movie Files|avi|Movie Files", "")
        if not paths: return
        path = paths[0] if isinstance(paths, (list, tuple)) else paths
        
        rv.commands.addSource(path)
        
        import opentimelineio.opentime as otio_time
        try:
            fps = rv.commands.fps()
            start = rv.commands.inPoint()
            end = rv.commands.outPoint()
            duration = end - start + 1
            if start > 0: start -= 1
            time_range = otio_time.TimeRange(otio_time.RationalTime(start, fps), otio_time.RationalTime(duration, fps))
        except Exception:
            time_range = otio_time.TimeRange(otio_time.RationalTime(0, 24), otio_time.RationalTime(10000, 24))
            
        clip = otio.schema.Clip(name=os.path.basename(path), media_reference=otio.schema.ExternalReference(target_url=path, available_range=time_range))
        self.sync_manager.insert_child(self._active_media_track_guid, clip)
        
        if event: event.reject()

    def do_show_status(self, event=None):
        if self.sync_manager:
            role = "MASTER" if self.sync_manager.is_master else "CLIENT"
            _log(f"Session: {self.sync_manager.session_id} | Role: {role} | Status: {self.sync_manager.status}")
        if event: event.reject()

    def deactivate(self):
        if self._timer: self._timer.stop()
        if self.sync_manager: self.sync_manager.close()
        rv.rvtypes.MinorMode.deactivate(self)

def createMode():
    return OpenRVSyncPlugin()
