#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""
xStudio plugin: ORI Sync Review

Joins an ORI Sync session via RabbitMQ, providing bidirectional playback
sync and annotation broadcast/receive using SyncManager from ORIAnnotations.

Threading model
---------------
xStudio calls plugin event handlers (``_on_bookmark_event``,
``_on_playhead_event``, etc.) on its own message-dispatch thread.  The
RabbitMQ send path (``RabbitMQNetwork.send_payload``) uses a
BlockingConnection and must not run on xStudio's thread.

All calls that mutate the manager are therefore pushed onto ``_cmd_queue``
or handled by ``_flush_pending_local_bookmarks`` — both executed by the poll
thread (``_poll_loop``).  The poll thread is the only thread that touches the
SyncManager after startup.

The one exception is ``_apply_playback_state``, which is called from the
poll thread via the ``on_playback_changed`` callback and writes to the
xStudio playhead.  xStudio's actor-based attribute system routes those
writes safely, but this should be verified against the installed version.

Poll-thread actor reads must be bounded
---------------------------------------
Every xStudio property read/write is a synchronous ``request_receive`` bounded
only by ``connection.default_timeout_ms`` (100 s default).  A read to a *stale*
playhead/viewport/bookmark actor (one destroyed during a source-view switch, or
busy under an annotation stream) blocks the poll thread for the full 100 s,
silently killing sync while xStudio's UI stays responsive.  A Python-thread
timeout cannot help — the C++ dequeue holds the GIL.  Such reads are therefore
wrapped with ``utils.bounded`` / ``utils.bounded_timeout`` to lower the timeout
at the C++ level.  Structural calls (``load_otio`` / ``to_otio_string``) are
deliberately left unbounded — they can be legitimately slow.  See
``docs/xstudio_constraints.md`` → "request_receive has a 100-second default
timeout" for the full rule.
"""

# utils performs the sys.path / OTIO_PLUGIN_MANIFEST_PATH setup as a side-effect.
from .utils import _log, _log_exc, _parse_ori_session, _uri_to_posix_path, QML_FOLDER, SESSION_DIALOG_QML  # noqa: E402
from .media_map import MediaMapController  # noqa: E402
from .timeline_build import TimelineBuildController  # noqa: E402
from .display_sync import DisplaySyncController  # noqa: E402
from .playback_sync import PlaybackSyncController  # noqa: E402
from .structure_sync import StructureSyncController  # noqa: E402
from .annotation_sync import AnnotationSyncController  # noqa: E402
from .color_sync import ColorSyncController  # noqa: E402

import os
import json
import queue
import threading
import time

import opentimelineio as otio
from xstudio.connection import Connection
from xstudio.api.session.playhead import Playhead

from otio_sync_core.manager import STATE_DISCOVERING, STATE_SYNCED, SyncManager  # noqa: E402
from otio_sync_core.rabbitmq_network import RabbitMQNetwork  # noqa: E402
from xstudio.plugin import PluginBase  # noqa: E402

# ── plugin ─────────────────────────────────────────────────────────────────────

class ORISyncPlugin(PluginBase):
    """xStudio plugin that joins an ORI Sync session.

    :param connection: xStudio connection object passed by the plugin loader.
    """

    #: How long to wait for a master before self-electing (seconds).
    DISCOVERY_TIMEOUT = 2.0
    #: Fallback scan interval (seconds).  AnnotationsCore plugin_events_ events
    #: (stroke_completed=True) are the preferred pen-up signal when they fire.
    #: This scan catches strokes in builds where those events are absent.
    #: Set to 1.0 until AnnotationsCore events are confirmed in the target build.
    ANNOTATION_SCAN_INTERVAL = 1.0

    def __init__(self, connection):
        PluginBase.__init__(
            self,
            connection,
            name="ORI Sync Review",
            qml_folder=QML_FOLDER,
        )

        # ── connection preferences exposed to the UI ───────────────────────
        self.mq_host_attr = self.add_attribute(
            "MQ Host", "127.0.0.1", register_as_preference=True
        )
        self.mq_host_attr.expose_in_ui_attrs_group("ori_sync_conn")

        self.mq_port_attr = self.add_attribute(
            "MQ Port", 5672, register_as_preference=True
        )
        self.mq_port_attr.expose_in_ui_attrs_group("ori_sync_conn")

        self.session_id_attr = self.add_attribute(
            "Session ID", "otio-sync-demo", register_as_preference=True
        )
        self.session_id_attr.expose_in_ui_attrs_group("ori_sync_conn")

        self.status_attr = self.add_attribute("Status", "Disconnected")
        self.status_attr.expose_in_ui_attrs_group("ori_sync_conn")

        # ── controllers ───────────────────────────────────────────────────
        self.media = MediaMapController(self)
        self.display = DisplaySyncController(self)
        self.builder = TimelineBuildController(self)
        self.playback = PlaybackSyncController(self)
        self.structure = StructureSyncController(self)
        self.annotation = AnnotationSyncController(self)
        self.color = ColorSyncController(self)

        # ── xStudio handles ────────────────────────────────────────────────
        self.active_playhead: Playhead | None = None
        self.subscribe_to_global_playhead_events(self._on_global_playhead_event)

        # ── runtime state ──────────────────────────────────────────────────
        self.manager: SyncManager | None = None
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        # Periodic dump of manager.export_state() to ORI_FULLSTATE_FILE so the
        # out-of-process test inspector can read guid-accurate state (it cannot
        # reach this in-process manager, and timeline_to_otio_string drops the
        # sync metadata).
        self._last_fullstate_write = 0.0

        # One xStudio (playlist, timeline) per OTIO timeline GUID received from the session.
        # Populated by _do_load_timelines() when we join as a non-master peer.
        self._sync_playlists: dict[str, tuple] = {}

        # Tracks the current OTIO clip-GUID order for the Media track of each
        # synced timeline.  Keyed by tl_guid, value is a list of clip sync-GUIDs
        # in the order they appear in the xStudio timeline track.  Initialised
        # from the OTIO track at load time and kept in sync by
        # _apply_remote_move_child so we never have to query xStudio clip actors.

        # Commands enqueued by xStudio callbacks; drained by poll thread.
        # Items are (command_name, payload_dict).
        self._cmd_queue: queue.Queue[tuple[str, dict]] = queue.Queue()

        # UUIDs of bookmarks we created from *remote* annotations.
        # show_atom scans skip these so we never re-broadcast them back.

        # Sync GUIDs of annotation clips that THIS peer has created or broadcast to.
        # Used to guard broadcast_replace_annotation_commands: only replace a clip
        # that we own.  If ann_clip_guid is not in this set, use broadcast_add_annotation
        # (parallel annotation) instead of overwriting the remote peer's clip.

        # Monotonic deadline before which show_atom annotation flushes are
        # suppressed.  Set briefly after load_otio reloads (e.g. on move_child)
        # so that xStudio's bookmark-re-trigger burst is not mistaken for new
        # local strokes.
        self._reload_suppress_until: float = 0.0

        # Cross-thread annotation trigger: set on xStudio thread by _on_annotation_event /
        # _on_core_annotation_event; read and cleared on poll thread by flush_pending_annotations.
        self._annotation_pending_time: float | None = None

        # Polling-based scrub detection: last frame seen by the poll loop and
        # last frame applied from a remote PLAYBACK_SETTINGS message.
        # When the poll sees a frame change that matches _last_applied_frame the
        # change came from a remote apply, so we skip re-broadcasting (echo guard).
        self._last_polled_frame: int | None = None
        self._last_applied_frame: int | None = None
        self._last_polled_playing: bool | None = None
        # Monotonic deadline until which local playhead attribute_changed events
        # are NOT re-broadcast.  Set on every remote playback apply: ph.position
        # fires attribute_changed asynchronously, and during rapid scrubbing the
        # single _last_applied_frame guard loses the race (a lagging callback for
        # an older frame no longer matches), echoing positions back and creating
        # a feedback loop.  A short rolling window suppresses those echoes while a
        # peer is driving playback, without blocking genuine local scrubs once it
        # stops (the window simply expires).
        self._playback_apply_suppress_until: float = 0.0
        # Monotonic deadline refreshed whenever we broadcast a *local* playhead
        # move (scrubbing).  Used only to suppress selection-driven clip-start
        # seeks while we are the one driving playback — a peer following our scrub
        # crosses clip boundaries and echoes selections back, and applying those
        # seeks would snap our own playhead to clip starts.  Kept separate from
        # _playback_apply_suppress_until, which doubles as the broadcast echo guard.
        self._local_scrub_active_until: float = 0.0
        # Monotonic timestamp of when playback last transitioned False→True.
        # Used to allow the first show_atom after play-start to broadcast even
        # though _last_polled_playing is already True (race-condition guard).
        self._playing_started_at: float = 0.0
        # Timestamp of the last remote playing=False; used to ignore rapid
        # stop→start (loop restart) events from the peer so they don't flip
        # _last_polled_playing back to True and suppress show_atom broadcasts.
        self._last_remote_stop_at: float = 0.0

        # Most recent show_atom media — tracked unconditionally so the PSM
        # True→False handler can broadcast mode=source even when the show_atom
        # itself was NOT suppressed (e.g. at play-start within the 0.3 s window).
        self._last_show_atom_media: str | None = None
        self._last_show_atom_seq_tl_guid: str | None = None
        self._last_show_atom_at: float = 0.0
        # Last clip GUID seen in the viewport (playlist selection or show_atom).
        # Used as a fallback in the annotation broadcast path for flat playlists,
        # where _resolve_clip_at_frame returns None.
        # Deferred seek: when a multi-clip sequence selection is received,
        # the target frame and its deadline are stored here.  Both Form-2
        # viewport_playhead_atom events fire within ~200 ms of the source
        # switch and update active_playhead; the poll loop applies the seek
        # once the deadline passes and the playhead has settled.

        # Last display state broadcast; compared each poll tick to detect changes.
        # xStudio's internal viewport scale at the first successful read.  Used
        # to normalise state_.scale_ (which is image_pixels/viewport_pixels, not
        # a zoom multiplier) to RV's convention (1.0 = fit-to-window).
        # Last read value of the playhead "Pinned Source Mode" attribute.
        # True = full timeline/sequence view; False = single selected-media view.
        # None on first read (no broadcast on initialisation).
        # Set to True while _apply_selection is writing Pinned Source Mode so
        # the poll loop ignores the resulting attribute-change echo.
        self._applying_pinned_mode: bool = False
        # Monotonic deadline before which show_atom clip-selection broadcasts are
        # suppressed.  Set after _apply_selection calls select_all() to prevent
        # the resulting show_atom burst from echoing individual clip selections
        # back to remote peers.
        self._selection_broadcast_suppress_until: float = 0.0
        self._structural_mutation_suppress_until: float = 0.0
        # Cached Viewport object; created lazily, cleared on disconnect.
        # Timeline to set as on-screen source once the viewport is ready.
        # Set by _do_load_timelines; consumed and cleared by _get_viewport.
        self._last_selection_scan = 0.0
        self.display._last_display_scan = 0.0
        self._last_flat_playlist_scan = 0.0
        self.structure._last_structure_scan = 0.0
        # Timestamps to throttle log messages during viewport discovery retry loop.

        # Maps tl_guid → (xs_playlist, [media_name_order]) for flat-Playlist
        # timelines built by _build_otio_from_playlist_media.  Only populated on
        # the master; used by _poll_flat_playlist_reorders to detect bin reorders
        # and broadcast MOVE_CHILD to peers.

        # Maps tl_guid → (xs_playlist, xs_timeline) for sequence Timelines built
        # by _build_otio_timelines on the master.  Used by _poll_sequence_new_media
        # to detect added clips and broadcast INSERT_CHILD.

        # Maps tl_guid → set of media names last seen in xs_playlist.media for
        # sequence Timelines.  Used by _poll_sequence_new_media to detect deletions:
        # names present here but absent in the current poll are broadcast as REMOVE_CHILD.

        # Viewport container tracking state: caches whether the active viewport
        # container is a Playlist or Timeline to avoid synchronous API calls in
        # playhead event handlers.
        self._viewport_container_is_playlist: bool = False
        self._viewport_container_is_timeline: bool = False
        # [TEST] subscription ID returned by subscribe_to_event_group for change_atom probe

        # [2F] Event-driven clip insertion: subscription IDs keyed by tl_guid.
        # When item_atom fires on a Timeline's event group, tl_guid is added to
        # _timeline_item_dirty so the poll thread can call _poll_sequence_new_media
        # for just that timeline without waiting for the next 0.5 s scan.

        self._pending_create_check: bool = False

        # Requester GUIDs that sent STATE_REQUEST when we had no timelines yet.
        # On each poll tick we retry send_state_snapshot until it succeeds.

        # Last-observed xStudio track clip name list per sequence timeline.
        # None = not yet recorded (e.g. just after load_otio); the next poll
        # records without comparing.  Only the poll AFTER that can detect real deletions.

        # Add session management menu items.
        self.insert_menu_item(
            "main menu bar",
            "Create Session...",
            "Session|Connect",
            0.1,
            callback=self._menu_create_session,
        )
        self.insert_menu_item(
            "main menu bar",
            "Join Session...",
            "Session|Connect",
            0.2,
            callback=self._menu_join_session,
        )
        self.insert_menu_item(
            "main menu bar",
            "Leave Session",
            "Session|Connect",
            0.3,
            callback=self._menu_leave_session,
        )

        # Place the top-level "Session" menu just before "Help" (which xStudio
        # fixes at position 100 on the main menu bar).  The position must be a
        # float — xStudio's menu-model handler matches on a double, so an int
        # is silently ignored and the menu falls back to its default slot.
        self.set_submenu_position("main menu bar", "Session", 99.0)

        self.connect_to_ui()

        ori_session = os.environ.get("ORI_SESSION")
        if ori_session:
            host, name = _parse_ori_session(ori_session)
            # Override the stored preference so it reflects what we used.
            self.mq_host_attr.set_value(host)
            self.session_id_attr.set_value(name)
            _log(f"ORI_SESSION set — auto-connecting to '{name}' on {host}")
            try:
                self.connect_to_session(host, name)
            except Exception:
                _log_exc("ORI_SESSION auto-connect failed")
        else:
            _log("Plugin loaded — no ORI_SESSION set, starting disconnected")

    # ── connection lifecycle ───────────────────────────────────────────────────

    def connect_to_session(self, host: str | None = None, session_name: str | None = None) -> None:
        """Connect to RabbitMQ and join the sync session.

        :param host: RabbitMQ hostname; falls back to ``mq_host_attr`` if ``None``.
        :param session_name: Session / exchange name; falls back to ``session_id_attr``
            if ``None``.
        """
        self.disconnect()
        self._poll_stop.clear()
        self.annotation._last_annotation_scan = time.monotonic()

        if host is None:
            host = self.mq_host_attr.value()
        else:
            self.mq_host_attr.set_value(host)
        if session_name is None:
            session_name = self.session_id_attr.value()
        else:
            self.session_id_attr.set_value(session_name)

        port = int(self.mq_port_attr.value())

        network = RabbitMQNetwork(
            host=host,
            port=port,
            session_id=session_name,
            self_guid=str(self.uuid),
        )
        self.manager = SyncManager(
            session_id=session_name,
            self_guid=str(self.uuid),
            network=network,
        )
        self.manager.on_playback_changed(self.playback.apply_playback_state)
        self.manager.on_status_changed(
            lambda s: self.status_attr.set_value(s)
        )
        # Colour metadata changes arrive as property changes (no dedicated tick
        # action); apply them to xStudio's OCIO pipeline as they land.
        self.manager.on_property_changed(self.color.apply_property_change)

        # Register on_synced here so the pending_create_check flag is captured
        # correctly for this connect call.
        _pending = self._pending_create_check

        @self.manager.on_synced
        def _on_synced_once():
            self._on_synced()
            if _pending and not self.manager.is_master:
                name = session_name or ""
                self.popup_message_box(
                    "Session Already Exists",
                    f"Session '{name}' already exists. "
                    "You have joined as a peer rather than creating a new session.",
                )
            self._pending_create_check = False

        # Wait for the consumer queue to be bound before broadcasting
        # WHO_IS_MASTER.  Without this, the I_AM_MASTER response from an
        # existing master can arrive before the queue exists and be lost,
        # causing xStudio to self-elect and end up with two masters.
        if not network.wait_until_ready(timeout=5.0):
            _log("Warning: RabbitMQ consumer did not become ready within 5 s")

        self.manager.start_session()

        # Grab the current playhead and subscribe to its position events.
        # subscribe_to_playhead_events() wires the playhead's attribute_changed
        # callback (via the base __connect_to_playhead) to playhead_attribute_changed
        # → on_playhead_attribute_changed, which is how scrub/position changes reach
        # us and get broadcast.  (The base __connect_to_playhead had a bogus
        # add_message_callback line referencing an undefined event_group, fixed in
        # plugin_base.py; without that fix this call raises NameError.)
        try:
            self.playback.check_and_update_active_playhead()
            self.subscribe_to_playhead_events()
        except Exception:
            _log_exc("Could not initialize active playhead at connect time")

        # Subscribe to the AnnotationsUI plugin so we hear annotation_atom
        # events whenever the user completes a stroke in xStudio.  This is
        # the same pattern used by xstudio_live_review.py (proven to work).
        try:
            ann_plugin = self.get_plugin("AnnotationsUI")
            self.subscribe_to_plugin_events(ann_plugin, self._on_annotation_event)
            _log("Subscribed to AnnotationsUI plugin events")
        except Exception:
            _log_exc("Could not subscribe to AnnotationsUI events")

        # [2C] Subscribe to AnnotationsCore's plugin_events_ group to receive
        # (event_atom, annotation_data_atom, user_id, stroke_completed) events.
        # stroke_completed=True fires at PaintEnd (pen-up); False fires at
        # PaintStart/PaintPoint (mid-stroke).  This replaces the show_atom
        # hot-scan activation and the 33 ms poll as the primary annotation trigger.
        try:
            ann_core_plugin = self.get_plugin("AnnotationsCore")
            self.subscribe_to_plugin_events(ann_core_plugin, self._on_core_annotation_event)
            _log("Subscribed to AnnotationsCore plugin events [2C]")
        except Exception:
            _log_exc("Could not subscribe to AnnotationsCore events")

        # Subscribe to the current viewed container's event group for add_media
        # detection.  If there's no container yet (peer joined an empty session),
        # on_global_playhead_event re-subscribes once one is viewed.
        try:
            container = self.playback.get_viewed_container_safe()
            if container:
                self.structure.subscribe_viewed_container_events(container)
            else:
                _log("[2F] no viewed_container yet (session empty at connect time) — will subscribe on first view")
        except Exception:
            _log_exc("[2F] initial viewed-container subscribe failed")

        # Subscribe to viewed container selection actor
        try:
            container = self.playback.get_viewed_container_safe()
            if container:
                self.playback.subscribe_container_selection(container)
        except Exception:
            _log_exc("[SEL] Initial selection subscription failed")

        # Self-elect if no master answers within DISCOVERY_TIMEOUT.
        threading.Thread(
            target=self._discovery_timeout_task, daemon=True
        ).start()

        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="ori_sync_poll", daemon=True
        )
        self._poll_thread.start()
        _log(f"Connecting: session={session_name!r} mq={host}:{port}")

    def disconnect(self) -> None:
        """Disconnect from the session and stop all background threads."""
        self._poll_stop.set()
        # Never join the current thread (e.g. when called from the poll thread
        # itself via the leave_session cmd_queue path).
        if (self._poll_thread
                and self._poll_thread.is_alive()
                and self._poll_thread is not threading.current_thread()):
            self._poll_thread.join(timeout=1.0)
        self._poll_thread = None
        if self.manager:
            self.manager.close()
            self.manager = None
        self.display._viewport = None
        self.display._last_display_state = {}
        self.display._xs_base_scale = None
        self._sync_playlists.clear()
        self.structure._xs_flat_playlists.clear()
        self.structure._xs_sequence_playlists.clear()
        self.structure._xs_sequence_media_names.clear()
        self.media.reset()
        self.color.reset()
        self.structure._timeline_item_sub_ids.clear()
        with self.structure._timeline_item_lock:
            self.structure._timeline_item_dirty.clear()
        if self.playback._current_selection_sub_id is not None:
            try:
                self.unsubscribe_from_event_group(self.playback._current_selection_sub_id)
            except Exception:
                pass
            self.playback._current_selection_sub_id = None
        self.playback._current_selection_container_uuid = None
        self.playback._last_logged_container_uuid = None
        self.playback._last_logged_clip_name = None
        self.playback._last_viewed_clip_guid = None
        self.playback._pending_seek_frame = None
        self.playback._pending_seek_deadline = 0.0
        self.playback._last_pinned_source_mode = None
        self._applying_pinned_mode = False
        self._selection_broadcast_suppress_until = 0.0
        self._structural_mutation_suppress_until = 0.0
        self.structure._pending_snapshot_requesters.clear()
        self.structure._xs_sequence_track_names.clear()
        self.status_attr.set_value("Disconnected")

    def cleanup(self) -> None:
        """Called by xStudio when the plugin is unloaded."""
        self.disconnect()

    # ── session menu callbacks ─────────────────────────────────────────────────

    def _menu_create_session(self) -> None:
        """Open SessionDialog in 'create' mode."""
        if self.manager is not None:
            name = self.session_id_attr.value() or "current"
            self.popup_message_box(
                "Already Connected",
                f"Already connected to '{name}'. Leave the current session first.",
            )
            return
        self._pending_create_check = True
        self.create_qml_item(SESSION_DIALOG_QML)

    def _menu_join_session(self) -> None:
        """Open SessionDialog in 'join' mode."""
        if self.manager is not None:
            name = self.session_id_attr.value() or "current"
            self.popup_message_box(
                "Already Connected",
                f"Already connected to '{name}'. Leave the current session first.",
            )
            return
        self._pending_create_check = False
        self.create_qml_item(SESSION_DIALOG_QML)

    def _menu_leave_session(self) -> None:
        """Disconnect from the active session."""
        if self.manager is None:
            return
        self._cmd_queue.put(("leave_session", {}))

    def do_session_connect(self, data) -> list:
        """Called from QML SessionDialog via python_callback.

        Spawns a background thread to perform the connection so that the
        python_callback (which blocks xStudio's Qt main thread) returns
        immediately.  connect_to_session() does blocking RabbitMQ I/O and
        calls disconnect() internally, which joins the poll thread — that join
        must not happen on the poll thread itself.

        :param data: Dict with ``host`` and ``name`` keys.
        :returns: ``[True, "Connecting…"]`` immediately.
        :rtype: list
        """
        host = (data.get("host") or "").strip() or os.environ.get("ORI_RMQ_HOST", "127.0.0.1")
        name = (data.get("name") or "").strip()
        if not name:
            return [False, "Session name cannot be empty."]
        threading.Thread(
            target=self._session_connect_worker,
            args=(host, name),
            daemon=True,
        ).start()
        return [True, "Connecting…"]

    def _session_connect_worker(self, host: str, name: str) -> None:
        """Background thread that calls connect_to_session safely off the poll thread."""
        try:
            self.connect_to_session(host, name)
        except Exception:
            _log_exc("session connect worker failed")

    # ── discovery ──────────────────────────────────────────────────────────────

    def _discovery_timeout_task(self) -> None:
        """Self-elect as master when the discovery timeout expires."""
        time.sleep(self.DISCOVERY_TIMEOUT)
        if self.manager and self.manager.status == STATE_DISCOVERING:
            _log("No master found — self-electing")
            # Register the current xStudio session as the initial timeline.
            # Done here rather than at connect time because viewed_container
            # fails at startup before any media is loaded.
            for tl in self.builder.build_otio_timelines():
                self.manager.register_timeline(tl)
            self.manager.is_master = True
            self.manager.master_guid = self.manager.self_guid
            self.manager.broadcast_master_response()
            self.manager._set_status(STATE_SYNCED)

    # ── poll loop ──────────────────────────────────────────────────────────────

    def _write_fullstate_file(self) -> None:
        """Atomically dump ``manager.export_state()`` to ``ORI_FULLSTATE_FILE``.

        The out-of-process test inspector reads this for guid-accurate state
        (it cannot reach this in-process manager, and ``timeline_to_otio_string``
        strips the sync metadata).  No-op unless the env var is set.
        """
        path = os.environ.get("ORI_FULLSTATE_FILE")
        if not path or not self.manager:
            return
        try:
            data = self.manager.export_state()
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except Exception as e:
            _log(f"_write_fullstate_file failed: {e}")

    def _poll_loop(self) -> None:
        """Background thread: blocks on command queue and processes ticks."""
        import contextlib

        @contextlib.contextmanager
        def _timed(label: str):
            _t0 = time.monotonic()
            try:
                yield
            finally:
                _dt = time.monotonic() - _t0
                if _dt > 1.0:
                    _log(f"[POLL-SLOW] {label} took {_dt:.1f}s")

        while not self._poll_stop.is_set():
            try:
                # 1. Poll timeout for command queue wait
                timeout = 0.1

                # 2. Block on the queue to wait for events or ticks
                try:
                    cmd, payload = self._cmd_queue.get(timeout=timeout)
                    with _timed(f"cmd:{cmd}"):
                        self._execute_command(cmd, payload)
                    with _timed("drain_cmd_queue"):
                        self._drain_cmd_queue()
                except queue.Empty:
                    pass

                # 3. Manager/network tick
                if self.manager:
                    with _timed("manager.tick"):
                        _events = self.manager.tick()
                    for action, data in _events:
                        with _timed(f"handle:{action}"):
                            self._handle_manager_event(action, data)

                # 4. Pen-up annotation flush
                with _timed("annotation.flush"):
                    self.annotation.flush_pending_annotations()

                # 5. Deferred seek application
                with _timed("apply_pending_seek"):
                    self.playback.apply_pending_seek()
                with _timed("flush_pending_scrub_broadcast"):
                    self.playback.flush_pending_scrub_broadcast()

                # 6. Periodic display state (zoom) scan (0.5s interval)
                now = time.monotonic()
                if now - self.display._last_display_scan >= 0.5:
                    with _timed("display.poll"):
                        self.display.poll_and_broadcast_display()
                    self.display._last_display_scan = now

                # 6.1. Periodic colour state scan (2.0s interval — colour changes
                # are infrequent and tolerate latency; a tight poll needlessly
                # competes with structural/playback sync on the poll thread).
                if now - self.color._last_color_scan >= 2.0:
                    with _timed("color.poll"):
                        self.color.poll_and_broadcast_color()
                    self.color._last_color_scan = now

                # 6.2. Periodic full-state dump for the test inspector (0.5s).
                if now - self._last_fullstate_write >= 0.5:
                    with _timed("fullstate_write"):
                        self._write_fullstate_file()
                    self._last_fullstate_write = now

                # 6.5. Periodic structure scan (1.0s interval)
                if now - self.structure._last_structure_scan >= 1.0:
                    with _timed("structure.poll_new_playlists"):
                        self.structure.poll_new_playlists()
                    with _timed("structure.poll_playlist_renames"):
                        self.structure.poll_playlist_renames()
                    with _timed("structure.poll_deleted_playlists"):
                        self.structure.poll_deleted_playlists()
                    self.structure._last_structure_scan = now

                # 7. Deferred snapshot responses
                if self.structure._pending_snapshot_requesters and self.manager and self.manager._timelines:
                    for _req_guid in list(self.structure._pending_snapshot_requesters):
                        _log(f"Deferred snapshot: sending to {_req_guid[:8]}")
                        self.manager.send_state_snapshot(
                            _req_guid,
                            playback_state=self.playback.current_playback_state(),
                        )
                    self.structure._pending_snapshot_requesters.clear()

            except Exception:
                _log_exc("Poll loop error")

    def _execute_command(self, cmd: str, payload) -> None:
        """Execute a single enqueued command from the queue on the poll thread."""
        try:
            if cmd == "load_timelines":
                self.builder.do_load_timelines()
            elif cmd == "live_stroke":
                self.annotation.broadcast_live_stroke_from_json(payload)
            elif cmd == "clear_live_stroke":
                self.annotation._live_stroke_current_key = None
            elif cmd == "leave_session":
                self.disconnect()
            elif cmd == "broadcast_playback_state":
                if self.manager and self.manager.status == STATE_SYNCED:
                    # Resolve the timeline guid from what is *actually* viewed
                    # (the sequence when scrubbing its timeline) rather than the
                    # stale active_timeline_guid, which a prior clip selection may
                    # have set to a transient per-clip timeline the peer lacks.
                    # Cached (short TTL) so per-frame scrub broadcasts stay cheap.
                    tl_guid = self.playback.cached_viewed_timeline_guid()
                    self.manager.broadcast_playback_state(payload, timeline_guid=tl_guid)
            elif cmd == "resolve_selection":
                self.playback.resolve_and_broadcast_selection()
            elif cmd == "sync_container":
                self.structure.execute_sync_container(payload.get("tl_guid"))
            elif cmd == "sync_sequences":
                # One-shot scan triggered by add_media_atom on the viewed
                # container — detects clips dragged into any sequence track.
                self.structure.poll_sequence_new_media()
            elif cmd == "rebuild_sequence":
                # Coalesced sequence reload: one load_otio for all clips that
                # arrived in the batch, instead of one expensive reload per clip.
                self.structure.execute_sequence_rebuild(payload.get("tl_guid"))
            elif cmd == "remove_timeline":
                self.structure.delete_local_container(payload.get("tl_guid"))
            elif cmd == "load_bin_media":
                playlist = payload.get("playlist")
                uris = payload.get("uris", [])
                tl_guid = payload.get("tl_guid", "")
                for _uri in uris:
                    _path = _uri_to_posix_path(_uri)
                    if _path:
                        try:
                            playlist.add_media(_path)
                        except Exception:
                            pass
                _log(f"load_bin_media: added {len(uris)} clip(s) to bin for {tl_guid[:8]}")
        except Exception:
            _log_exc(f"Command {cmd!r} failed")

    def _drain_cmd_queue(self) -> None:
        """Execute all enqueued commands on the poll thread."""
        qsize = self._cmd_queue.qsize()
        for _ in range(qsize):
            try:
                cmd, payload = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            self._execute_command(cmd, payload)

    # ── manager event dispatch ─────────────────────────────────────────────────

    def _handle_manager_event(self, action: str, data) -> None:
        """React to events returned by manager.tick()."""
        _log(f"Event: {action}")
        if action == "state_request_received":
            requester_guid = data
            _log(f"State request from {requester_guid[:8]} — sending snapshot")
            if not self.manager.root_timeline:
                for tl in self.builder.build_otio_timelines():
                    self.manager.register_timeline(tl)
            # Snapshot current display state so the joiner inherits it.
            current_display = self.display.read_xs_display_state()
            self.manager.display_state = current_display
            self.display._last_display_state = dict(current_display)
            if self.manager._timelines:
                self.manager.send_state_snapshot(
                    requester_guid,
                    playback_state=self.playback.current_playback_state(),
                )
            else:
                # No timelines yet (session still loading) — defer until the
                # poll loop has built and registered them.
                _log(f"No timelines yet — deferring snapshot for {requester_guid[:8]}")
                if requester_guid not in self.structure._pending_snapshot_requesters:
                    self.structure._pending_snapshot_requesters.append(requester_guid)

        elif action == "partial_annotation":
            self.annotation.apply_partial_annotation_xs(data)

        elif action == "insert_child":
            child_obj = data
            ann_cmds = (
                child_obj.metadata.get("annotation_commands")
                if hasattr(child_obj, "metadata")
                else None
            )
            if ann_cmds:
                self.annotation.apply_remote_annotation(child_obj, ann_cmds)
            elif isinstance(child_obj, otio.schema.Clip):
                self.structure.apply_remote_clip_insert(child_obj)

        elif action == "annotation_commands_added":
            # An existing annotation clip had new commands merged into it on
            # the manager side.  Update the corresponding xStudio bookmark with
            # the full merged stroke set.
            merged_clip, _delta_clip = data
            self.annotation.refresh_annotation_bookmark(merged_clip)

        elif action == "annotation_commands_replaced":
            # A peer replaced the full annotation_commands list on an existing
            # clip (e.g. in-place text edit).  Re-render the bookmark.
            self.annotation.refresh_annotation_bookmark(data)

        elif action == "move_child":
            self.structure.apply_remote_move_child(data)

        elif action == "remove_child":
            self.structure.apply_remote_remove_child(data)

        elif action == "display_settings":
            self.display.apply_display_state(data)

        # selection_changed is retired — view/selection is now folded into the
        # PLAYBACK_SETTINGS view-state, applied by playback.apply_playback_state
        # via the on_playback_changed callback.

        elif action == "add_timeline":
            # A new sequence/playlist timeline arrived from a remote peer.
            # Reuse _do_load_timelines — it skips GUIDs already in
            # _sync_playlists, so it is safe to call repeatedly.
            # Both master and client create the local playlist/timeline so
            # any peer can receive new timelines regardless of master status.
            self._cmd_queue.put(("load_timelines", {}))

        elif action == "remove_timeline":
            # A sequence/playlist timeline was deleted on a remote peer.
            # `data` is the removed OTIO timeline; tear down the local
            # container on the poll thread via the command queue (the xStudio
            # session mutation must not run on the network thread).
            tl_guid = data.metadata.get("sync", {}).get("guid") if data is not None else None
            if tl_guid:
                self._cmd_queue.put(("remove_timeline", {"tl_guid": tl_guid}))

        elif action == "replace_timeline":
            # A peer pushed a wholesale structure replacement (e.g. clip trim).
            # The manager has already updated _timelines[tl_guid]; rebuild the
            # local xStudio timeline from the new OTIO.
            tl_guid = data.metadata.get("sync", {}).get("guid") if data is not None else None
            if tl_guid and tl_guid in self._sync_playlists:
                self._cmd_queue.put(("rebuild_sequence", {"tl_guid": tl_guid}))

        elif action == "timeline_renamed":
            tl_guid = data.get("timeline_guid")
            new_name = data.get("name", "")
            if tl_guid and new_name and tl_guid in self._sync_playlists:
                pl, xs_tl = self._sync_playlists[tl_guid]
                target = xs_tl if xs_tl is not None else pl
                try:
                    target.name = new_name
                    _log(f"RECV timeline_renamed: {tl_guid[:8]} → {new_name!r}")
                except Exception:
                    _log_exc(f"Could not rename timeline {tl_guid[:8]}")

        elif action == "state_request_timeout":
            _log("State request timed out. Electing self as master.")
            for tl in self.builder.build_otio_timelines():
                self.manager.register_timeline(tl)
            self.manager.is_master = True
            self.manager.master_guid = self.manager.self_guid
            self.manager.broadcast_master_response()
            self.manager._set_status(STATE_SYNCED)

    def _on_synced(self) -> None:
        _log(f"Session reached STATE_SYNCED (master={self.manager.is_master})")
        # Reset the scan timer so the first bookmarks.bookmarks call is deferred
        # by at least ANNOTATION_SCAN_INTERVAL seconds after STATE_SYNCED.
        # Without this, the scan fires immediately while xStudio's bookmark actor
        # may still be processing the async load_otio() call, causing a deadlock.
        self.annotation._last_annotation_scan = time.monotonic()
        if not self.manager.is_master:
            # We joined an existing session — create one playlist per received timeline.
            self._cmd_queue.put(("load_timelines", {}))
            if self.manager.display_state:
                self.display.apply_display_state(self.manager.display_state)

    # ── event handler thin shims ───────────────────────────────────────────────
    # xStudio registers these bound methods on the plugin; they delegate to the
    # appropriate controller so the real logic runs on the correct thread.

    def _on_global_playhead_event(self, event) -> None:
        self.playback.on_global_playhead_event(event)

    def _on_selection_event(self, event) -> None:
        self.playback.on_selection_event(event)

    def _on_test_container_event(self, event) -> None:
        self.structure.on_test_container_event(event)

    def _on_annotation_event(self, data) -> None:
        self.annotation.on_annotation_event(data)

    def _on_core_annotation_event(self, data) -> None:
        self.annotation.on_core_annotation_event(data)

    def playhead_attribute_changed(self, attr, role) -> None:
        self.playback.on_playhead_attribute_changed(attr, role)

# ── xStudio entry points ───────────────────────────────────────────────────────

def create_plugin_instance(connection):
    return ORISyncPlugin(connection)

if __name__ == "__main__":
    XSTUDIO = Connection(auto_connect=True)
    create_plugin_instance(XSTUDIO)
    XSTUDIO.link.run_xstudio_message_loop()
