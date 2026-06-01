import rv.commands
import rv.extra_commands
import rv.rvtypes
import logging as _logging
import json
import os
import re
import time
import uuid
from collections import Counter
import collections.abc


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
        # Mirror to stderr so the log is visible in the terminal alongside the file.
        import sys as _sys
        eh = _logging.StreamHandler(_sys.stderr)
        eh.setFormatter(ts_fmt)
        logger.addHandler(eh)
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


def _show_warning(msg):
    """Display a warning popup in RV (thread-safe, fire-and-forget)."""
    try:
        if QtCore:
            QtCore.QTimer.singleShot(0, lambda: _show_warning_main(msg))
        else:
            _log(f"WARNING: {msg}")
    except Exception:
        _log(f"WARNING: {msg}")


def _show_warning_main(msg):
    """Show the warning on the main thread."""
    try:
        from PySide2.QtWidgets import QMessageBox
    except ImportError:
        try:
            from PySide6.QtWidgets import QMessageBox
        except ImportError:
            _log(f"WARNING: {msg}")
            return
    try:
        mb = QMessageBox()
        mb.setWindowTitle("OTIOSync")
        mb.setText(msg)
        mb.setIcon(QMessageBox.Warning)
        mb.exec_()
    except Exception as e:
        _log(f"_show_warning_main failed: {e}")


def _parse_ori_session(env_val):
    """Parse ``[host:]session_name`` from an env-var string.

    :param env_val: Raw value of ``ORI_SESSION``.
    :returns: ``(host, session_name)`` tuple; host defaults to ``localhost``
        (or ``ORI_RMQ_HOST`` if set) when no colon is present.
    :rtype: tuple
    """
    default_host = os.environ.get("ORI_RMQ_HOST", "127.0.0.1")
    if ":" in env_val:
        host, name = env_val.split(":", 1)
        return (host or default_host, name)
    return (default_host, env_val)


