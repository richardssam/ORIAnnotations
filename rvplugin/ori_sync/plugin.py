import rv.commands
import rv.extra_commands
import rv.rvtypes
import contextlib
import os
import sys
import time

from utils import (_log, _show_warning, _parse_ori_session, _media_path,
                   _is_media_track, session_dialog)

#: Set to the ImportError text when the sync core is unavailable. A swallowed
#: import error is indistinguishable from a working plugin — this is what the
#: vendored-pika packaging incident looked like from the outside — so the
#: failure is both recorded here (to gate the menu) and written to stderr.
_SYNC_IMPORT_ERROR = None

try:
    from otio_sync_core import SyncManager, RabbitMQNetwork
    from otio_sync_core.manager import STATE_DISCOVERING
    from otio_sync_core.protocol_messages import timeline_origin, ORIGIN_OTIO_IMPORT
    from otio_sync_core.rabbitmq_network import resolve_host
    import opentimelineio as otio
except ImportError as e:
    SyncManager = None
    RabbitMQNetwork = None
    resolve_host = None
    _SYNC_IMPORT_ERROR = str(e)
    _log(f"Import error: {e}")
    # _log is a no-op unless ORI_SYNC_LOG_FILE/DEBUG_OTIO_SYNC is set, so
    # stderr is the only place this is reliably visible.
    print(f"[OTIOSync] sync core unavailable — import failed: {e}", file=sys.stderr)

    def timeline_origin(_tl):
        return "native"
    ORIGIN_OTIO_IMPORT = "otio_import"

try:
    from PySide2 import QtCore
except ImportError:
    try:
        from PySide6 import QtCore
    except ImportError:
        QtCore = None

