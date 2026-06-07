import rv.commands
import rv.extra_commands
import rv.rvtypes
import os
import time

from utils import _log, _show_warning, _parse_ori_session, _media_path, _is_media_track

try:
    from otio_sync_core import SyncManager, RabbitMQNetwork
    from otio_sync_core.manager import STATE_DISCOVERING, STATE_SYNCED
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

# Import controllers
from sequence_sync import SequenceSyncController
from playback_sync import PlaybackSyncController
from display_sync import DisplaySyncController
from annotation_sync import AnnotationSyncController

SYNC_DEMO_TRACK_UUID = "otio-sync-demo-track-0"


class OpenRVSyncPlugin(rv.rvtypes.MinorMode):
    #: Mode name passed to init() and used as the key in defineModeMenu().
    MENU_NAME = "openrv_sync_plugin"
    #: Display title for the top-level menu entry.
    MENU_TITLE = "OTIO Sync"

    _media_path = staticmethod(_media_path)
    _is_media_track = staticmethod(_is_media_track)

    def __init__(self):
        rv.rvtypes.MinorMode.__init__(self)

        self.sync_manager = None
        self._rv_updating = False
        self._timer = None
        self._current_session_name = None
        self._current_host = None
        self._pending_create_check = False
        self._discovery_start_time = 0

        # Instantiate controllers
        self.sequence = SequenceSyncController(self)
        self.playback = PlaybackSyncController(self)
        self.display = DisplaySyncController(self)
        self.annotation = AnnotationSyncController(self)

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

    # Property Descriptors for compatibility/cross-controller accesses
    @property
    def _rv_node_to_timeline_guid(self):
        return self.sequence._rv_node_to_timeline_guid
    @_rv_node_to_timeline_guid.setter
    def _rv_node_to_timeline_guid(self, value):
        self.sequence._rv_node_to_timeline_guid = value

    @property
    def _sequence_input_order(self):
        return self.sequence._sequence_input_order
    @_sequence_input_order.setter
    def _sequence_input_order(self, value):
        self.sequence._sequence_input_order = value

    @property
    def _sg_to_path_cache(self):
        return self.sequence._sg_to_path_cache
    @_sg_to_path_cache.setter
    def _sg_to_path_cache(self, value):
        self.sequence._sg_to_path_cache = value

    @property
    def _sequence_settle_until(self):
        return self.sequence._sequence_settle_until
    @_sequence_settle_until.setter
    def _sequence_settle_until(self, value):
        self.sequence._sequence_settle_until = value

    @property
    def _active_media_track_guid(self):
        return self.sequence._active_media_track_guid
    @_active_media_track_guid.setter
    def _active_media_track_guid(self, value):
        self.sequence._active_media_track_guid = value

    @property
    def _track(self):
        return self.sequence._track
    @_track.setter
    def _track(self, value):
        self.sequence._track = value

    @property
    def _last_broadcast_frame(self):
        return self.playback._last_broadcast_frame
    @_last_broadcast_frame.setter
    def _last_broadcast_frame(self, value):
        self.playback._last_broadcast_frame = value

    @property
    def _last_selection(self):
        return self.playback._last_selection
    @_last_selection.setter
    def _last_selection(self, value):
        self.playback._last_selection = value

    @property
    def _last_broadcast_clip_guid(self):
        return self.playback._last_broadcast_clip_guid
    @_last_broadcast_clip_guid.setter
    def _last_broadcast_clip_guid(self, value):
        self.playback._last_broadcast_clip_guid = value

    @property
    def _sequence_selection_applied_at(self):
        return self.playback._sequence_selection_applied_at
    @_sequence_selection_applied_at.setter
    def _sequence_selection_applied_at(self, value):
        self.playback._sequence_selection_applied_at = value

    @property
    def _last_display_state(self):
        return self.display._last_display_state
    @_last_display_state.setter
    def _last_display_state(self, value):
        self.display._last_display_state = value

    @property
    def _pending_stroke(self):
        return self.annotation._pending_stroke
    @_pending_stroke.setter
    def _pending_stroke(self, value):
        self.annotation._pending_stroke = value

    @property
    def _next_stroke_uuid(self):
        return self.annotation._next_stroke_uuid
    @_next_stroke_uuid.setter
    def _next_stroke_uuid(self, value):
        self.annotation._next_stroke_uuid = value

    @property
    def _stroke_timer(self):
        return self.annotation._stroke_timer
    @_stroke_timer.setter
    def _stroke_timer(self, value):
        self.annotation._stroke_timer = value

    @property
    def _last_partial_point_count(self):
        return self.annotation._last_partial_point_count
    @_last_partial_point_count.setter
    def _last_partial_point_count(self, value):
        self.annotation._last_partial_point_count = value

    @property
    def _partial_pen_nodes(self):
        return self.annotation._partial_pen_nodes
    @_partial_pen_nodes.setter
    def _partial_pen_nodes(self, value):
        self.annotation._partial_pen_nodes = value

    @property
    def _last_sent_replace_sig(self):
        return self.annotation._last_sent_replace_sig
    @_last_sent_replace_sig.setter
    def _last_sent_replace_sig(self, value):
        self.annotation._last_sent_replace_sig = value

    @property
    def _ignore_annotations_until(self):
        return self.annotation._ignore_annotations_until
    @_ignore_annotations_until.setter
    def _ignore_annotations_until(self, value):
        self.annotation._ignore_annotations_until = value

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
        """Create a SyncManager and join the named session."""
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
                        if _media_path(ref.target_url) not in self.sequence._path_to_source_group_map():
                            rv.commands.addSource(_media_path(ref.target_url))

        @self.sync_manager.on_synced
        def _on_synced():
            if not self.sync_manager.is_master:
                self._rv_updating = True
                try:
                    self.sequence.rebuild_rv_session()
                    if self.sync_manager.playback_state:
                        self.playback._apply_playback(self.sync_manager.playback_state)
                    if self.sync_manager.display_state:
                        self.display._apply_display_state(self.sync_manager.display_state)
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
        self.sequence._sg_to_path_cache.clear()
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
            self.sequence._init_timelines_from_sequences(seq_groups, fps)
            total_clips = sum(
                len(list(tr))
                for tl in self.sync_manager._timelines.values()
                for tr in tl.tracks
            )
            if total_clips == 0:
                _log("No clips found on init — scheduling graph-settled retry")
                QtCore.QTimer.singleShot(500, self.sequence._retry_init_timelines)
        else:
            self.sequence._init_single_timeline(fps)

        self.sync_manager.broadcast_master_response()
        self.annotation._import_existing_rv_annotations()
        _log("Session Initialized as MASTER")

    def poll_network(self):
        if not self.sync_manager:
            return

        # Re-broadcast WHO_IS_MASTER on every tick during discovery and check
        # for the self-election timeout.
        if self.sync_manager.status == STATE_DISCOVERING:
            self.sync_manager.broadcast_master_discovery()
            if time.time() - self._discovery_start_time > 2.0:
                self._init_as_master()

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
                        tl_guid = (self.sequence._rv_node_to_timeline_guid.get(view)
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
                    self.sync_manager.display_state = self.display._read_rv_display_state()
                    self.display._last_display_state = dict(self.sync_manager.display_state)
                    self.sync_manager.send_state_snapshot(data, playback_state=playback_state)
                else:
                    self._handle_action(action, data)
            finally:
                self._rv_updating = False

        if not self._rv_updating:
            try:
                self.sequence._check_sequence_reorders()
            except Exception as e:
                import traceback
                _log(f"ERROR in _check_sequence_reorders: {e}\n{traceback.format_exc()}")
            self.sequence._poll_new_sequences()
            self.sequence._poll_sequence_renames()
            self.display._broadcast_display_state()

    def _handle_action(self, action, data):
        """Common dispatcher for sync actions."""
        _log(f"RECV action={action}")
        if action == "playback_settings":
            self.playback._apply_playback(data)
        elif action == "display_settings":
            self._rv_updating = True
            try:
                self.display._apply_display_state(data)
            finally:
                self._rv_updating = False
        elif action == "selection_changed":
            self.playback._apply_selection(data)
        elif action == "annotation_commands_added":
            _merged_clip, delta_clip = data
            self._rv_updating = True
            try:
                self.annotation._apply_annotation_render(delta_clip)
            finally:
                self._rv_updating = False
        elif action == "annotation_commands_replaced":
            self.annotation._ignore_annotations_until = time.time() + 0.5
            self._rv_updating = True
            try:
                self.annotation._apply_annotation_replace(data)
            finally:
                self._rv_updating = False
        elif action == "partial_annotation":
            self.annotation._ignore_annotations_until = time.time() + 0.5
            self.annotation._apply_partial_annotation(data)
        elif action == "insert_child":
            if isinstance(data, otio.schema.Clip) and "annotation_commands" in data.metadata:
                self.annotation._ignore_annotations_until = time.time() + 0.5
                self._rv_updating = True
                try:
                    self.annotation._apply_annotation_render(data)
                finally:
                    self._rv_updating = False
            else:
                self.sequence._apply_insert_child(data)
        elif action == "remove_child":
            self.sequence._apply_remove_child(data)
        elif action == "move_child":
            self.sequence._apply_move_child(data)
        elif action == "add_timeline":
            self._rv_updating = True
            try:
                self.sequence._create_rv_sequence_for_timeline(data)
            finally:
                self._rv_updating = False
        elif action == "timeline_renamed":
            tl_guid = data.get("timeline_guid")
            new_name = data.get("name", "")
            for seq_group, guid in list(self.sequence._rv_node_to_timeline_guid.items()):
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

    def on_rv_view_changed(self, event):
        self.playback.on_view_changed(event)

    def on_rv_play_start(self, event):
        self.playback._broadcast_playback()
        event.reject()

    def on_rv_play_stop(self, event):
        self.playback._broadcast_playback()
        event.reject()

    def on_rv_pen_up(self, event):
        """Pointer release / leave — flush any in-progress stroke immediately."""
        self.annotation._on_pen_up()
        event.reject()

    def on_rv_frame_changed(self, event):
        if self._rv_updating:
            event.reject()
            return
        current_frame = rv.commands.frame()
        if not rv.commands.isPlaying() and current_frame != self.playback._last_broadcast_frame:
            self.playback._broadcast_playback()
            self.playback._last_broadcast_frame = current_frame
        event.reject()

    def on_rv_selection_changed(self, event):
        self.playback.on_selection_changed(event)

    def on_rv_graph_state_change(self, event):
        self.annotation.on_graph_state_change(event)

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
