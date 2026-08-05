#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""
xStudio plugin: ORI Sync Review

Joins an ORI Sync session via RabbitMQ, providing bidirectional playback
sync and annotation broadcast/receive using SyncManager from ORIAnnotations.

Threading model
---------------
xStudio calls plugin event handlers (``_on_bookmark_event``,
``_on_playhead_event``, etc.) on its own message-dispatch thread.
``RabbitMQNetwork.send_payload`` itself is non-blocking (it enqueues onto a
dedicated publisher thread that owns the BlockingConnection), but the
``SyncManager`` is not thread-safe: its state must only ever be read and
mutated by a single thread.

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
import functools
import json
import queue
import sys
import threading
import time

import opentimelineio as otio
from xstudio.connection import Connection
from xstudio.api.session.playhead import Playhead

from otio_sync_core.manager import STATE_DISCOVERING, STATE_SYNCED, SyncManager  # noqa: E402
from otio_sync_core.rabbitmq_network import RabbitMQNetwork, resolve_host  # noqa: E402
from xstudio.plugin import PluginBase  # noqa: E402

# ── plugin ─────────────────────────────────────────────────────────────────────

class ORISyncPlugin(PluginBase):
    """xStudio plugin that joins an ORI Sync session.

    :param connection: xStudio connection object passed by the plugin loader.
    """

    #: How long to wait for a master before self-electing (seconds).
    DISCOVERY_TIMEOUT = 2.0
    #: Fallback scan interval (seconds).  AnnotationsCore draw events
    #: (stroke_completed=True) are the pen-up signal, and a bookmark that
    #: disappears is detected on the same scan, so this is a safety net for
    #: strokes neither path sees — not a primary detection route.
    #: Was 1.0 while those events were going to a group nothing broadcast on;
    #: they are confirmed live since fix-xs-annotation-draw-subscription.
    ANNOTATION_SCAN_INTERVAL = 30.0

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

        # Every xStudio event group this plugin has joined, keyed by the
        # group-owning actor's address string:
        #   {"sub_id": ..., "callbacks": [(label, cb), ...]}
        # See join_event_group for the two rules this exists to enforce.
        self._event_group_subs: dict[str, dict] = {}

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

        # Commands enqueued by xStudio callbacks; drained by poll thread.
        # Items are (command_name, payload_dict).
        self._cmd_queue: queue.Queue[tuple[str, dict]] = queue.Queue()

        # True while the session dialog is open in "create" mode (vs "join").
        self._pending_create_check: bool = False

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
        if host is None:
            host = self.mq_host_attr.value()
        if session_name is None:
            session_name = self.session_id_attr.value()

        try:
            resolve_host(host)
        except ValueError as e:
            _log(f"connect_to_session aborted: {e}")
            print(f"[OTIOSync] {e}", file=sys.stderr)
            self.popup_message_box("Cannot Connect", str(e))
            return

        self.mq_host_attr.set_value(host)
        self.session_id_attr.set_value(session_name)

        self.disconnect()
        self._poll_stop.clear()
        self.annotation._last_annotation_scan = time.monotonic()

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

        # Grab the current playhead and wire its attribute events ourselves.
        #
        # We deliberately do NOT call PluginBase.subscribe_to_playhead_events().
        # On develop that call is actively harmful here, for two compounding
        # reasons:
        #
        #   1. It calls subscribe_to_global_playhead_events() a SECOND time (see
        #      plugin_base.subscribe_to_playhead_events) on top of our own call in
        #      __init__.  PlayheadGlobalEventsActor delegates both join AND leave
        #      to its single event_group_ (playhead_global_events_actor.cpp:101-105),
        #      so the two routes collapse onto one entry in
        #      BroadcastActor::subscribers_ — the "two callbacks reaching the same
        #      group by different routes" case in xstudio/scratch/
        #      python-event-routing-notes.md, which predates 70aaaa3f.
        #   2. Its __connect_to_playhead calls cleanup_message_handler() on the
        #      previous playhead on every viewport_playhead_atom event.  With one
        #      shared listener actor per connection, that leave revokes the shared
        #      membership our own Playhead objects rely on, leaving their
        #      attribute_changed callbacks registered but permanently silent.
        #
        # Net effect on develop: "Logical Frame"/"playing" events stopped arriving
        # almost immediately, so on_playhead_attribute_changed never ran and NO
        # position or play/stop state was ever broadcast — scrubbing and playback
        # simply did not sync, while selection-driven view-state messages (which
        # take a different path) kept working and masked it.  Confirmed against a
        # two-peer session log: zero "queuing playback state broadcast" lines in
        # either process, every PLAYBACK_SETTINGS_1.0 carrying frame=0.0.
        #
        # _adopt_playhead does the one thing we actually needed from the base call
        # (assign attribute_changed), at every site that acquires a playhead, and
        # never issues the killing leave.
        #
        # ── IF pr/python-per-subscription-listeners LANDS, REDO THIS ──
        # That branch gives every subscription its own listener actor, which
        # changes the ground this workaround stands on in three ways.  Re-verify
        # each rather than assuming the workaround stays correct or stays needed:
        #
        #   * Both reasons above dissolve.  Each subscription gets its own entry
        #     in BroadcastActor::subscribers_, so a leave can only revoke its own
        #     membership — subscribe_to_playhead_events() may become safe to use,
        #     and this hand-rolled ownership could go away.
        #   * Playhead construction stops being free.  Today a duplicate Playhead
        #     collapses onto the connection's one shared listener; there it spawns
        #     a listener actor per subscription.  The "compare the remote key
        #     BEFORE constructing" guard in on_global_playhead_event and the
        #     no-op-on-unchanged-key in _adopt_playhead become load-bearing for
        #     resource use, not just for churn — as does the 1 Hz re-check in
        #     _poll_loop, which would otherwise spawn a listener every second.
        #   * The notes warn the fix REMOVES events callbacks used to receive via
        #     crosstalk.  Playhead attribute events (anon_mail, keyed on the group)
        #     and playhead events (mail, keyed on the owner) currently collapse
        #     onto one owner key; if anything here depends on that overlap it will
        #     go quiet silently.  Diff an event trace, do not eyeball behaviour.
        try:
            self.playback.check_and_update_active_playhead()
        except Exception:
            _log_exc("Could not initialize active playhead at connect time")

        self.display.acquire_annotations_ui()

        # [2C] Subscribe to AnnotationsCore's draw-events group.  It carries
        # both kinds of event we need:
        #
        #   (event_atom, annotation_atom, JsonStore)                 raw draw
        #       interaction — PaintClear, HideDrawings/ShowDrawings, tool changes
        #   (event_atom, annotation_data_atom, JsonStore, Uuid, bool)  the
        #       serialised live stroke, with user_id and stroke_completed
        #
        # stroke_completed=True fires at PaintEnd (pen-up); False fires at
        # PaintStart/PaintPoint (mid-stroke).  This replaces the show_atom
        # hot-scan activation and the 33 ms poll as the primary annotation trigger.
        #
        # Both handlers previously subscribed to the AnnotationsUI and
        # AnnotationsCore *plugin events* groups instead.  PluginBase spawns
        # those without an owner and nothing ever broadcasts on them, so neither
        # handler had ever fired — confirmed by probe against both the develop
        # and per-subscription-listener xStudio builds.  The group used here is
        # the one AnnotationsCore exposes for exactly this purpose, via
        # get_event_group_atom + annotation_atom.
        try:
            self.subscribe_to_annotation_draw_events(self._on_annotation_draw_event)
            _log("Subscribed to AnnotationsCore plugin events [2C]")
        except Exception:
            _log_exc("Could not subscribe to AnnotationsCore draw events")

        # Bookmarks appear and disappear by routes the draw events never see —
        # the notes panel, a script, another plugin — and a clear deletes its
        # bookmark outright.  The bookmarks actor broadcasts add/remove on its
        # event group, so subscribing gives prompt detection for all of them
        # instead of waiting out ANNOTATION_SCAN_INTERVAL.
        try:
            self.join_event_group(
                self.connection.api.session.bookmarks,
                "bookmarks",
                self._on_bookmarks_event,
            )
            _log("Subscribed to bookmarks add/remove events")
        except Exception:
            _log_exc("Could not subscribe to bookmarks events")

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
        # Controllers own their own teardown; each reset() restores that
        # controller's post-construction defaults and releases any event-group
        # subscriptions or cached handles it acquired.
        for controller in (self.media, self.display, self.builder, self.playback,
                           self.structure, self.annotation, self.color):
            controller.reset()
        self._sync_playlists.clear()
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
        """Enqueue self-election when the discovery timeout expires.

        Runs on its own short-lived daemon thread, so it does no manager work
        itself — the poll thread is the manager's single writer.  The status
        read here is only a cheap "is this timeout still relevant" filter; the
        authoritative check happens at drain time in the ``self_elect`` command,
        because a master can answer in the interval between the two.
        """
        time.sleep(self.DISCOVERY_TIMEOUT)
        if self.manager and self.manager.status == STATE_DISCOVERING:
            _log("No master found — queuing self-election")
            self._cmd_queue.put(("self_elect", {}))

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

                # 6.15. Periodic active-playhead re-check (1.0s interval).
                #
                # The event-driven acquisition paths do not cover every way the
                # viewport's playhead can change.  Building a sequence out of a
                # bin, for instance, fires no viewport_playhead_atom (the C++
                # handler early-returns when the viewport's playhead is
                # unchanged) and no selection event — and those two, plus
                # connect-time, were the only things that ever re-checked.  A
                # peer that hit that held the pre-sequence playhead for the rest
                # of the session and silently broadcast no positions at all.
                #
                # This makes the wiring self-healing: _adopt_playhead no-ops when
                # the remote key is unchanged, so the steady-state cost is one
                # bounded actor read per second and no re-subscription.
                if now - self.playback._last_playhead_scan >= 1.0:
                    with _timed("playback.check_playhead"):
                        self.playback.check_and_update_active_playhead()
                    self.playback._last_playhead_scan = now

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
            elif cmd == "self_elect":
                # Discovery timed out with no master.  Registration and election
                # both run here rather than on the timeout thread: they mutate
                # the manager, and build_otio_timelines() reads xStudio actors.
                #
                # Re-check the status — this is the authoritative one.  A peer's
                # I_AM_MASTER processed by manager.tick() during the queue
                # latency (or a leave_session drained ahead of us) leaves the
                # session out of STATE_DISCOVERING, and electing then would make
                # a second master.
                if self.manager and self.manager.status == STATE_DISCOVERING:
                    _log("No master found — self-electing")
                    # Register the current xStudio session as the initial
                    # timeline.  Done here rather than at connect time because
                    # viewed_container fails at startup before any media loads.
                    for tl in self.builder.build_otio_timelines():
                        self.manager.register_timeline(tl)
                    self.manager.elect_self_as_master()
                else:
                    _log("self_elect: no longer discovering — skipping election")
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
            self.manager.elect_self_as_master()

    def _on_synced(self) -> None:
        _log(f"Session reached STATE_SYNCED (master={self.manager.is_master})")
        role = "MASTER" if self.manager.is_master else "CLIENT"
        print(
            f"[OTIOSync] Connected to session '{self.session_id_attr.value()}' "
            f"on {self.mq_host_attr.value()} as {role}",
            file=sys.stderr,
        )
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

    # ── event-group membership ────────────────────────────────────────────────
    # All event-group joins in this plugin go through here.  Two rules, both from
    # xstudio/scratch/python-event-routing-notes.md and both hit in practice:
    #
    #   1. Join each group at most once.  The client shares a single listener
    #      actor per connection, so two subscriptions to one group collapse onto
    #      a single BroadcastActor::subscribers_ entry and the second silences
    #      the first.  Reachable from ordinary use: a sequence Timeline is both a
    #      tracked timeline and, once viewed, the viewed container.  When that
    #      happened, item events stopped 3 s after the sequence was created and
    #      its later edits were never broadcast.
    #   2. Never leave.  A leave revokes that shared entry for *every* callback
    #      in the process.  Resolving (1) by unsubscribing the first subscriber
    #      was strictly worse: the plugin went deaf to playhead, selection and
    #      timeline events alike for the rest of the session.
    #
    # So additional handlers are multiplexed in Python behind one join, and a
    # handler that is finished detaches from the fan-out while the join stays for
    # the life of the connection.  A stale join costs one no-op dispatch.
    #
    # If pr/python-per-subscription-listeners lands upstream, revisit: with a
    # listener per subscription both rules relax, and keeping joins forever stops
    # being free (each would hold its own actor).

    @staticmethod
    def event_group_key(obj) -> "str | None":
        """Address of the actor whose event group *obj* would have us join.

        String form: raw handles are fresh objects per access and never compare
        equal, so two wrappers around one actor must be normalised to one key.
        """
        remote = getattr(obj, "remote", None)
        return str(remote) if remote is not None else None

    def _dispatch_event_group(self, key: str, event) -> None:
        """Fan one group's messages out to every handler registered on it."""
        entry = self._event_group_subs.get(key)
        if not entry:
            return
        for label, cb in list(entry["callbacks"]):
            try:
                cb(event)
            except Exception:
                _log_exc(f"event-group handler {label} raised")

    def join_event_group(self, obj, label: str, cb) -> "str | None":
        """Register *cb* on *obj*'s event group, joining the group at most once.

        :param label: Stable identifier for this handler, used to replace or
            detach it later.  One label per logical subscriber.
        :returns: The group key the handler is attached to, or None on failure.
        """
        key = self.event_group_key(obj)
        if key is None:
            return None
        entry = self._event_group_subs.get(key)
        if entry is not None:
            entry["callbacks"] = [
                (_l, _c) for _l, _c in entry["callbacks"] if _l != label
            ]
            entry["callbacks"].append((label, cb))
            return key
        try:
            sub_id = self.subscribe_to_event_group(
                obj, functools.partial(self._dispatch_event_group, key)
            )
        except Exception:
            _log_exc(f"join_event_group failed for {label}")
            return None
        self._event_group_subs[key] = {"sub_id": sub_id, "callbacks": [(label, cb)]}
        # Report the registry size on every NEW group join. Entries are keyed by
        # actor address and are deliberately never left while connected (a stale
        # join costs one no-op dispatch), so this dict grows by one for every
        # distinct actor we ever subscribe to — most visibly, once per playhead
        # replacement. One stale entry is the accepted trade; an unbounded run of
        # them is not, because every group event then fans out across all of
        # them. Counting here makes that observable without waiting out a long
        # session: the driver is adoption count, not elapsed time, so repeating
        # the actions that replace a playhead (viewport switch, on-screen source
        # change, entering/leaving single-clip isolation) exercises it directly.
        _log(
            f"[event-group] joined {label} on new group {key} — "
            f"{len(self._event_group_subs)} group(s) joined, "
            f"{sum(len(e['callbacks']) for e in self._event_group_subs.values())} "
            "handler(s) total"
        )
        return key

    def detach_event_group_handler(self, key: "str | None", label: str) -> None:
        """Remove one handler from a group's fan-out, keeping the join itself."""
        if not key:
            return
        entry = self._event_group_subs.get(key)
        if not entry:
            return
        entry["callbacks"] = [
            (_l, _c) for _l, _c in entry["callbacks"] if _l != label
        ]

    # ── event handler thin shims ───────────────────────────────────────────────
    # xStudio registers these bound methods on the plugin; they delegate to the
    # appropriate controller so the real logic runs on the correct thread.

    def _on_global_playhead_event(self, event) -> None:
        self.playback.on_global_playhead_event(event)

    def _on_selection_event(self, event) -> None:
        self.playback.on_selection_event(event)

    def _on_test_container_event(self, event) -> None:
        self.structure.on_test_container_event(event)

    def _on_annotation_draw_event(self, event_data, user_id, stroke_completed) -> None:
        self.annotation.on_draw_event(event_data, user_id, stroke_completed)

    def _on_bookmarks_event(self, event) -> None:
        self.annotation.on_bookmarks_event(event)

    def playhead_attribute_changed(self, attr, role) -> None:
        self.playback.on_playhead_attribute_changed(attr, role)

# ── xStudio entry points ───────────────────────────────────────────────────────

def create_plugin_instance(connection):
    return ORISyncPlugin(connection)

if __name__ == "__main__":
    XSTUDIO = Connection(auto_connect=True)
    create_plugin_instance(XSTUDIO)
    XSTUDIO.link.run_xstudio_message_loop()