class OpenRVSyncPlugin(rv.rvtypes.MinorMode):
    #: Mode name passed to init() and used as the key in defineModeMenu().
    MENU_NAME = "openrv_sync_plugin"
    #: Display title for the top-level menu entry.
    MENU_TITLE = "OTIO Sync"

    def __init__(self):
        rv.rvtypes.MinorMode.__init__(self)

        self.sync_manager = None
        self._rv_updating = False
        self._track = None
        self._active_media_track_guid = None
        self._rv_node_to_timeline_guid = {}  # RV node name → timeline GUID
        self._sequence_input_order = {}      # RV node name → [source_group, ...]
        self._timer = None
        self._last_broadcast_frame = -1
        self._last_selection = []
        self._last_broadcast_clip_guid = None  # last clip GUID sent via SELECTION
        self._discovery_start_time = 0
        self._pending_stroke = None   # (node_name, pen_component, stroke_uuid)
        self._next_stroke_uuid = None # set when paint.nextId fires; consumed on first .points
        self._stroke_timer = None     # repeating partial-broadcast timer during drawing
        self._last_partial_point_count = 0
        self._partial_pen_nodes = {}  # stroke_uuid → rv pen node name (e.g. "pen:3:42:remote")
        self._last_display_state = {}  # last state broadcast to detect changes
        self._current_session_name = None
        self._current_host = None
        self._pending_create_check = False
        self._sequence_selection_applied_at = 0.0  # monotonic time of last remote sequence-mode selection

        self.init(self.MENU_NAME, [
            ("play-start", self.on_rv_play_start, "Broadcast Play"),
            ("play-stop", self.on_rv_play_stop, "Broadcast Stop"),
            ("frame-changed", self.on_rv_frame_changed, "Broadcast Frame"),
            ("selection-changed", self.on_rv_selection_changed, "Broadcast Selection"),
            ("graph-state-change", self.on_rv_graph_state_change, "Broadcast Annotation"),
            ("after-graph-view-change", self.on_rv_view_changed, "Broadcast View"),
            ("pointer-1--release",      self.on_rv_pen_up, "Pen up (release)"),
            ("pointer--leave",          self.on_rv_pen_up, "Pen up (leave viewport)"),
            ("pointer--control--leave", self.on_rv_pen_up, "Pen up (leave control)"),
        ], None, self._build_menu())

        ori_session = os.environ.get("ORI_SESSION")
        if ori_session and SyncManager and RabbitMQNetwork and QtCore:
            host, name = _parse_ori_session(ori_session)
            QtCore.QTimer.singleShot(0, lambda: self.connect_to_session(host, name))
        elif not SyncManager or not RabbitMQNetwork:
            _log("SyncManager/RabbitMQNetwork not available")

    @property
    def _in_session(self):
        return self.sync_manager is not None

    def _build_menu(self):
        """Return the menu list for the current session state."""
        if self._in_session:
            return [
                (self.MENU_TITLE, [
                    (f"Leave Session ({self._current_session_name})", self.do_leave_session, None,
                     lambda: rv.commands.NeutralMenuState),
                    ("_", None),
                    ("Add Clip to Timeline...", self.do_add_clip, None,
                     lambda: rv.commands.NeutralMenuState),
                    ("Sync Status", self.do_show_status, None,
                     lambda: rv.commands.NeutralMenuState),
                ])
            ]
        return [
            (self.MENU_TITLE, [
                ("Create Session...", self.do_create_session, None,
                 lambda: rv.commands.NeutralMenuState),
                ("Join Session...", self.do_join_session, None,
                 lambda: rv.commands.NeutralMenuState),
                ("_", None),
                ("Add Clip to Timeline...", self.do_add_clip, None,
                 lambda: rv.commands.DisabledMenuState),
                ("Sync Status", self.do_show_status, None,
                 lambda: rv.commands.NeutralMenuState),
            ])
        ]

    def _rebuild_menu(self):
        """Rebuild the OTIO Sync menu to reflect current connection state."""
        try:
            rv.commands.defineModeMenu(self.MENU_NAME, self._build_menu(), True)
        except Exception as e:
            _log(f"_rebuild_menu failed: {e}")

    def connect_to_session(self, host, session_name):
        """Create a SyncManager and join the named session.

        :param host: RabbitMQ hostname.
        :param session_name: Exchange / session name.
        """
        if not SyncManager or not RabbitMQNetwork:
            _log("SyncManager/RabbitMQNetwork not available — cannot connect")
            return
        self.disconnect_from_session()
        self._current_host = host
        self._current_session_name = session_name

        self.sync_manager = SyncManager(session_id=session_name)
        network = RabbitMQNetwork(
            host=host,
            session_id=session_name,
            self_guid=self.sync_manager.self_guid,
        )
        self.sync_manager.network = network

        if not network.wait_until_ready(timeout=5.0):
            _log("Warning: RabbitMQ consumer did not become ready within 5 s")
        _log(f"Starting Master Discovery (ID: {self.sync_manager.self_guid})...")

        @self.sync_manager.on_property_changed
        def _on_property_changed(target_uuid, path, new_value):
            if not self._rv_updating:
                rv.commands.redraw()

        @self.sync_manager.on_hierarchy_changed
        def _on_hierarchy_changed(parent_uuid, action, child_uuid):
            if action == "insert_child" and self.sync_manager.is_syncing:
                child = self.sync_manager._object_map.get(child_uuid)
                if isinstance(child, otio.schema.Clip):
                    ref = child.media_reference
                    if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                        if self._media_path(ref.target_url) not in self._path_to_source_group_map():
                            rv.commands.addSource(self._media_path(ref.target_url))

        @self.sync_manager.on_synced
        def _on_synced():
            if not self.sync_manager.is_master:
                self._rv_updating = True
                try:
                    self._rebuild_rv_session()
                    if self.sync_manager.playback_state:
                        self._apply_playback(self.sync_manager.playback_state)
                    if self.sync_manager.display_state:
                        self._apply_display_state(self.sync_manager.display_state)
                finally:
                    self._rv_updating = False
            if self._pending_create_check:
                self._pending_create_check = False
                if not self.sync_manager.is_master:
                    name = self._current_session_name or ""
                    _show_warning(
                        f"Session '{name}' already exists. "
                        "You have joined as a peer rather than creating a new session."
                    )

        self.sync_manager.start_session()
        self._discovery_start_time = time.time()

        if QtCore and not self._timer:
            self._timer = QtCore.QTimer()
            self._timer.timeout.connect(self.poll_network)
            self._timer.start(33)

        self._rebuild_menu()
        _log(f"Connecting to session '{session_name}' on {host}")

    def disconnect_from_session(self):
        """Stop the poll timer, shut down the network, and return to disconnected state."""
        if self._timer:
            self._timer.stop()
            self._timer = None
        if self.sync_manager:
            self.sync_manager.close()
            self.sync_manager = None
        self._current_session_name = None
        self._current_host = None
        self._pending_create_check = False
        self._rebuild_menu()
        _log("Disconnected from session")

    def _init_as_master(self):
        """Initialise the session as the first participant (Master)."""
        self.sync_manager.is_master = True
        self.sync_manager._set_status(STATE_SYNCED)

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
        self._import_existing_rv_annotations()
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
        self.sync_manager.reset_timelines()
        self._rv_node_to_timeline_guid.clear()
        self._sequence_input_order.clear()
        self._active_media_track_guid = None
        self._track = None

        self._init_timelines_from_sequences(seq_groups, fps)
        self._import_existing_rv_annotations()
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

    def _poll_new_sequences(self):
        """Detect newly created RVSequenceGroups and broadcast them as new timelines.

        Runs on any synced peer (not just the master).  Any ``RVSequenceGroup``
        node not yet in ``_rv_node_to_timeline_guid`` is built into an OTIO
        timeline, registered in the sync manager, and broadcast to all peers via
        :meth:`~otio_sync_core.manager.SyncManager.broadcast_add_timeline`.
        When a peer receives a remote ``add_timeline`` it registers the resulting
        ``RVSequenceGroup`` in ``_rv_node_to_timeline_guid`` immediately, so
        this method will not re-broadcast it on the next poll.
        """
        if not self.sync_manager:
            return
        if self.sync_manager.status != STATE_SYNCED:
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
            timeline = otio.schema.Timeline(seq_name)
            stack = otio.schema.Stack("tracks")
            timeline.tracks = stack
            media_track = otio.schema.Track("Media")
            stack.append(media_track)
            ann_track = otio.schema.Track("Annotations")
            stack.append(ann_track)
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
            self.sync_manager.register_timeline(timeline)
            tl_guid = timeline.metadata["sync"]["guid"]
            self._rv_node_to_timeline_guid[seq_group] = tl_guid
            self._sequence_input_order[seq_group] = self._get_sequence_inputs(seq_group)
            self.sync_manager.broadcast_add_timeline(tl_guid)
            _log(f"New RVSequenceGroup '{seq_name}' → timeline {tl_guid[:8]} broadcast")

    def _poll_sequence_renames(self):
        """Detect and broadcast RVSequenceGroup name changes.

        Runs on any synced peer (not just the master).  Compares the current
        ``ui.name`` property of each tracked sequence group against the OTIO
        timeline name stored in the sync manager.  When a change is detected,
        :meth:`~otio_sync_core.manager.SyncManager.broadcast_timeline_rename`
        is called to propagate it to all peers.
        """
        if not self.sync_manager:
            return
        if self.sync_manager.status != STATE_SYNCED:
            return
        for seq_group, tl_guid in list(self._rv_node_to_timeline_guid.items()):
            tl = self.sync_manager._timelines.get(tl_guid)
            if tl is None:
                continue
            try:
                current_name = rv.commands.getStringProperty(f"{seq_group}.ui.name")[0]
            except Exception:
                continue
            if current_name and current_name != (tl.name or ""):
                _log(f"Sequence rename: '{tl.name}' → '{current_name}' (node={seq_group})")
                self.sync_manager.broadcast_timeline_rename(tl_guid, current_name)

    def _create_rv_sequence_for_timeline(self, tl):
        """Create an RVSequenceGroup for a remotely-received OTIO timeline.

        Loads any media sources not already present in RV, then creates a new
        ``RVSequenceGroup`` wired in clip order.  Registers the node in
        ``_rv_node_to_timeline_guid`` so subsequent polling and selection events
        resolve correctly.

        This is the client-side counterpart to :meth:`_init_timelines_from_sequences`
        on the master.  It is called when an ``add_timeline`` event arrives from
        a remote peer.

        :param tl: The :class:`~opentimelineio.schema.Timeline` that was just
            registered into the sync manager.
        """
        tl_guid = tl.metadata.get("sync", {}).get("guid") if tl else None
        if not tl_guid:
            _log("_create_rv_sequence_for_timeline: no GUID on timeline")
            return

        # Collect ordered media paths from the timeline's video tracks.
        all_paths = []
        for track in tl.tracks:
            if not self._is_media_track(track):
                continue
            for child in track:
                if not isinstance(child, otio.schema.Clip):
                    continue
                ref = child.media_reference
                if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                    all_paths.append(self._media_path(ref.target_url))

        if not all_paths:
            _log(f"_create_rv_sequence_for_timeline: no media in '{tl.name}'")
            return

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
            media_track = next((t for t in timeline.tracks if self._is_media_track(t)), None)
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
                        result[self._media_path(ref.target_url)] = clip.metadata.get("sync", {}).get("guid")
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
            otio_path_counts = Counter(
                self._media_path(clip.media_reference.target_url)
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
                        _log(f"Add: broadcasting insert_child sg={sg} at index={valid_sgs_before}")
                        self.sync_manager.insert_child(track_guid, clip, valid_sgs_before)
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
                if not self._is_media_track(track):
                    continue
                if not any(c.metadata.get("sync", {}).get("guid") == clip_guid for c in track):
                    continue
                path_to_sg = self._path_to_source_group_map()
                new_inputs = []
                for c in track:
                    ref = c.media_reference
                    if hasattr(ref, "target_url") and ref.target_url:
                        sg = path_to_sg.get(self._media_path(ref.target_url))
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
                if not self._is_media_track(track):
                    continue
                if track.metadata.get("sync", {}).get("guid") != parent_uuid:
                    continue
                path_to_sg = self._path_to_source_group_map()
                new_inputs = []
                for clip in track:
                    ref = clip.media_reference
                    if hasattr(ref, "target_url") and ref.target_url:
                        sg = path_to_sg.get(self._media_path(ref.target_url))
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
                if self._is_media_track(track) and track.metadata.get("sync", {}).get("guid") == parent_uuid:
                    timeline_guid = tl_guid
                    # Rebuild RV sequence inputs from the updated OTIO track order
                    path_to_sg = self._path_to_source_group_map()
                    new_inputs = []
                    for clip in track:
                        ref = clip.media_reference
                        if hasattr(ref, "target_url") and ref.target_url:
                            sg = path_to_sg.get(self._media_path(ref.target_url))
                            if sg:
                                new_inputs.append(sg)
                    if new_inputs:
                        rv.commands.setNodeInputs(seq_group, new_inputs)
                        self._sequence_input_order[seq_group] = new_inputs
                        _log(f"RECV move_child: reordered {seq_group} → {new_inputs}")
                    rv.commands.redraw()
                    return

    def poll_network(self):
        if not self.sync_manager:
            return

        # Re-broadcast WHO_IS_MASTER on every tick during discovery and check
        # for the self-election timeout.
        if self.sync_manager.status == STATE_DISCOVERING:
            self.sync_manager.broadcast_master_discovery()
            if time.time() - self._discovery_start_time > 2.0:
                self._init_as_master()
            # fall through to tick() so I_AM_MASTER responses are processed

        # tick() handles master_found → request_state and
        # state_snapshot_received → apply_snapshot internally.
        # on_synced callback (registered in connect_to_session) rebuilds the RV
        # session when we join an existing master.
        for action, data in self.sync_manager.tick():
            self._rv_updating = True
            try:
                if action == "state_request_received":
                    _log("state_request_received — sending snapshot")
                    try:
                        fps = rv.commands.fps()
                        frame = rv.commands.frame()
                        playing = rv.commands.isPlaying()
                        view = rv.commands.viewNode()
                        tl_guid = (self._rv_node_to_timeline_guid.get(view)
                                   or self.sync_manager.active_timeline_guid)
                        playback_state = {
                            "playing": playing,
                            "current_time": {
                                "OTIO_SCHEMA": "RationalTime.1",
                                "value": float(frame - 1),
                                "rate": float(fps),
                            },
                            "looping": True,
                            "timeline_guid": tl_guid,
                        }
                    except Exception:
                        playback_state = None
                    # Snapshot the current display state so joiners inherit it.
                    self.sync_manager.display_state = self._read_rv_display_state()
                    self._last_display_state = dict(self.sync_manager.display_state)
                    self.sync_manager.send_state_snapshot(data, playback_state=playback_state)
                else:
                    self._handle_action(action, data)
            finally:
                self._rv_updating = False

        if not self._rv_updating:
            self._check_sequence_reorders()
            self._poll_new_sequences()
            self._poll_sequence_renames()
            self._broadcast_display_state()

    def _handle_action(self, action, data):
        """Common dispatcher for sync actions."""
        _log(f"RECV action={action}")
        if action == "playback_settings":
            self._apply_playback(data)
        elif action == "display_settings":
            self._rv_updating = True
            try:
                self._apply_display_state(data)
            finally:
                self._rv_updating = False
        elif action == "selection_changed":
            self._apply_selection(data)
        elif action == "annotation_commands_added":
            # A second (or later) stroke arrived on an already-annotated frame.
            # data is (merged_clip, delta_clip); render only the delta so we
            # don't duplicate strokes that RV already painted.
            _merged_clip, delta_clip = data
            self._apply_annotation_render(delta_clip)
        elif action == "annotation_commands_replaced":
            # Full annotation_commands replacement (e.g. text edit or drag-move).
            # Update existing paint nodes in place instead of adding duplicates.
            self._apply_annotation_replace(data)
        elif action == "partial_annotation":
            self._apply_partial_annotation(data)
        elif action == "insert_child":
            if isinstance(data, otio.schema.Clip) and "annotation_commands" in data.metadata:
                self._apply_annotation_render(data)
            else:
                self._apply_insert_child(data)
        elif action == "remove_child":
            self._apply_remove_child(data)
        elif action == "move_child":
            self._apply_move_child(data)
        elif action == "add_timeline":
            # A new sequence/playlist timeline arrived from a remote peer.
            # Create the corresponding RVSequenceGroup so the user can view it.
            self._rv_updating = True
            try:
                self._create_rv_sequence_for_timeline(data)
            finally:
                self._rv_updating = False
        elif action == "timeline_renamed":
            tl_guid = data.get("timeline_guid")
            new_name = data.get("name", "")
            for seq_group, guid in list(self._rv_node_to_timeline_guid.items()):
                if guid == tl_guid:
                    try:
                        rv.commands.setStringProperty(
                            f"{seq_group}.ui.name", [new_name], True
                        )
                        _log(f"RECV timeline_renamed: '{seq_group}' → '{new_name}'")
                    except Exception as e:
                        _log(f"Could not rename RVSequenceGroup '{seq_group}': {e}")
                    break
        elif action == "state_request_timeout":
            _log("State request timed out. Electing self as master.")
            self._init_as_master()
        else:
            _log(f"RECV unhandled action={action}")

    def _clip_guid_for_media_path(self, media_path):
        """Return the OTIO GUID of the Clip whose ExternalReference matches media_path."""
        for guid, obj in self.sync_manager._object_map.items():
            if isinstance(obj, otio.schema.Clip):
                ref = obj.media_reference
                if isinstance(ref, otio.schema.ExternalReference) and ref.target_url == media_path:
                    return guid
        return None

    def _resolve_media_path_for_paint_node(self, node_name):
        """Return the media file path for an RVPaint node, or None.

        Supports both sequence-context nodes (``{seq}_p_{slot}``) and
        direct-source nodes (``{sg}_paint``).
        """
        if "_p_" in node_name:
            seq_name = node_name.split("_p_")[0]
            display_slot = node_name.split("_p_")[1]
            try:
                for n in rv.commands.nodesInGroup(display_slot):
                    if rv.commands.nodeType(n) == "RVFileSource":
                        path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                        if path:
                            return path
            except Exception:
                pass
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
                    clip_guid = self._clip_guid_for_media_path(media_path)
                    if not clip_guid:
                        continue
                    annotation_track_guid = self.sync_manager.annotation_track_guid_for_clip(clip_guid)
                    if not annotation_track_guid:
                        continue
                    clip = self.sync_manager._object_map.get(clip_guid)
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

        rv_frame = int(frame_val) + 1  # OTIO 0-indexed → RV 1-indexed

        media_clip = self.sync_manager._object_map.get(clip_guid) if clip_guid else None
        if not isinstance(media_clip, otio.schema.Clip):
            return
        ref = media_clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
            return
        media_path = self._media_path(ref.target_url)

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
            node = self._find_paint_node_for_media(media_path, rv_frame)
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
        rv_frame = (int(ann_clip.source_range.start_time.value) + 1
                    if ann_clip.source_range else 1)

        media_clip = self.sync_manager._object_map.get(clip_guid) if clip_guid else None
        if not isinstance(media_clip, otio.schema.Clip):
            _log(f"RECV annotation: no media Clip for guid={clip_guid}")
            return
        ref = media_clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
            _log(f"RECV annotation: clip {clip_guid} has no ExternalReference")
            return
        media_path = self._media_path(ref.target_url)

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
        _paint_node_cache = self._find_paint_node_for_media(media_path, rv_frame)
        for ev in events_data:
            try:
                if isinstance(ev, (dict, collections.abc.Mapping)):
                    ev = otio.adapters.read_from_string(otio.adapters.write_to_string(ev, "otio_json"), "otio_json")
                if isinstance(ev, otio.schemadef.SyncEvent.TextAnnotation):
                    uuid_val = ev.uuid or ""
                    # Snapshot replay sends the full clip as insert_child; if the
                    # node was already painted by _rebuild_rv_session, skip it.
                    if _paint_node_cache and self._text_uuid_exists_in_rv(_paint_node_cache, rv_frame, uuid_val):
                        text_val = ev.text or ""
                        position = list(ev.position) if getattr(ev, "position", None) else [0.0, 0.0]
                        color = list(ev.rgba) if getattr(ev, "rgba", None) else [1.0, 1.0, 1.0, 1.0]
                        rv_size = float(ev.font_size) / 15000.0 if getattr(ev, "font_size", None) else 0.01
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
                            QtCore.QTimer.singleShot(0, rv.commands.redraw)
                            rendered += 1
                        else:
                            _log(f"RECV annotation: skip dup text uuid={uuid_val[:8]!r} (already in RV, but could not update)")
                        continue
                    rv_size = float(ev.font_size) / 15000.0 if getattr(ev, "font_size", None) else 0.01
                    _log(f"RECV TextAnnotation font_size={getattr(ev, 'font_size', None)!r} → rv_size={rv_size!r}")
                    self._apply_text_annotation({
                        "media_path": media_path,
                        "frame": rv_frame,
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
                node = _paint_node_cache or self._find_paint_node_for_media(media_path, rv_frame)
                pen_node = self._partial_pen_nodes.pop(ev_uuid)
                if node and rv.commands.propertyExists(f"{node}.{pen_node}.points"):
                    points_flat = [v for pair in zip(points_event.points.x, points_event.points.y) for v in pair]
                    rv.commands.setFloatProperty(f"{node}.{pen_node}.points", points_flat, True)
                    widths = list(points_event.points.size)
                    if len(widths) == 1:
                        widths = widths * (len(points_flat) // 2)
                    rv.commands.setFloatProperty(f"{node}.{pen_node}.width", widths, True)
                    QtCore.QTimer.singleShot(0, rv.commands.redraw)
                    rendered += 1
                continue
            points_flat = [v for pair in zip(points_event.points.x, points_event.points.y) for v in pair]
            self._apply_annotation({
                "media_path": media_path,
                "frame": rv_frame,
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
        rv_frame = (int(ann_clip.source_range.start_time.value) + 1
                    if ann_clip.source_range else 1)

        media_clip = self.sync_manager._object_map.get(clip_guid) if clip_guid else None
        if not isinstance(media_clip, otio.schema.Clip):
            _log(f"RECV annotation replace: no media Clip for guid={clip_guid}")
            return
        ref = media_clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
            return
        media_path = self._media_path(ref.target_url)

        node = self._find_paint_node_for_media(media_path, rv_frame)
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
            rv_size = float(ev.font_size) / 15000.0 if getattr(ev, "font_size", None) else 0.01

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
                    rv.commands.setStringProperty(f"{node}.{item}.text", [text_val], True)
                    rv.commands.setFloatProperty(f"{node}.{item}.position", position, True)
                    rv.commands.setFloatProperty(f"{node}.{item}.color", color, True)
                    rv.commands.setFloatProperty(f"{node}.{item}.size", [rv_size], True)
                    _log(f"RECV annotation replace: updated {item} text={text_val!r}")
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

        QtCore.QTimer.singleShot(0, rv.commands.redraw)

    @staticmethod
    def _media_path(url: str) -> str:
        """Normalise a ``file://`` URL (any variant) to a canonical POSIX path.

        Delegates to :func:`opentimelineio.url_utils.filepath_from_url` for
        correct handling of percent-encoding and Windows UNC paths, then
        applies an extra pass to collapse the ``//path`` double-slash that
        OTIO returns for the ``file://localhost//path`` form emitted by
        xStudio's flat-playlist exporter.

        Non-``file://`` strings (plain absolute paths, relative paths, URIs
        with other schemes) are returned unchanged.

        :param url: A media URL or path string.
        :returns: A normalised absolute path suitable for use as a dict key
            or as an argument to ``rv.commands.addSource``.
        :rtype: str
        """
        if not url or not url.startswith('file://'):
            return url
        try:
            import opentimelineio.url_utils as _url_utils
            path = _url_utils.filepath_from_url(url)
        except Exception:
            # Fallback: manual parse (handles the common macOS cases).
            from urllib.parse import urlparse, unquote
            path = unquote(urlparse(url).path)
        # OTIO returns '//path' for file://localhost//path — collapse to '/path'.
        while path.startswith('//'):
            path = path[1:]
        return path

    @staticmethod
    def _is_media_track(track) -> bool:
        """Return True if *track* carries source clips (not annotations).

        Matches both the ``"Media"`` name used by RV-originated timelines and
        the ``"Video Track"`` name used by xStudio-originated timelines.
        Audio tracks (``kind != Video``) and the ``"Annotations"`` overlay
        track are explicitly excluded.
        """
        if track.kind != otio.schema.TrackKind.Video:
            return False
        name = track.name or ""
        return not name.startswith("Annotations")

    def _path_to_source_group_map(self):
        """Return {path: source_group_node_name} for all currently loaded RVSourceGroups."""
        mapping = {}
        for sg in rv.commands.nodesOfType("RVSourceGroup"):
            try:
                for n in rv.commands.nodesInGroup(sg):
                    if rv.commands.nodeType(n) == "RVFileSource":
                        path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                        if path:
                            mapping[self._media_path(path)] = sg
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
                if not self._is_media_track(item):
                    continue
                for child in item:
                    if not isinstance(child, otio.schema.Clip):
                        continue
                    ref = child.media_reference
                    if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
                        continue
                    norm = self._media_path(ref.target_url)
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
                    if not self._is_media_track(item):
                        continue
                    for child in item:
                        if not isinstance(child, otio.schema.Clip):
                            continue
                        ref = child.media_reference
                        if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                            sg = path_to_sg.get(self._media_path(ref.target_url))
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
                if item.name and item.name.startswith("Annotations"):
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
                                media_obj = self.sync_manager._object_map.get(clip_guid)
                                if isinstance(media_obj, otio.schema.Clip):
                                    ref = media_obj.media_reference
                                    if isinstance(ref, otio.schema.ExternalReference):
                                        media_path = self._media_path(ref.target_url)
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
                                    rv_size = float(event.font_size) / 15000.0 if getattr(event, "font_size", None) else 0.01
                                    uuid_val = event.uuid or ""
                                    # Guard against duplicates when INSERT_CHILD already painted
                                    # this node before the snapshot arrived.
                                    paint_node = self._find_paint_node_for_media(media_path, frame) if media_path else None
                                    if paint_node and self._text_uuid_exists_in_rv(paint_node, frame, uuid_val):
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
                                    self._apply_text_annotation(text_data)
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
                                self._apply_annotation(data)

        # Set active media track so do_add_clip works on clients too
        active_tl = self.sync_manager._timelines.get(self.sync_manager.active_timeline_guid)
        if active_tl:
            for track in active_tl.tracks:
                if self._is_media_track(track):
                    self._active_media_track_guid = track.metadata.get("sync", {}).get("guid")
                    self._track = track
                    break

        # Restore view to the active timeline
        active_tl_guid = self.sync_manager.active_timeline_guid
        if active_tl_guid:
            for rv_node, tl_guid in self._rv_node_to_timeline_guid.items():
                if tl_guid == active_tl_guid:
                    try:
                        if rv.commands.viewNode() != rv_node:
                            _log(f"Rebuild restoring active view to '{rv_node}' for timeline GUID '{active_tl_guid[:8]}'")
                            self._rv_updating = True
                            try:
                                rv.commands.setViewNode(rv_node)
                            finally:
                                self._rv_updating = False
                    except Exception as e:
                        _log(f"Failed to restore view to '{rv_node}': {e}")
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
        # Timeline switch: view node is a sequence group.
        tl_guid = self._rv_node_to_timeline_guid.get(view)
        if tl_guid and tl_guid != self.sync_manager.active_timeline_guid:
            self.sync_manager.active_timeline_guid = tl_guid
            _log(f"SEND view_change view={view} tl={tl_guid}")
            self._broadcast_playback()
        # Clip selection: user double-clicked into a source group (source view).
        # Map source group → media path → OTIO clip GUID and broadcast.
        if rv.commands.nodeType(view) == "RVSourceGroup":
            sg_to_path = {v: k for k, v in self._path_to_source_group_map().items()}
            media_path = sg_to_path.get(view)
            if media_path:
                clip_guid = self._clip_guid_for_media_path(media_path)
                if clip_guid and clip_guid != self._last_broadcast_clip_guid:
                    _clip_obj = self.sync_manager._object_map.get(clip_guid)
                    _clip_label = getattr(_clip_obj, "name", None) or clip_guid[:8]
                    _log(f"SEND selection [view-change]: clip '{_clip_label}' guid={clip_guid[:8]} view={view}")
                    is_new = clip_guid not in self.sync_manager._clip_timelines
                    clip_tl_guid = self.sync_manager.get_or_create_clip_timeline(clip_guid)
                    if clip_tl_guid:
                        if is_new:
                            self.sync_manager.broadcast_clip_timeline(clip_tl_guid)
                        self.sync_manager.active_timeline_guid = clip_tl_guid
                    self.sync_manager.broadcast_selection(clip_guid)
                    self._last_broadcast_clip_guid = clip_guid
        elif view in self._rv_node_to_timeline_guid and self._last_broadcast_clip_guid:
            # Returned to sequence/timeline view — restore sequence active_timeline_guid
            # and broadcast clear so peers exit single-clip mode.
            _tl_guid = self._rv_node_to_timeline_guid.get(view)
            _tl = self.sync_manager.timelines.get(_tl_guid) if _tl_guid else None
            _tl_name = getattr(_tl, "name", None) or view
            _log(f"SEND selection [view-change]: clear → sequence '{_tl_name}' (view={view})")
            seq_tl_guid = self._rv_node_to_timeline_guid.get(view)
            if seq_tl_guid:
                self.sync_manager.active_timeline_guid = seq_tl_guid
            self.sync_manager.broadcast_selection("", view_mode="sequence")
            self._last_broadcast_clip_guid = None
        event.reject()

    def on_rv_play_start(self, event):
        self._broadcast_playback()
        event.reject()

    def on_rv_play_stop(self, event):
        self._broadcast_playback()
        event.reject()

    def on_rv_pen_up(self, event):
        """Pointer release / leave — flush any in-progress stroke immediately."""
        self._on_pen_up()
        event.reject()

    def on_rv_frame_changed(self, event):
        if self._rv_updating: event.reject(); return
        current_frame = rv.commands.frame()
        if not rv.commands.isPlaying() and current_frame != self._last_broadcast_frame:
            self._broadcast_playback()
            self._last_broadcast_frame = current_frame
        event.reject()

    def on_rv_selection_changed(self, event):
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED:
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
        sg_to_path = {v: k for k, v in self._path_to_source_group_map().items()}
        for node in selection:
            media_path = sg_to_path.get(node)
            if media_path:
                clip_guid = self._clip_guid_for_media_path(media_path)
                if clip_guid:
                    _clip_obj = self.sync_manager._object_map.get(clip_guid)
                    _clip_label = getattr(_clip_obj, "name", None) or clip_guid[:8]
                    _log(f"SEND selection [selection-change]: clip '{_clip_label}' guid={clip_guid[:8]} node={node}")
                    self.sync_manager.broadcast_selection(clip_guid)
                    break
        event.reject()

    def on_rv_graph_state_change(self, event):
        contents = event.contents()
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        # Channel change: RVDisplayColor.color.channelFlood written by r/g/b/a keys.
        # Broadcast immediately rather than waiting for the next poll tick.
        if "channelFlood" in contents:
            self._broadcast_display_state()
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

        # Pen point or text change: node.pen:N:F:user.points / node.text:N:F:user.text
        is_pen = ".pen:" in contents and contents.endswith(".points")
        is_text = ".text:" in contents and contents.endswith(".text")
        if is_pen or is_text:
            parts = contents.split(".")
            if len(parts) == 3:
                node_name, component = parts[0], parts[1]
                _log(f"annotation updated: {node_name}.{component}")
                # Consume the UUID prepared by paint.nextId (or fall back for
                # text strokes which don't trigger nextId).
                stroke_uuid = self._next_stroke_uuid or str(uuid.uuid4())
                self._next_stroke_uuid = None
                self._pending_stroke = (node_name, component, stroke_uuid)
                # Repeating partial broadcast (50 ms) — fires while user is drawing.
                if self._stroke_timer is None:
                    self._stroke_timer = QtCore.QTimer()
                    self._stroke_timer.timeout.connect(self._send_partial_stroke)
                if not self._stroke_timer.isActive():
                    self._stroke_timer.start(50)
        event.reject()

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

    def _broadcast_annotation(self, node_name, component, partial=False, stroke_uuid=None):
        _log(f"SEND annotation node={node_name} component={component} partial={partial}")
        try:
            full_prop = f"{node_name}.{component}"
            is_text = component.startswith("text:")
            events = []
            
            if is_text:
                text_prop = f"{full_prop}.text"
                if not rv.commands.propertyExists(text_prop):
                    _log(f"SEND annotation skipped: no text property on {full_prop}")
                    return
                text = rv.commands.getStringProperty(text_prop)
                text_val = text[0] if text else ""
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

                # Frame number is part of the component name: text:N:F:user
                parts = component.split(":")
                frame = int(parts[2])

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
                        friendly_name=font[0] if font else "",
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
                    return
            else:
                points = rv.commands.getFloatProperty(f"{full_prop}.points")
                if not points:
                    _log(f"SEND annotation skipped: no points on {full_prop}")
                    return
                color = rv.commands.getFloatProperty(f"{full_prop}.color")
                brush = rv.commands.getStringProperty(f"{full_prop}.brush")[0]
                width = rv.commands.getFloatProperty(f"{full_prop}.width")
                join = rv.commands.getIntProperty(f"{full_prop}.join")[0]
                cap = rv.commands.getIntProperty(f"{full_prop}.cap")[0]
                frame = int(component.split(":")[2])

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
                    return

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

            clip_guid = self._clip_guid_for_media_path(media_path)
            if not clip_guid:
                _log(f"SEND annotation skipped: no clip_guid for media_path={media_path}")
                return
            annotation_track_guid = self.sync_manager.annotation_track_guid_for_clip(
                clip_guid,
                preferred_timeline_guid=self.sync_manager.active_timeline_guid,
            )
            if not annotation_track_guid:
                _log(f"SEND annotation skipped: no annotation track for clip {clip_guid}")
                return

            fps = rv.commands.fps()
            # RV frames are 1-indexed; OTIO clip-local time is 0-indexed
            otio_frame = frame - 1 if frame > 0 else 0
            if partial:
                self.sync_manager.broadcast_partial_annotation(
                    clip_guid=clip_guid,
                    frame=float(otio_frame),
                    fps=float(fps),
                    events=events,
                )
            else:
                clip_local_time = otio.opentime.RationalTime(otio_frame, fps)
                self.sync_manager.broadcast_add_annotation(
                    annotation_track_guid=annotation_track_guid,
                    clip_guid=clip_guid,
                    clip_local_time=clip_local_time,
                    events=events,
                )
        except Exception as e:
            _log_exc(f"Failed to broadcast annotation: {e}")

    def _rv_display_color_nodes(self):
        """Return all active-viewer RVDisplayColor nodes (one per display pane).

        Excludes ``defaultOutputGroup*`` which is the export/output pipeline and
        is NOT modified by the r/g/b/a channel-isolation keys.  RV creates one
        ``displayGroup*_colorPipeline_0`` node per layout pane; pressing r/g/b/a
        only changes the *focused* pane, so we must read and write all of them.
        """
        all_nodes = rv.commands.nodesOfType("RVDisplayColor")
        if not all_nodes:
            return []
        if not getattr(self, "_display_color_nodes_logged", False):
            self._display_color_nodes_logged = True
            _log(f"RVDisplayColor nodes: {all_nodes}")
        active = [n for n in all_nodes if "defaultoutput" not in n.lower()]
        return active if active else all_nodes

    def _rv_display_color_node(self):
        """Return the first active-viewer RVDisplayColor node, or None."""
        nodes = self._rv_display_color_nodes()
        return nodes[0] if nodes else None

    # channelFlood encoding from rvui.mu showChannel(): 0=RGBA, 1=R, 2=G, 3=B, 4=A, 5=Luma
    _RV_FLOOD_TO_CH = {0: "RGBA", 1: "R", 2: "G", 3: "B", 4: "A"}
    _RV_CH_TO_FLOOD = {"RGBA": 0, "R": 1, "G": 2, "B": 3, "A": 4}

    def _rv_color_node_for_current_source(self):
        """Return the RVColor node for the currently visible source, or None.

        ``rv.commands.sourcesAtFrame`` returns a list of source node names of
        the form ``sourceGroupNNNNNN_source``.  The corresponding RVColor pipeline
        node is ``sourceGroupNNNNNN_colorPipeline_0``.
        """
        try:
            sources = rv.commands.sourcesAtFrame(rv.commands.frame())
            if sources:
                src = sources[0]
                if src.endswith("_source"):
                    return src[:-len("_source")] + "_colorPipeline_0"
        except Exception:
            pass
        nodes = rv.commands.nodesOfType("RVColor")
        return nodes[0] if nodes else None

    def _read_rv_display_state(self):
        """Return a dict with pan, zoom, exposure and channel for the current session.

        Pan/zoom come from ``rv.extra_commands.translation()`` / ``.scale()``.
        Exposure comes from the ``RVColor.color.exposure`` node for the
        *currently visible* source (the ``e`` key; 3-element RGB array, channel
        0 used as scalar).  Channel comes from ``RVDisplayColor.color.channelFlood``
        (``r``/``g``/``b``/``a`` keys; 0=RGBA 1=R 2=G 3=B 4=A).
        """
        state = {
            "pan": [0.0, 0.0],
            "zoom": 1.0,
            "exposure": 0.0,
            "channel": "RGBA",
        }
        # Pan and zoom via rv.extra_commands (viewer-level, not a node property).
        try:
            t = rv.extra_commands.translation()
            state["pan"] = [float(t[0]), float(t[1])]
        except Exception as e:
            _log(f"WARN _read_rv_display_state translation: {e}")
        try:
            state["zoom"] = float(rv.extra_commands.scale())
        except Exception as e:
            _log(f"WARN _read_rv_display_state scale: {e}")
        # Exposure — current source's RVColor node (e key).
        try:
            node = self._rv_color_node_for_current_source()
            if node:
                exp = rv.commands.getFloatProperty(f"{node}.color.exposure")
                state["exposure"] = float(exp[0]) if exp else 0.0
        except Exception as e:
            _log(f"WARN _read_rv_display_state exposure: {e}")
        # Channel — scan ALL displayGroup RVDisplayColor nodes.
        # Pressing r/g/b/a only changes the focused pane's node; if that pane is
        # not displayGroup0 we'd miss the change reading just one node.  Scan all
        # of them: if any deviates from the last known channel, use that value so
        # the change is detected and broadcast.
        dc_nodes = self._rv_display_color_nodes()
        if dc_nodes:
            last_flood = self._RV_CH_TO_FLOOD.get(
                self._last_display_state.get("channel", "RGBA"), 0)
            floods = []
            for n in dc_nodes:
                try:
                    f = rv.commands.getIntProperty(f"{n}.color.channelFlood")
                    floods.append(f[0] if f else 0)
                except Exception as e:
                    _log(f"WARN _read_rv_display_state channelFlood ({n}): {e}")
            if floods:
                # Prefer any pane that differs from the last broadcast state
                # (that's the pane the user just changed).
                changed = [f for f in floods if f != last_flood]
                state["channel"] = self._RV_FLOOD_TO_CH.get(
                    changed[0] if changed else floods[0], "RGBA")
        return state

    def _broadcast_display_state(self):
        """Read the current RV display state and broadcast it if it has changed.

        When exposure changes, all per-source ``RVColor`` nodes are normalised
        to the new value before broadcasting.  This ensures that navigating
        between clips (which may have had different per-clip exposures set
        before the sync was active) does not trigger spurious re-broadcasts on
        the next frame.
        """
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED:
            return
        state = self._read_rv_display_state()
        if state == self._last_display_state:
            return
        prev = self._last_display_state
        self._last_display_state = state
        # Guard the normalisation writes with _rv_updating so that the
        # synchronous graph-state-change events they fire are suppressed by
        # on_rv_graph_state_change.  Without this, each write re-enters
        # _broadcast_display_state while the other panes are still mid-update,
        # causing the "changed" detection to misread a partially-normalised
        # state and broadcast the wrong channel back.
        self._rv_updating = True
        try:
            # Normalise all source nodes to the new exposure so that navigating
            # between clips does not trigger false change detections next tick.
            if state["exposure"] != prev.get("exposure"):
                ev = float(state["exposure"])
                try:
                    for node in rv.commands.nodesOfType("RVColor"):
                        rv.commands.setFloatProperty(
                            f"{node}.color.exposure", [ev, ev, ev], True)
                except Exception:
                    pass
            # Normalise all display panes to the new channel so that subsequent
            # reads from any pane agree and don't re-trigger a broadcast.
            if state["channel"] != prev.get("channel"):
                flood = self._RV_CH_TO_FLOOD.get(state["channel"], 0)
                for dc in self._rv_display_color_nodes():
                    try:
                        rv.commands.setIntProperty(
                            f"{dc}.color.channelFlood", [flood], True)
                    except Exception:
                        pass
        finally:
            self._rv_updating = False
        _log(f"SEND display zoom={state['zoom']:.3f} pan={state['pan']} "
             f"exposure={state['exposure']:.3f} channel={state['channel']}")
        self.sync_manager.broadcast_display_state(state)

    def _apply_display_state(self, data):
        """Apply an incoming display state dict to the local RV session.

        Pan/zoom are applied via ``rv.extra_commands`` only when the incoming
        values are non-None.  A ``None`` value means the sender does not support
        pan/zoom (e.g. xStudio) and the local values should be left unchanged.
        Exposure is written to **all** ``RVColor`` source nodes (3-element RGB)
        so every clip matches.  Channel is written to
        ``RVDisplayColor.color.channelFlood``.
        """
        pan = data.get("pan")
        zoom = data.get("zoom")
        exposure = data.get("exposure", 0.0)
        channel = data.get("channel", "RGBA")
        _log(f"RECV display pan={pan} zoom={zoom} "
             f"exposure={exposure:.3f} channel={channel}")

        if pan is not None:
            try:
                rv.extra_commands.setTranslation((float(pan[0]), float(pan[1])))
            except Exception as e:
                _log(f"RECV display: pan set failed: {e}")
        if zoom is not None:
            try:
                rv.extra_commands.setScale(float(zoom))
            except Exception as e:
                _log(f"RECV display: zoom set failed: {e}")

        # Apply exposure to every source node so all clips match.
        try:
            ev = float(exposure)
            for node in rv.commands.nodesOfType("RVColor"):
                rv.commands.setFloatProperty(
                    f"{node}.color.exposure", [ev, ev, ev], True)
        except Exception as e:
            _log(f"RECV display: exposure set failed: {e}")

        flood = self._RV_CH_TO_FLOOD.get(channel, 0)
        for dc in self._rv_display_color_nodes():
            try:
                rv.commands.setIntProperty(f"{dc}.color.channelFlood",
                                           [flood], True)
            except Exception as e:
                _log(f"RECV display: channel set failed ({dc}): {e}")

        # Keep _last_display_state consistent with what we actually hold.
        # If the sender omitted pan/zoom, preserve our current read-back values
        # so the next broadcast comparison doesn't spuriously see a change.
        cur = self._read_rv_display_state()
        self._last_display_state = {
            "pan": [float(pan[0]), float(pan[1])] if pan is not None else cur["pan"],
            "zoom": float(zoom) if zoom is not None else cur["zoom"],
            "exposure": exposure,
            "channel": channel,
        }
        rv.commands.redraw()

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
        known_tl_guids = set(self._rv_node_to_timeline_guid.values())
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
                for rv_node, tl_guid in self._rv_node_to_timeline_guid.items():
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
                (n for n in self._rv_node_to_timeline_guid
                 if rv.commands.nodeType(n) != "RVSourceGroup"),
                None
            )
            if seq_node:
                seq_tl_guid = self._rv_node_to_timeline_guid.get(seq_node)
                if seq_tl_guid:
                    self.sync_manager.active_timeline_guid = seq_tl_guid
                self._rv_updating = True
                try:
                    rv.commands.setViewNode(seq_node)
                finally:
                    self._rv_updating = False
            return

        # Find the media path for this GUID then look up the local source group.
        clip = self.sync_manager._object_map.get(clip_guid) if self.sync_manager else None
        if clip is None or not isinstance(clip, otio.schema.Clip):
            _log(f"RECV selection: clip_guid={clip_guid} not found in object_map")
            return
        ref = clip.media_reference
        if not isinstance(ref, otio.schema.ExternalReference):
            return
        media_path = self._media_path(ref.target_url)
        source_group = self._path_to_source_group_map().get(media_path)
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
            target_tl_guid = None
            for tl_guid_iter, tl in self.sync_manager.timelines.items():
                found = False
                for track in tl.tracks:
                    if track.kind != otio.schema.TrackKind.Video:
                        continue
                    elapsed = 0
                    for child in track:
                        if child.metadata.get("sync", {}).get("guid") == clip_guid:
                            start_frame = elapsed + 1  # RV frames are 1-indexed
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
                for rv_node, tl_guid_map in self._rv_node_to_timeline_guid.items():
                    if (tl_guid_map == target_tl_guid
                            and rv.commands.nodeType(rv_node) != "RVSourceGroup"):
                        seq_node = rv_node
                        break
            if seq_node is None:
                # Fallback: first non-source-group node (single-sequence sessions).
                seq_node = next(
                    (n for n in self._rv_node_to_timeline_guid
                     if rv.commands.nodeType(n) != "RVSourceGroup"),
                    None
                )

            _log(
                f"RECV selection seq: seq_node={seq_node} start_frame={start_frame}"
                f" target_tl={target_tl_guid[:8] if target_tl_guid else None}"
            )
            if seq_node:
                seq_tl_guid = self._rv_node_to_timeline_guid.get(seq_node)
                if seq_tl_guid:
                    self.sync_manager.active_timeline_guid = seq_tl_guid
                self._last_broadcast_clip_guid = clip_guid
                self._sequence_selection_applied_at = time.monotonic()
                self._rv_updating = True
                try:
                    rv.commands.setViewNode(seq_node)
                    rv.commands.setFrame(start_frame)
                    _log(f"RECV selection seq: applied setViewNode={seq_node} setFrame={start_frame}")
                except Exception as e:
                    _log(f"RECV selection seq: error applying setViewNode/setFrame: {e}")
                finally:
                    self._rv_updating = False
            else:
                _log("RECV selection seq: no seq_node found — cannot seek")
            return

        # source mode: switch active_timeline_guid to the clip's own timeline.
        clip_tl_guid = self.sync_manager.get_or_create_clip_timeline(clip_guid)
        if clip_tl_guid:
            self.sync_manager.active_timeline_guid = clip_tl_guid

        # Set echo guard before setViewNode so after-graph-view-change doesn't
        # re-broadcast the remote-applied selection.
        self._last_broadcast_clip_guid = clip_guid
        self._rv_updating = True
        try:
            rv.commands.setViewNode(source_group)
            rv.commands.setFrame(1)  # jump to first frame of this source
        finally:
            self._rv_updating = False

    def _find_paint_node_for_media(self, media_path, frame):
        """Find the local RVPaint node for a given media path and frame.

        Must use metaEvaluateClosestByType to get the sequence-level paint node
        (e.g. defaultSequence_p_sourceGroup000000) rather than the source-level
        node found inside the source group (e.g. sourceGroup000000_paint).  The
        source-level node is invisible in sequence view, so strokes written there
        never appear when the user is watching a sequence.
        """
        # frame is source-local (1-indexed).
        # We need to map it to a sequence frame (1-indexed) if a sequence view is active.
        seq_frame = frame
        if self.sync_manager:
            clip_guid = self._clip_guid_for_media_path(media_path)
            if clip_guid:
                clip = self.sync_manager._object_map.get(clip_guid)
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
        sg = self._path_to_source_group_map().get(media_path)
        if sg:
            for n in rv.commands.nodesInGroup(sg):
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
            _log(f"RECV annotation frame={frame} brush={brush} node={node_name} npts={len(points) // 2 if points else 0}")
            node = self._find_paint_node_for_media(media_path, frame)
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

            _log(f"RECV text annotation frame={frame} text={text} uuid={uuid_val}")
            node = self._find_paint_node_for_media(media_path, frame)
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
            QtCore.QTimer.singleShot(0, rv.commands.redraw)
        except Exception as e:
            _log_exc(f"Failed to apply remote text annotation: {e}")

    def _apply_insert(self, clip_obj):
        ref = clip_obj.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            rv.commands.addSource(self._media_path(ref.target_url))

    def _session_dialog(self, title):
        """Show a two-field dialog for MQ Host and Session Name.

        :param title: Dialog window title (e.g. "Create Session").
        :returns: ``(host, name)`` or ``(None, None)`` on cancel.
        :rtype: tuple
        """
        try:
            from PySide2.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QLabel
        except ImportError:
            try:
                from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QLabel
            except ImportError:
                _log("PySide not available — cannot show session dialog")
                return None, None

        default_host = os.environ.get("ORI_RMQ_HOST", "127.0.0.1")
        dlg = QDialog()
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(360)
        layout = QFormLayout(dlg)
        host_edit = QLineEdit(default_host)
        name_edit = QLineEdit()
        layout.addRow(QLabel("MQ Host:"), host_edit)
        layout.addRow(QLabel("Session Name:"), name_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        host_edit.returnPressed.connect(name_edit.setFocus)
        name_edit.returnPressed.connect(dlg.accept)
        layout.addRow(buttons)
        if dlg.exec_() != QDialog.Accepted:
            return None, None
        host = host_edit.text().strip() or default_host
        name = name_edit.text().strip()
        if not name:
            return None, None
        return host, name

    def do_create_session(self, event=None):
        """Prompt for host/name and create a new session (with master-check warning)."""
        if self._in_session:
            _show_warning(
                f"Already connected to '{self._current_session_name}'. "
                "Leave the current session first."
            )
            if event: event.reject()
            return
        host, name = self._session_dialog("Create Session")
        if name:
            self._pending_create_check = True
            self.connect_to_session(host, name)
        if event: event.reject()

    def do_join_session(self, event=None):
        """Prompt for host/name and join an existing session."""
        if self._in_session:
            _show_warning(
                f"Already connected to '{self._current_session_name}'. "
                "Leave the current session first."
            )
            if event: event.reject()
            return
        host, name = self._session_dialog("Join Session")
        if name:
            self.connect_to_session(host, name)
        if event: event.reject()

    def do_leave_session(self, event=None):
        """Disconnect from the active session."""
        self.disconnect_from_session()
        if event: event.reject()

    def do_add_clip(self, event=None):
        if not self.sync_manager:
            if event: event.reject()
            return
        paths = rv.commands.openFileDialog(False, False, False, "mp4|Movie Files|mov|Movie Files|m4v|Movie Files|mkv|Movie Files|avi|Movie Files", "")
        if not paths:
            if event: event.reject()
            return
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
        self.disconnect_from_session()
        rv.rvtypes.MinorMode.deactivate(self)

def createMode():
    return OpenRVSyncPlugin()