# Import controllers
from sequence_sync import SequenceSyncController
from playback_sync import PlaybackSyncController, _PLAY_MODE_TO_WIRE
from display_sync import DisplaySyncController
from annotation_sync import AnnotationSyncController
from color_sync import ColorSyncController

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
        self._updating_depth = 0
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
        self.color = ColorSyncController(self)

        self.init(self.MENU_NAME, [
            ("play-start", self.on_rv_play_start, "Broadcast Play"),
            ("play-stop", self.on_rv_play_stop, "Broadcast Stop"),
            ("play-mode-changed", self.on_rv_play_mode_changed, "Broadcast Play Mode"),
            ("frame-changed", self.on_rv_frame_changed, "Broadcast Frame"),
            ("selection-changed", self.on_rv_selection_changed, "Broadcast Selection"),
            ("graph-state-change", self.on_rv_graph_state_change, "Broadcast Annotation"),
            ("clear-paint", self.annotation.on_clear_paint, "Broadcast Annotation Clear"),
            ("clear-all-paint", self.annotation.on_clear_paint, "Broadcast Annotation Clear All"),
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

    @property
    def _rv_updating(self):
        """``True`` while any remote-apply scope is open, including nested ones.

        Depth-counted rather than a plain flag: RV's events are synchronous, so
        an apply that itself triggers a further apply (e.g. ``on_synced``'s
        view rebuild calling into ``_apply_playback``, which does its own
        inner apply scope) must not have the inner scope's exit re-enable
        broadcasting before the outer one is done. A plain bool collapsed to
        ``False`` the instant *any* nested scope exited, which is exactly the
        gap a synchronous echo event could land in.

        Every existing call site keeps working unchanged: assigning ``True``/
        ``False`` here increments/decrements :attr:`_updating_depth` rather
        than overwriting a flag, so the many ``self._rv_updating = True`` /
        ``finally: self._rv_updating = False`` pairs throughout this plugin
        nest correctly with no call-site changes. New code should prefer the
        :meth:`_updating` context manager instead.
        """
        return self._updating_depth > 0

    @_rv_updating.setter
    def _rv_updating(self, value):
        if value:
            self._updating_depth += 1
        else:
            self._updating_depth = max(0, self._updating_depth - 1)

    @contextlib.contextmanager
    def _updating(self):
        """Context manager form of the depth-counted apply-scope guard.

        ``with self._updating(): ...`` is equivalent to the existing
        ``self._rv_updating = True`` / ``finally: self._rv_updating = False``
        pattern, preferred for new call sites.
        """
        self._rv_updating = True
        try:
            yield
        finally:
            self._rv_updating = False

    def _build_menu(self):
        """Return the menu list for the current session state."""
        if _SYNC_IMPORT_ERROR:
            # Offering Create/Join here would present functional-looking items
            # that connect_to_session silently refuses. Say so instead.
            return [
                (self.MENU_TITLE, [
                    ("Sync Unavailable (otio_sync_core import failed)", None, None,
                     lambda: rv.commands.DisabledMenuState),
                ])
            ]
        if self._in_session:
            return [
                (self.MENU_TITLE, [
                    (f"Leave Session ({self._current_session_name})", self.do_leave_session, None,
                     lambda: rv.commands.NeutralMenuState),
                    ("Force Resync", self.do_resync, None,
                     lambda: rv.commands.DisabledMenuState if self.sync_manager and self.sync_manager.is_master else rv.commands.NeutralMenuState),
                    ("_", None),
                    ("Add Clip to Timeline...", self.do_add_clip, None,
                     lambda: rv.commands.NeutralMenuState),
                    ("Session State...", self.do_show_session_state, None,
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
                ("Session State...", self.do_show_session_state, None,
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
        if resolve_host:
            try:
                resolve_host(host)
            except ValueError as e:
                _log(f"ORI_SESSION connect aborted: {e}")
                print(f"[OTIOSync] {e}", file=sys.stderr)
                _show_warning(str(e))
                return
        self.disconnect_from_session()
        self._current_host = host
        self._current_session_name = session_name

        # app_name ranks this peer for host election: xStudio is the preferred
        # visibility authority, so RV hosts only an RV-only session.
        self.sync_manager = SyncManager(session_id=session_name, app_name="openrv")
        # Expose the manager to the in-process sync_test inspector (it reads
        # manager.export_state() for /full_state and the active timeline name).
        try:
            import otio_sync_core
            otio_sync_core.register_manager(self.sync_manager)
            otio_sync_core.register_annotation_controller(self.annotation)
            otio_sync_core.register_playback_controller(self.playback)
        except Exception as e:
            _log(f"Could not register manager for inspection: {e}")
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
            if path and path.startswith("metadata/color") and not self._rv_updating:
                self._rv_updating = True
                try:
                    self.color.on_property_changed(target_uuid, path, new_value)
                finally:
                    self._rv_updating = False
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

        @self.sync_manager.on_host_changed
        def _on_host_changed(host_guid, is_host):
            print(
                f"[OTIOSync] Visibility authority: "
                + ("this peer is HOST" if is_host
                   else f"following host {(host_guid or 'none')[:8]}"),
                file=sys.stderr,
            )

        @self.sync_manager.on_synced
        def _on_synced():
            role = "MASTER" if self.sync_manager.is_master else "CLIENT"
            # Host is reported separately from master on purpose: they are
            # different roles (snapshot authority vs visibility authority) and
            # conflating them in the log is how they get conflated in reasoning.
            seat = "HOST" if self.sync_manager.is_host else "follower"
            print(
                f"[OTIOSync] Connected to session '{session_name}' on {host} as {role} ({seat})",
                file=sys.stderr,
            )
            if not self.sync_manager.is_master:
                self._rv_updating = True
                try:
                    self.sequence.rebuild_rv_session()
                    if self.sync_manager.playback_state:
                        self.playback._apply_playback(self.sync_manager.playback_state)
                    if self.sync_manager.display_state:
                        self.display._apply_display_state(self.sync_manager.display_state)
                    self.color.apply_all()
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
        """Initialise the session as the first participant (Master).

        Mastership is claimed synchronously but announced later: ``poll_network``
        re-checks ``STATE_DISCOVERING`` every 33 ms tick and would re-enter here
        for the whole of _deferred_master_init's expansion window, while a
        joiner's snapshot is empty until that init has built the timelines.  The
        matching ``broadcast_master_response()`` is at the end of
        :meth:`_deferred_master_init`.
        """
        self.sync_manager.elect_self_as_master(broadcast=False)
        self._deferred_master_init()

    def _deferred_master_init(self, attempt=0):
        """Build the initial timelines, deferring while an OTIO import expands.

        RV expands an imported ``.otio`` asynchronously; snapshotting before the
        ``tracks`` Stack has materialized would register empty/partial state
        (the old retry-on-empty heuristic). Poll a bounded number of times until
        expansion settles, then run the normal init.
        """
        if self.sequence._otio_expansion_pending() and attempt < 40:
            if attempt == 0:
                _log("OTIO import expanding — deferring master init")
            if QtCore:
                QtCore.QTimer.singleShot(
                    250, lambda: self._deferred_master_init(attempt + 1)
                )
                return
            # No Qt scheduler available — fall through and init with what's there.

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
            # Only retry the native scan when there is genuinely nothing yet AND
            # no OTIO import is in play (an OTIO-only session legitimately has no
            # native timelines — its content is synced via the snapshot model).
            if total_clips == 0 and not self.sequence._otio_stack_groups():
                _log("No clips found on init — scheduling graph-settled retry")
                QtCore.QTimer.singleShot(500, self.sequence._retry_init_timelines)
        else:
            self.sequence._init_single_timeline(fps)

        # Mirror any OTIO-imported Stacks into the sync manager as snapshot
        # timelines (whole-OTIO model), built via RV's native otio_writer.
        self.sequence.init_otio_timelines()

        # Deferred half of the election started in _init_as_master (which claims
        # mastership with broadcast=False).  Not a stray broadcast: announcing
        # only now guarantees a joiner's STATE_REQUEST is answered with a
        # populated snapshot rather than an empty one.
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
                            "playback_mode": _PLAY_MODE_TO_WIRE.get(rv.commands.playMode(), "loop"),
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
            self.sequence._poll_deleted_sequences()
            self.sequence.check_otio_snapshots()
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
                # OTIO-origin timelines are rebuilt via RV's native reader
                # (full Stack/EDL fidelity); native ones use the flat builder.
                if timeline_origin(data) == ORIGIN_OTIO_IMPORT:
                    self.sequence.apply_otio_snapshot(data)
                else:
                    self.sequence._create_rv_sequence_for_timeline(data)
            finally:
                self._rv_updating = False
        elif action == "replace_timeline":
            self._rv_updating = True
            try:
                self.sequence.apply_otio_snapshot(data)
            finally:
                self._rv_updating = False
        elif action == "remove_timeline":
            self._rv_updating = True
            try:
                self.sequence._delete_rv_sequence_for_timeline(data)
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

    def on_rv_play_mode_changed(self, event):
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
        # Mark OTIO snapshots dirty on structural graph changes so the next poll
        # re-exports and diffs (gates the whole-timeline serialization off the
        # hot path). Reading contents does not consume the event.
        try:
            contents = event.contents()
            if contents and (
                "SEQUENCES" in contents   # clip EDL / cut-trim changes (5.2)
                or "STACKS" in contents   # structural topology changes
                or "SOURCES" in contents  # media swap on an existing source (5.1)
            ):
                self.sequence._otio_dirty = True
        except Exception:
            pass
        # Color changes (ocio.inColorSpace) are consumed here; everything else
        # falls through to the annotation handler.
        if self.color.on_graph_state_change(event):
            return
        self.annotation.on_graph_state_change(event)

    def do_create_session(self, event=None):
        """Prompt for host/name and create a new session (with master-check warning)."""
        if self._in_session:
            _show_warning(
                f"Already connected to '{self._current_session_name}'. "
                "Leave the current session first."
            )
            if event: event.reject()
            return
        host, name = session_dialog("Create Session")
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
        host, name = session_dialog("Join Session")
        if name:
            self.connect_to_session(host, name)
        if event: event.reject()

    def do_leave_session(self, event=None):
        """Disconnect and return to local-only operation."""
        _log("User requested: Leave Session")
        self.disconnect_from_session()
        if event: event.reject()
        
    def do_resync(self, event=None):
        """Force a full state sync from the master."""
        if self.sync_manager and not self.sync_manager.is_master:
            _log("Forcing resync from master...")
            self.sync_manager.request_state()
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
        self.sequence.add_clip_from_path(path)
        if event: event.reject()

    def _local_view(self):
        """What *this* RV is showing, as ``(timeline_guid, clip_guid)``.

        The manager only tracks the session-wide view, so the Session State
        panel asks the host directly to tell whether we have diverged from it.

        Resolved through ``_displayed_view()`` — the same reader the apply path
        uses — because it answers for a source group (the isolated clip's own
        timeline) as well as a sequence.  Falling back to
        ``active_timeline_guid`` here would make the local view equal the shared
        one by construction, and the panel could never report a split.
        """
        _mode, _node, tl_guid = self.playback._displayed_view()
        return (tl_guid, self.playback._cur_clip_guid)

    def do_show_session_state(self, event=None):
        if not self.sync_manager:
            if event: event.reject()
            return
            
        try:
            from PySide6.QtQuick import QQuickView
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QColor
            from PySide6.QtQml import QQmlEngine
        except ImportError:
            _log("PySide6 not available for UI")
            if event: event.reject()
            return
            
        import otio_sync_core
        from otio_sync_core.ui_model import SessionStateModel, PeerListModel
        
        if not hasattr(self, "_state_view"):
            self._state_view = QQuickView()
            self._state_view.setTitle(f"Session State ({self.sync_manager.session_id})")
            self._state_view.resize(400, 500)
            self._state_view.setResizeMode(QQuickView.SizeRootObjectToView)
            
            # Setup models
            self._session_model = SessionStateModel(
                self.sync_manager, local_view_provider=self._local_view
            )
            self._peer_model = PeerListModel(self.sync_manager)
            
            self._state_view.rootContext().setContextProperty("sessionState", self._session_model)
            self._state_view.rootContext().setContextProperty("peerModel", self._peer_model)
            
            # Add path to qmldir
            # Add path to PySide6 QML modules (e.g. QtQuick.Controls)
            import PySide6
            pyside_qml = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "qml")
            if os.path.exists(pyside_qml):
                self._state_view.engine().addImportPath(pyside_qml)
                
            qml_path = os.path.join(os.path.dirname(otio_sync_core.__file__), "qml")
            
            qml_file = os.path.join(qml_path, "SessionStatePanel.qml")
            self._state_view.setSource(QUrl.fromLocalFile(qml_file))
            
            for err in self._state_view.errors():
                _log(f"QML Error: {err.toString()}")
            
            # Optional: handle engine quitting if needed
            def _cleanup():
                if hasattr(self, "_state_view"):
                    del self._state_view
            self._state_view.engine().quit.connect(_cleanup)
            
        self._state_view.show()
        self._state_view.raise_()
        
        if event: event.reject()

    def deactivate(self):
        self.disconnect_from_session()
        rv.rvtypes.MinorMode.deactivate(self)


def createMode():
    return OpenRVSyncPlugin()
