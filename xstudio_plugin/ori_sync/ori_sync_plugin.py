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
"""

import sys
import os

# ── path setup ─────────────────────────────────────────────────────────────────

_here = os.path.dirname(os.path.realpath(__file__))
_repo_root = os.path.dirname(os.path.dirname(_here))
_python_dir = os.path.join(_repo_root, "python")
_manifest_dir = os.path.join(_repo_root, "otio_event_plugin")
_manifest_file = os.path.join(_manifest_dir, "plugin_manifest.json")

for _p in (_python_dir, _manifest_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

if os.path.exists(_manifest_file):
    _existing = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
    if _manifest_file not in _existing:
        os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = (
            _existing + os.pathsep + _manifest_file if _existing else _manifest_file
        )

import datetime
import json
import logging
import queue
import threading
import time
import uuid

import opentimelineio as otio
from xstudio.connection import Connection
from xstudio.core import (
    BookmarkDetail,
    LoopMode,
    annotation_atom,
    annotation_data_atom,
    bookmark_detail_atom,
    change_atom,
    event_atom,
    position_atom,
    serialise_atom,
    show_atom,
    viewport_playhead_atom,
    viewport_active_media_container_atom,
    item_atom,
    item_selection_atom,
    item_type_atom,
    selection_actor_atom,
    get_media_atom,
    attribute_value_atom,
    JsonStore,
)
from xstudio.api.session.playhead import Playhead, PlayheadSelection
from xstudio.api.intrinsic.viewport import Viewport
from xstudio.api.session.playlist.timeline import Timeline, create_item_container_from_type
from xstudio.api.session.playlist import Playlist
from xstudio.api.session.playlist.timeline.clip import Clip
from xstudio.api.session.container import Container
from xstudio.api.session.playlist.subset import Subset
from xstudio.api.session.playlist.contact_sheet import ContactSheet
from xstudio.api.session.media.media import Media

from otio_sync_core.manager import (  # noqa: E402
    STATE_DISCOVERING,
    STATE_SYNCED,
    SyncManager,
    sync_event_schema,
)
from otio_sync_core.rabbitmq_network import RabbitMQNetwork  # noqa: E402
from otio_sync_core.xs_annotation_codec import (  # noqa: E402
    xs_strokes_to_sync_events,
    xs_captions_to_sync_events,
    sync_events_to_xs_strokes,
    sync_events_to_xs_captions,
)
from xstudio.plugin import PluginBase  # noqa: E402

# ── logging ────────────────────────────────────────────────────────────────────


def _make_logger() -> logging.Logger:
    logger = logging.getLogger("ori_sync")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d  %(message)s", datefmt="%H:%M:%S")
    log_file = os.environ.get("ORI_SYNC_LOG_FILE")
    if log_file:
        fh = logging.FileHandler(log_file, mode="w")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        # Mirror to stderr so logs appear in the terminal that launched xStudio.
        # Use sys.__stderr__ to bypass xStudio's internal capture of sys.stderr.
        import sys as _sys
        raw_stderr = getattr(_sys, "__stderr__", None) or _sys.stderr
        if raw_stderr is not None:
            eh = logging.StreamHandler(raw_stderr)
            eh.setFormatter(fmt)
            logger.addHandler(eh)
    return logger


_logger = _make_logger()


def _log(msg: str) -> None:
    _logger.debug(msg)


def _log_exc(msg: str) -> None:
    _logger.exception(msg)


def _uri_to_posix_path(uri: str) -> str:
    """Convert a URI or xStudio internal URI string to a POSIX filesystem path.

    Handles the common forms returned by xStudio's ``MediaReference.uri()``:

    * ``file:///path`` → ``/path``
    * ``file://localhost/path`` → ``/path``
    * ``localhost//path`` (xStudio-specific, no ``file:`` scheme) → ``/path``
    * plain ``/path`` → ``/path`` (unchanged)
    """
    import urllib.parse
    if uri.startswith("file:"):
        parsed = urllib.parse.urlparse(uri)
        path = urllib.parse.unquote(parsed.path)
        # file://localhost//path serialises with netloc='localhost' and
        # path='//absolute/path' — normalize the double leading slash.
        if path.startswith("//"):
            path = path[1:]
        return path
    if uri.startswith("localhost//"):
        # xStudio stores local URIs as "localhost//absolute/path"
        return uri[10:]  # strip "localhost/" leaving "/absolute/path"
    return uri


# ── QML folder ─────────────────────────────────────────────────────────────────

_QML_FOLDER = "qml/ORISyncPlugin.1"


# ── plugin ─────────────────────────────────────────────────────────────────────

class ORISyncPlugin(PluginBase):
    """xStudio plugin that joins an ORI Sync session.

    :param connection: xStudio connection object passed by the plugin loader.
    """

    #: How often the poll thread calls manager.tick() (seconds).
    POLL_INTERVAL = 0.033
    #: How long to wait for a master before self-electing (seconds).
    DISCOVERY_TIMEOUT = 2.0
    #: Periodic fallback scan interval (seconds).  show_atom fires when a NEW
    #: bookmark is created but not when the user adds strokes to an existing one
    #: on the same frame.  This scan catches those missed updates.
    ANNOTATION_SCAN_INTERVAL = 1.0

    def __init__(self, connection):
        PluginBase.__init__(
            self,
            connection,
            name="ORI Sync Review",
            qml_folder=_QML_FOLDER,
        )

        # ── connection preferences exposed to the UI ───────────────────────
        self.mq_host_attr = self.add_attribute(
            "MQ Host", "localhost", register_as_preference=True
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

        # ── xStudio handles ────────────────────────────────────────────────
        self.active_playhead: Playhead | None = None
        self.subscribe_to_global_playhead_events(self._on_global_playhead_event)

        # ── runtime state ──────────────────────────────────────────────────
        self.manager: SyncManager | None = None
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None

        # One xStudio (playlist, timeline) per OTIO timeline GUID received from the session.
        # Populated by _do_load_timelines() when we join as a non-master peer.
        self._sync_playlists: dict[str, tuple] = {}

        # Tracks the current OTIO clip-GUID order for the Media track of each
        # synced timeline.  Keyed by tl_guid, value is a list of clip sync-GUIDs
        # in the order they appear in the xStudio timeline track.  Initialised
        # from the OTIO track at load time and kept in sync by
        # _apply_remote_move_child so we never have to query xStudio clip actors.
        self._xs_media_order: dict[str, list] = {}

        # Commands enqueued by xStudio callbacks; drained by poll thread.
        # Items are (command_name, payload_dict).
        self._cmd_queue: queue.Queue[tuple[str, dict]] = queue.Queue()

        # Tracks the xStudio Bookmark created for each (clip_guid, frame) pair
        # so that additional strokes on the same frame can be merged into the
        # existing bookmark rather than creating a new one.
        self._annotation_bookmarks: dict[tuple, object] = {}

        # Cache of parsed stroke and caption dicts (including their Python-side "uuid" keys)
        # for each (clip_guid, frame) pair, allowing non-destructive partial updates.
        self._bookmark_strokes_cache: dict[tuple, list] = {}
        self._bookmark_captions_cache: dict[tuple, list] = {}

        # UUIDs of bookmarks we created from *remote* annotations.
        # show_atom scans skip these so we never re-broadcast them back.
        self._our_bookmark_uuids: set = set()
        self._our_bookmark_uuids_lock = threading.Lock()

        # Monotonic deadline before which show_atom annotation flushes are
        # suppressed.  Set briefly after load_otio reloads (e.g. on move_child)
        # so that xStudio's bookmark-re-trigger burst is not mistaken for new
        # local strokes.
        self._reload_suppress_until: float = 0.0

        # Signature of the last xStudio caption data broadcast per (clip_guid, frame).
        # Compared against the current bookmark on each scan to detect real user edits
        # and avoid re-broadcasting when nothing has changed.  Keyed as
        # "{clip_guid}:{frame}" → JSON string of the captions list.
        self._last_sent_captions: dict[str, str] = {}

        # Maps bookmark UUID → (clip_guid, clip_local_frame) for bookmarks created from
        # remote annotations.  bm.detail.start is the clip-local time, not global sequence
        # time; _resolve_clip_at_frame uses global time and lands on the wrong clip when
        # two media clips share the same clip-local frame number (e.g. cars and coaster
        # both have clip-local frame 199 → both falsely resolve to the cars clip).
        self._our_bookmark_clip_frame: dict[str, tuple[str, int]] = {}

        # Set by _on_annotation_event / show_atom when a local stroke completes.
        # Cleared by _flush_pending_annotations after debounce + broadcast.
        self._annotation_pending_time: float | None = None
        # Timestamp of the last annotation scan (event-triggered or periodic).
        # Used by the fallback scan path so we don't call bookmarks.bookmarks
        # on every 50 ms tick when no events are pending.
        self._last_annotation_scan: float = 0.0
        # Retry counter: incremented when a flush finds unowned bookmarks but
        # annotation_data hasn't been committed yet (reads stale stroke count).
        # Reset to 0 after a successful broadcast or when no bookmarks are pending.
        self._annotation_flush_retries: int = 0

        # Stable UUID cache: maps f"{clip_guid}:{frame}" → [uuid_for_stroke_0, ...]
        # Used so that partial and final broadcasts for the same frame share UUIDs,
        # enabling receivers to update in-place rather than duplicate strokes.
        self._stroke_uuid_cache: dict[str, list] = {}
        # Hot-scan state: after show_atom fires the poll loop scans the active frame
        # on every tick to detect mid-stroke data as soon as xStudio exposes it.
        self._hot_scan_active: bool = False
        self._hot_scan_frame: int | None = None
        self._hot_scan_stroke_counts: dict[str, int] = {}  # f"{clip}:{frame}" → last sent count
        self._hot_scan_point_counts: dict[str, int] = {}  # f"{clip}:{frame}" → last sent point count
        self._hot_scan_last_change: float = 0.0

        # Polling-based scrub detection: last frame seen by the poll loop and
        # last frame applied from a remote PLAYBACK_SETTINGS message.
        # When the poll sees a frame change that matches _last_applied_frame the
        # change came from a remote apply, so we skip re-broadcasting (echo guard).
        self._last_polled_frame: int | None = None
        self._last_applied_frame: int | None = None
        self._last_polled_playing: bool | None = None

        # Last-logged container UUID / selection state; used by
        # _poll_and_broadcast_selection to suppress duplicate log lines.
        self._last_logged_container_uuid: str | None = None
        self._last_logged_clip_name: str | None = None
        # Last clip GUID seen in the viewport (playlist selection or show_atom).
        # Used as a fallback in the annotation broadcast path for flat playlists,
        # where _resolve_clip_at_frame returns None.
        self._last_viewed_clip_guid: str | None = None
        # Deferred seek: when a multi-clip sequence selection is received,
        # the target frame and its deadline are stored here.  Both Form-2
        # viewport_playhead_atom events fire within ~200 ms of the source
        # switch and update active_playhead; the poll loop applies the seek
        # once the deadline passes and the playhead has settled.
        self._pending_seek_frame: int | None = None
        self._pending_seek_deadline: float = 0.0

        # Last display state broadcast; compared each poll tick to detect changes.
        self._last_display_state: dict = {}
        # xStudio's internal viewport scale at the first successful read.  Used
        # to normalise state_.scale_ (which is image_pixels/viewport_pixels, not
        # a zoom multiplier) to RV's convention (1.0 = fit-to-window).
        self._xs_base_scale: float | None = None
        # Last read value of the playhead "Pinned Source Mode" attribute.
        # True = full timeline/sequence view; False = single selected-media view.
        # None on first read (no broadcast on initialisation).
        self._last_pinned_source_mode: bool | None = None
        # Set to True while _apply_selection is writing Pinned Source Mode so
        # the poll loop ignores the resulting attribute-change echo.
        self._applying_pinned_mode: bool = False
        # Monotonic deadline before which show_atom clip-selection broadcasts are
        # suppressed.  Set after _apply_selection calls select_all() to prevent
        # the resulting show_atom burst from echoing individual clip selections
        # back to remote peers.
        self._selection_broadcast_suppress_until: float = 0.0
        # Cached Viewport object; created lazily, cleared on disconnect.
        self._viewport: "Viewport | None" = None
        # Timeline to set as on-screen source once the viewport is ready.
        # Set by _do_load_timelines; consumed and cleared by _get_viewport.
        self._pending_on_screen_source = None
        self._last_selection_scan = 0.0
        self._last_display_scan = 0.0
        self._last_flat_playlist_scan = 0.0
        # Timestamps to throttle log messages during viewport discovery retry loop.
        self._last_timeline_defer_log_time: float = 0.0
        self._last_viewport_error_log_time: float = 0.0

        # Maps tl_guid → (xs_playlist, [media_name_order]) for flat-Playlist
        # timelines built by _build_otio_from_playlist_media.  Only populated on
        # the master; used by _poll_flat_playlist_reorders to detect bin reorders
        # and broadcast MOVE_CHILD to peers.
        self._xs_flat_playlists: dict[str, tuple] = {}

        # Maps tl_guid → (xs_playlist, xs_timeline) for sequence Timelines built
        # by _build_otio_timelines on the master.  Used by _poll_sequence_new_media
        # to detect added clips and broadcast INSERT_CHILD.
        self._xs_sequence_playlists: dict[str, tuple] = {}

        # Maps clip_guid → Media for clips added to flat playlists on this
        # (client) peer via _do_load_timelines or _apply_flat_playlist_insert.
        # Avoids fragile name-based lookups when xStudio uses the full file path
        # as the media name after add_media(path).
        self._flat_clip_to_media: dict = {}

        # Viewport container tracking state: caches whether the active viewport
        # container is a Playlist or Timeline to avoid synchronous API calls in
        # playhead event handlers.
        self._viewport_container_is_playlist: bool = False
        self._viewport_container_is_timeline: bool = False
        # [TEST] subscription ID returned by subscribe_to_event_group for change_atom probe
        self._test_container_sub_id = None

        # [2F] Event-driven clip insertion: subscription IDs keyed by tl_guid.
        # When item_atom fires on a Timeline's event group, tl_guid is added to
        # _timeline_item_dirty so the poll thread can call _poll_sequence_new_media
        # for just that timeline without waiting for the next 0.5 s scan.
        self._timeline_item_sub_ids: dict = {}
        self._timeline_item_dirty: set = set()
        self._timeline_item_lock = threading.Lock()

        # Auto-connect on startup using the current preference values.
        _log("Plugin loaded — auto-connecting to session")
        try:
            self.connect_to_session()
        except Exception:
            _log_exc("connect_to_session failed")

    # ── connection lifecycle ───────────────────────────────────────────────────

    def connect_to_session(self) -> None:
        """Connect to RabbitMQ and join the sync session.

        Safe to call from the xStudio UI thread.
        """
        self.disconnect()
        self._poll_stop.clear()

        host = self.mq_host_attr.value()
        port = int(self.mq_port_attr.value())
        session = self.session_id_attr.value()

        network = RabbitMQNetwork(
            host=host,
            port=port,
            session_id=session,
            self_guid=str(self.uuid),
        )
        self.manager = SyncManager(
            session_id=session,
            self_guid=str(self.uuid),
            network=network,
        )
        self.manager.on_playback_changed(self._apply_playback_state)
        self.manager.on_synced(self._on_synced)
        self.manager.on_status_changed(
            lambda s: self.status_attr.set_value(s)
        )

        # Wait for the consumer queue to be bound before broadcasting
        # WHO_IS_MASTER.  Without this, the I_AM_MASTER response from an
        # existing master can arrive before the queue exists and be lost,
        # causing xStudio to self-elect and end up with two masters.
        if not network.wait_until_ready(timeout=5.0):
            _log("Warning: RabbitMQ consumer did not become ready within 5 s")

        self.manager.start_session()

        # Grab the current playhead so the poll loop can start reading position.
        try:
            ph = self.current_playhead()
            self.active_playhead = ph
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

        # [TEST change_atom] Subscribe to the current viewed container's event
        # group.  If change_atom fires reliably here we can replace the
        # _poll_sequence_new_media poll with an event-driven path.
        try:
            container = self.connection.api.session.viewed_container
            self._test_container_sub_id = self.subscribe_to_event_group(
                container, self._on_test_container_event
            )
            _log(
                f"[TEST change_atom] subscribed to viewed_container events"
                f" (type={type(container).__name__})"
            )
        except Exception:
            _log_exc("[TEST change_atom] subscribe_to_event_group failed")

        # Self-elect if no master answers within DISCOVERY_TIMEOUT.
        threading.Thread(
            target=self._discovery_timeout_task, daemon=True
        ).start()

        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="ori_sync_poll", daemon=True
        )
        self._poll_thread.start()
        # Suppress selection broadcasts for 5 s after connect so the initial
        # playhead position doesn't immediately drive RV's view before the user
        # has intentionally navigated anywhere.
        _log(f"Connecting: session={session!r} mq={host}:{port}")

    def disconnect(self) -> None:
        """Disconnect from the session and stop all background threads."""
        self._poll_stop.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1.0)
        self._poll_thread = None
        if self.manager:
            self.manager.close()
            self.manager = None
        self._viewport = None
        self._pending_on_screen_source = None
        self._last_display_state = {}
        self._xs_base_scale = None
        self._sync_playlists.clear()
        self._xs_flat_playlists.clear()
        self._xs_sequence_playlists.clear()
        self._flat_clip_to_media.clear()
        self._timeline_item_sub_ids.clear()
        with self._timeline_item_lock:
            self._timeline_item_dirty.clear()
        self._last_logged_container_uuid = None
        self._last_logged_clip_name = None
        self._last_viewed_clip_guid = None
        self._pending_seek_frame = None
        self._pending_seek_deadline = 0.0
        self._last_pinned_source_mode = None
        self._applying_pinned_mode = False
        self._selection_broadcast_suppress_until = 0.0
        self.status_attr.set_value("Disconnected")

    def cleanup(self) -> None:
        """Called by xStudio when the plugin is unloaded."""
        self.disconnect()

    # ── discovery ──────────────────────────────────────────────────────────────

    def _discovery_timeout_task(self) -> None:
        """Self-elect as master when the discovery timeout expires."""
        time.sleep(self.DISCOVERY_TIMEOUT)
        if self.manager and self.manager.status == STATE_DISCOVERING:
            _log("No master found — self-electing")
            # Register the current xStudio session as the initial timeline.
            # Done here rather than at connect time because viewed_container
            # fails at startup before any media is loaded.
            for tl in self._build_otio_timelines():
                self.manager.register_timeline(tl)
            self.manager.is_master = True
            self.manager.master_guid = self.manager.self_guid
            self.manager.broadcast_master_response()
            self.manager._set_status(STATE_SYNCED)

    # ── poll loop ──────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Background thread: processes the command queue then polls the manager."""
        while not self._poll_stop.is_set():
            try:
                self._drain_cmd_queue()
                if self.manager:
                    for action, data in self.manager.tick():
                        self._handle_manager_event(action, data)
                self._hot_scan_active_annotation()
                self._flush_pending_annotations()
                self._poll_and_broadcast_frame()
                self._apply_pending_seek()

                # [2F] Event-driven clip insertion: drain timelines flagged by item_atom.
                with self._timeline_item_lock:
                    _dirty_tl_guids = self._timeline_item_dirty.copy()
                    self._timeline_item_dirty.clear()
                for _tl_guid in _dirty_tl_guids:
                    self._poll_sequence_new_media(only_guid=_tl_guid)

                now = time.monotonic()
                if now - self._last_selection_scan >= 0.2:
                    self._poll_and_broadcast_selection()
                    self._last_selection_scan = now
                    
                if now - self._last_display_scan >= 0.5:
                    self._poll_and_broadcast_display()
                    self._last_display_scan = now

                if now - self._last_flat_playlist_scan >= 0.5:
                    self._poll_flat_playlist_reorders()
                    self._poll_flat_playlist_new_media()
                    self._poll_sequence_new_media()
                    self._poll_new_playlists()
                    self._poll_playlist_renames()
                    self._last_flat_playlist_scan = now
            except Exception:
                _log_exc("Poll loop error")
            self._poll_stop.wait(self.POLL_INTERVAL)

    def _drain_cmd_queue(self) -> None:
        """Execute all enqueued commands on the poll thread."""
        qsize = self._cmd_queue.qsize()
        for _ in range(qsize):
            try:
                cmd, payload = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if cmd == "load_timelines":
                    self._do_load_timelines()
            except Exception:
                _log_exc(f"Command {cmd!r} failed")

    # ── manager event dispatch ─────────────────────────────────────────────────

    def _handle_manager_event(self, action: str, data) -> None:
        """React to events returned by manager.tick()."""
        _log(f"Event: {action}")
        if action == "state_request_received":
            requester_guid = data
            _log(f"State request from {requester_guid[:8]} — sending snapshot")
            if not self.manager.root_timeline:
                for tl in self._build_otio_timelines():
                    self.manager.register_timeline(tl)
            # Snapshot current display state so the joiner inherits it.
            current_display = self._read_xs_display_state()
            self.manager.display_state = current_display
            self._last_display_state = dict(current_display)
            self.manager.send_state_snapshot(
                requester_guid,
                playback_state=self._current_playback_state(),
            )

        elif action == "partial_annotation":
            self._apply_partial_annotation_xs(data)

        elif action == "insert_child":
            child_obj = data
            ann_cmds = (
                child_obj.metadata.get("annotation_commands")
                if hasattr(child_obj, "metadata")
                else None
            )
            if ann_cmds:
                self._apply_remote_annotation(child_obj, ann_cmds)
            elif isinstance(child_obj, otio.schema.Clip):
                self._apply_remote_clip_insert(child_obj)

        elif action == "annotation_commands_added":
            # An existing annotation clip had new commands merged into it on
            # the manager side.  Update the corresponding xStudio bookmark with
            # the full merged stroke set.
            merged_clip, _delta_clip = data
            self._refresh_annotation_bookmark(merged_clip)

        elif action == "annotation_commands_replaced":
            # A peer replaced the full annotation_commands list on an existing
            # clip (e.g. in-place text edit).  Re-render the bookmark.
            self._refresh_annotation_bookmark(data)

        elif action == "move_child":
            self._apply_remote_move_child(data)

        elif action == "display_settings":
            self._apply_display_state(data)

        elif action == "selection_changed":
            self._apply_selection(data)

        elif action == "add_timeline":
            # A new sequence/playlist timeline arrived from a remote peer.
            # Reuse _do_load_timelines — it skips GUIDs already in
            # _sync_playlists, so it is safe to call repeatedly.
            # Both master and client create the local playlist/timeline so
            # any peer can receive new timelines regardless of master status.
            self._cmd_queue.put(("load_timelines", {}))

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
            for tl in self._build_otio_timelines():
                self.manager.register_timeline(tl)
            self.manager.is_master = True
            self.manager.master_guid = self.manager.self_guid
            self.manager.broadcast_master_response()
            self.manager._set_status(STATE_SYNCED)

    def _on_synced(self) -> None:
        _log(f"Session reached STATE_SYNCED (master={self.manager.is_master})")
        if not self.manager.is_master:
            # We joined an existing session — create one playlist per received timeline.
            self._cmd_queue.put(("load_timelines", {}))
            if self.manager.display_state:
                self._apply_display_state(self.manager.display_state)

    @staticmethod
    def _fill_source_ranges(otio_tl: otio.schema.Timeline) -> None:
        """Backfill source_range from media available_range on clips where it is absent.

        OTIO semantics allow ``source_range=None`` (meaning "use the full
        available_range of the media reference"), but xStudio's ``load_otio``
        does not honour that convention — it needs an explicit ``source_range``
        to position and size each clip correctly in the track.  Without it
        xStudio reports "Model size of -8" and renders clips as separate
        playlist items rather than a joined sequence.
        """
        for track in otio_tl.tracks:
            for item in track:
                if not isinstance(item, otio.schema.Clip):
                    continue
                if item.source_range is not None:
                    continue
                mr = item.media_reference
                if mr is None:
                    continue
                avail = getattr(mr, "available_range", None)
                if avail is not None:
                    item.source_range = avail

    def _do_load_timelines(self) -> None:
        """Create one xStudio Sequence playlist per OTIO timeline in the snapshot."""
        if not self.manager or not self.manager.timelines:
            _log("Snapshot had no timelines")
            return

        # Defer loading timelines until viewport is acquired to prevent the empty playlist panel race.
        if self._get_viewport() is None:
            now = time.monotonic()
            if now - getattr(self, "_last_timeline_defer_log_time", 0.0) >= 5.0:
                _log("Deferring timeline loading — viewport not ready")
                self._last_timeline_defer_log_time = now
            self._cmd_queue.put(("load_timelines", {}))
            return

        first_xs_timeline = None
        for guid, otio_tl in self.manager.timelines.items():
            if guid in self._sync_playlists:
                continue  # already created

            # Skip creating xStudio playlist/timeline for dynamic clip timelines
            if otio_tl.metadata.get("clip_timeline_for"):
                _log(f"Skipping loading of dynamic clip timeline {otio_tl.name!r} to xStudio")
                continue

            playlist_name = otio_tl.metadata.get("xs_playlist_name") or otio_tl.name or guid[:8]
            timeline_name = otio_tl.name or guid[:8]

            # Backfill source_range so xStudio can position and size each clip.
            self._fill_source_ranges(otio_tl)

            tracks = list(otio_tl.tracks)
            _log(f"OTIO Timeline {timeline_name!r}: {len(tracks)} track(s)")
            for i, track in enumerate(tracks):
                children = list(track)
                _log(f"  Track {i} {track.name!r} kind={track.kind}: {len(children)} child(ren)")
                for j, child in enumerate(children[:8]):
                    sr = getattr(child, "source_range", None)
                    _log(f"    [{j}] {type(child).__name__} {getattr(child, 'name', '?')!r} sr={sr}")

            if otio_tl.metadata.get("xs_flat_playlist"):
                # Flat media-bin Playlist: add each clip by URI so xStudio reads
                # file headers directly (avoids the source_range=None problem).
                try:
                    playlist = self.connection.api.session.create_playlist(playlist_name)[1]
                    added_media: list = []
                    for track in otio_tl.tracks:
                        if track.kind != otio.schema.TrackKind.Video:
                            continue
                        for clip in track:
                            if not isinstance(clip, otio.schema.Clip):
                                continue
                            mr = clip.media_reference
                            if not isinstance(mr, otio.schema.ExternalReference):
                                continue
                            uri = mr.target_url or ""
                            path = _uri_to_posix_path(uri)
                            _log(f"  flat media: uri={uri!r} → path={path!r}")
                            if path:
                                try:
                                    media_obj = playlist.add_media(path)
                                    added_media.append(media_obj)
                                    # Build GUID→Media mapping so selection and
                                    # reorder lookups don't rely on name matching.
                                    clip_guid = clip.metadata.get("sync", {}).get("guid")
                                    if clip_guid and media_obj:
                                        self._flat_clip_to_media[clip_guid] = media_obj
                                except Exception:
                                    _log_exc(f"  Could not add {path!r}")
                    self._sync_playlists[guid] = (playlist, None)
                    if first_xs_timeline is None:
                        first_xs_timeline = playlist
                    _log(f"Created flat playlist {playlist_name!r} from OTIO timeline {guid[:8]}")
                    self._load_snapshot_annotations(otio_tl, playlist)
                except Exception:
                    _log_exc(f"Failed to create flat playlist for {playlist_name!r}")
            else:
                try:
                    playlist = self.connection.api.session.create_playlist(playlist_name)[1]
                    xs_timeline = playlist.create_timeline(timeline_name)[1]
                    otio_str = otio.adapters.write_to_string(otio_tl, "otio_json")
                    xs_timeline.load_otio(otio_str, clear=True)
                    self._sync_playlists[guid] = (playlist, xs_timeline)
                    # Record the OTIO Media-track clip-GUID order so move_children
                    # calls can find the current index without querying xStudio clip actors.
                    media_track = next(
                        (t for t in otio_tl.tracks if t.name == "Media"), None
                    )
                    if media_track is not None:
                        self._xs_media_order[guid] = [
                            c.metadata.get("sync", {}).get("guid")
                            for c in media_track
                            if isinstance(c, otio.schema.Clip)
                        ]
                    if first_xs_timeline is None:
                        first_xs_timeline = xs_timeline
                    _log(f"Created playlist {playlist_name!r} / timeline {timeline_name!r} from OTIO timeline {guid[:8]}")
                    # Convert any annotation clips already in the snapshot to bookmarks.
                    self._load_snapshot_annotations(otio_tl, playlist)
                except Exception:
                    _log_exc(f"Failed to create playlist for {playlist_name!r}")

        if first_xs_timeline is not None:
            # Defer set_on_screen_source until the viewport is ready — calling it
            # before viewport acquisition is ignored by xStudio and the session
            # panel never refreshes.  _get_viewport() applies it on first success.
            self._pending_on_screen_source = first_xs_timeline

    # ── OTIO construction ──────────────────────────────────────────────────────

    def _build_otio_timelines(self) -> list:
        """Convert all xStudio session playlists into OTIO Timelines.

        Enumerates ``session.playlists``.  For each Playlist:

        - If it contains :class:`~xstudio.api.session.playlist.timeline.Timeline`
          children, each is exported via ``to_otio_string()``.
        - If it is a flat media-bin Playlist (no Timeline children), a synthetic
          OTIO Timeline is built from the media items.

        Falls back to ``viewed_container`` when ``session.playlists`` is empty.

        :returns: List of :class:`~opentimelineio.schema.Timeline` objects.
        :rtype: list[opentimelineio.schema.Timeline]
        """
        result: list[otio.schema.Timeline] = []
        try:
            playlists = self.connection.api.session.playlists
        except Exception:
            _log_exc("Could not enumerate session playlists — falling back to viewed_container")
            playlists = []

        for playlist in playlists:
            try:
                containers = playlist.containers
            except Exception:
                _log_exc(f"Could not get containers for playlist {getattr(playlist, 'name', '?')!r}")
                containers = []

            timelines = [c for c in containers if isinstance(c, Timeline)]
            if timelines:
                for xs_tl in timelines:
                    try:
                        if hasattr(xs_tl, "to_otio_string"):
                            otio_str = xs_tl.to_otio_string()
                        else:
                            from xstudio.api.auxiliary.otio import timeline_to_otio_string as _tl_str
                            otio_str = _tl_str(xs_tl)
                        tl = otio.adapters.read_from_string(otio_str)
                        # Use C++ timeline UUID as the persistent sync GUID.
                        tl_guid = str(xs_tl.uuid)
                        tl.metadata.setdefault("sync", {})["guid"] = tl_guid
                        tl.metadata["xs_playlist_name"] = playlist.name

                        import hashlib
                        # Generate deterministic GUIDs for tracks and clips
                        for track_idx, track in enumerate(tl.tracks):
                            track_seed = f"{tl_guid}:{track.kind}:{track_idx}:{track.name}"
                            track_guid = hashlib.sha1(track_seed.encode("utf-8")).hexdigest()
                            track.metadata.setdefault("sync", {})["guid"] = track_guid
                            
                            clip_idx = 0
                            for child in track:
                                if isinstance(child, otio.schema.Clip):
                                    clip_seed = f"{track_guid}:{clip_idx}:{child.name}"
                                    clip_guid = hashlib.sha1(clip_seed.encode("utf-8")).hexdigest()
                                    child.metadata.setdefault("sync", {})["guid"] = clip_guid
                                    clip_idx += 1
                        _log(f"Built OTIO timeline: {tl.name!r} (parent playlist: {playlist.name!r})")
                        result.append(tl)
                        # Store for master-side new-clip polling and _apply_selection.
                        self._xs_sequence_playlists[tl_guid] = (playlist, xs_tl)
                        self._sync_playlists[tl_guid] = (playlist, xs_tl)
                        self._subscribe_timeline_item_events(tl_guid, xs_tl)
                    except Exception:
                        _log_exc(f"Could not export Timeline {getattr(xs_tl, 'name', '?')!r}")
            else:
                tl = self._build_otio_from_playlist_media(playlist)
                if tl is not None:
                    result.append(tl)

        if not result:
            # Fallback for sessions that expose no playlists through the API.
            tl = self._build_otio_from_viewed_container()
            if tl is not None:
                result.append(tl)

        return result

    def _build_otio_from_viewed_container(self) -> otio.schema.Timeline | None:
        """Export the currently-viewed xStudio container as an OTIO Timeline.

        :returns: OTIO Timeline, or None on failure.
        :rtype: opentimelineio.schema.Timeline or None
        """
        try:
            try:
                container = self.connection.api.session.viewed_container
            except RuntimeError as e:
                if "invalid_argument" in str(e):
                    _log("_build_otio_from_viewed_container: no valid viewed_container (session may be empty)")
                    return None
                raise
            if container is None:
                return None
            if hasattr(container, "to_otio_string"):
                otio_str = container.to_otio_string()
            else:
                from xstudio.api.auxiliary.otio import timeline_to_otio_string as _tl_str
                otio_str = _tl_str(container)
            tl = otio.adapters.read_from_string(otio_str)
            _log(f"Built OTIO timeline (viewed_container): {tl.name!r}")
            return tl
        except Exception:
            _log_exc("Could not build OTIO from viewed_container")
            return None

    def _build_otio_from_playlist_media(self, playlist) -> otio.schema.Timeline | None:
        """Build a synthetic OTIO Timeline from a flat Playlist's media items.

        Used when a Playlist has no Timeline containers — i.e. it is a plain
        media-bin.  Clips without a determinable frame count are emitted with
        ``source_range=None``; :meth:`_fill_source_ranges` will propagate the
        ExternalReference ``available_range`` on the receiving side.

        :param playlist: xStudio Playlist object.
        :returns: OTIO Timeline, or None when the playlist has no media.
        :rtype: opentimelineio.schema.Timeline or None
        """
        try:
            media_list = playlist.media
        except Exception:
            _log_exc(f"Could not get media from playlist {getattr(playlist, 'name', '?')!r}")
            return None

        if not media_list:
            return None

        name = getattr(playlist, "name", "Playlist")
        tl = otio.schema.Timeline(name=name)
        # Use C++ playlist UUID as the persistent sync GUID.
        tl_guid = str(playlist.uuid)
        tl.metadata.setdefault("sync", {})["guid"] = tl_guid
        tl.metadata["xs_flat_playlist"] = True
        self._xs_flat_playlists[tl_guid] = (playlist, [m.name for m in media_list])
        # Also register in _sync_playlists so _apply_selection works on the master.
        # xs_timeline is None for flat playlists on the master (no Timeline child exists
        # at build time); _apply_selection only needs the Playlist object.
        self._sync_playlists[tl_guid] = (playlist, None)
        track = otio.schema.Track(name="Video Track", kind=otio.schema.TrackKind.Video)
        import hashlib
        track_seed = f"{tl_guid}:Video:0:Video Track"
        track_guid = hashlib.sha1(track_seed.encode("utf-8")).hexdigest()
        track.metadata.setdefault("sync", {})["guid"] = track_guid

        for media_idx, media in enumerate(media_list):
            try:
                ms = media.media_source()
                mr = ms.media_reference
                uri = str(mr.uri())

                fps = 25.0
                rate_obj = ms.rate
                if rate_obj is not None:
                    try:
                        fps = rate_obj.fps()
                    except Exception:
                        pass

                # Try to get frame count from the UI display info JSON.
                frame_count: int | None = None
                try:
                    info = media.display_info
                    for key in ("frames", "Frames", "frame_count", "num_frames", "duration_frames"):
                        if key in info and info[key]:
                            frame_count = int(info[key])
                            break
                except Exception:
                    pass

                clip_guid = hashlib.sha1(f"{track_guid}:{media_idx}:{media.name}".encode("utf-8")).hexdigest()
                if frame_count is not None:
                    sr = otio.opentime.TimeRange(
                        otio.opentime.RationalTime(0, fps),
                        otio.opentime.RationalTime(frame_count, fps),
                    )
                    clip = otio.schema.Clip(
                        name=media.name,
                        media_reference=otio.schema.ExternalReference(
                            target_url=uri, available_range=sr,
                        ),
                        source_range=sr,
                    )
                else:
                    clip = otio.schema.Clip(
                        name=media.name,
                        media_reference=otio.schema.ExternalReference(target_url=uri),
                    )

                clip.metadata["sync"] = {"guid": clip_guid}
                self._flat_clip_to_media[clip_guid] = media
                track.append(clip)
                _log(f"  Flat media clip: {media.name!r} fps={fps} frames={frame_count}")
            except Exception:
                _log_exc(f"Could not convert media {getattr(media, 'name', '?')!r} to OTIO clip")

        clips = list(track)
        if not clips:
            return None

        tl.tracks.append(track)
        _log(f"Built synthetic OTIO timeline for flat playlist {name!r}: {len(clips)} clip(s)")
        return tl

    # ── playback sync ──────────────────────────────────────────────────────────

    def _on_global_playhead_event(self, event) -> None:
        """Track the on-screen playhead and detect locally-drawn annotations.

        PlayheadGlobalEventsActor broadcasts several shapes:
        - ``(event_atom, viewport_playhead_atom, playhead_actor)`` — Form 1
        - ``(event_atom, viewport_playhead_atom, viewport_name, playhead_actor)`` — Form 2
        - ``(event_atom, show_atom, UuidActor, UuidActor, str, int)`` — bookmark shown
          (fires when the user draws a stroke and when bookmarks are displayed)
        """
        if not (len(event) >= 2 and isinstance(event[0], event_atom)):
            return

        # show_atom: fires when a bookmark/annotation is shown or created,
        # or when the active on-screen media item changes.
        if isinstance(event[1], show_atom):
            _shape = f"len={len(event)} types=[{', '.join(type(e).__name__ for e in event)}]"
            is_bookmark_shown = len(event) == 6 and isinstance(event[5], int)
            if not is_bookmark_shown and len(event) >= 5 and hasattr(event[2], 'uuid'):
                # On-screen media changed
                media_ua = event[2]
                media_uuid_str = str(media_ua.uuid)
                is_playlist = getattr(self, "_viewport_container_is_playlist", False)
                is_timeline = getattr(self, "_viewport_container_is_timeline", False)
                _container_label = "playlist" if is_playlist else ("timeline" if is_timeline else "unknown")
                _media_name_hint = None
                for _pl, _ in self._sync_playlists.values():
                    try:
                        for _m in _pl.media:
                            if str(_m.uuid) == media_uuid_str:
                                _media_name_hint = _m.name
                                break
                    except Exception:
                        pass
                    if _media_name_hint:
                        break
                _log(f"[SEL] show_atom media-change: name={_media_name_hint!r} uuid={media_uuid_str[:8]} container={_container_label} raw={_shape}")
                # Suppress clip broadcasts while the viewport is confirmed to be
                # showing a Timeline.  In Timeline mode the playhead fires show_atom
                # as it scans through clips in the sequence; broadcasting those would
                # push RV out of sequence view on every frame advance.
                if is_timeline or time.monotonic() < self._selection_broadcast_suppress_until:
                    _log(f"[SEL] → suppressed (timeline/sequence mode)")
                    return
                if (_media_name_hint and self.manager
                        and self.manager.status == STATE_SYNCED):
                    clip_guid = self._clip_guid_for_media_name(_media_name_hint)
                    if clip_guid:
                        self._last_viewed_clip_guid = clip_guid
                        clip_tl_guid = self.manager.get_or_create_clip_timeline(clip_guid)
                        if clip_tl_guid:
                            self.manager.active_timeline_guid = clip_tl_guid
                        self.manager.broadcast_selection(clip_guid, view_mode="source")
                        _log(f"[SEL] → broadcast clip {clip_guid[:8]}")
                    else:
                        _log(f"[SEL] → no clip_guid found for {_media_name_hint!r}")
                return

            if time.monotonic() < self._reload_suppress_until:
                return
            _log(f"[SEL] show_atom (annotation/bookmark): {_shape} — queuing annotation flush")
            if self.manager and self.manager.status == STATE_SYNCED:
                self._annotation_pending_time = time.monotonic()
                # [2C] Hot scan is now activated by _on_core_annotation_event
                # (PaintStart/PaintPoint events from AnnotationsCore).  Keep this
                # as a fallback for builds that don't have the [2C] broadcast.
                if not self._hot_scan_active:
                    try:
                        if self.active_playhead:
                            self._hot_scan_frame = self.active_playhead.position
                            self._hot_scan_active = True
                            self._hot_scan_last_change = time.monotonic()
                            _log(f"Hot scan activated at frame {self._hot_scan_frame} (show_atom fallback)")
                    except Exception:
                        pass
            return

        if not isinstance(event[1], viewport_playhead_atom):
            return
        # Only Form 2 carries a reliable playhead: (event_atom, viewport_playhead_atom,
        # viewport_name, playhead_actor).  Form 1 (len==3) omits the viewport name and
        # its playhead actor may differ from the one the user is actually scrubbing.
        if len(event) <= 3:
            _log(f"viewport_playhead_atom Form-1 (ignored): len={len(event)}")
            return
        ph_remote = event[3]
        try:
            self.active_playhead = Playhead(self.connection, ph_remote)
            _log(f"[SEL] viewport_playhead_atom Form-2: active playhead updated viewport={event[2]!r}")
        except Exception:
            _log_exc("_on_global_playhead_event: failed to update playhead")

        # [TEST position_atom] Subscribe to this playhead's position events.
        # If position_atom fires reliably (even across timeline switches) we can
        # replace the poll-based frame detection with an event-driven path.
        try:
            self.subscribe_to_playhead_events(ph_remote, self._on_test_position_event)
            _log("[TEST position_atom] subscribed to playhead events")
        except Exception:
            _log_exc("[TEST position_atom] subscribe_to_playhead_events failed")

    def _on_test_position_event(self, event) -> None:
        """[TEST] Fires if subscribe_to_playhead_events + position_atom works."""
        if (
            len(event) > 2
            and isinstance(event[0], event_atom)
            and isinstance(event[1], position_atom)
        ):
            _log(f"[TEST position_atom] FIRED frame={event[2]}")

    def _on_test_container_event(self, event) -> None:
        """[TEST] Fires if subscribe_to_event_group + change_atom works."""
        t1 = type(event[1]).__name__ if len(event) > 1 else "n/a"
        is_change = len(event) > 1 and isinstance(event[1], change_atom)
        _log(f"[TEST change_atom] event: len={len(event)}, t1={t1}, is_change_atom={is_change}")

    def _subscribe_timeline_item_events(self, tl_guid: str, xs_tl) -> None:
        """Subscribe to *xs_tl*'s event group to receive item_atom notifications.

        Called whenever a new sequence Timeline is registered.  Stores the
        subscription ID in ``_timeline_item_sub_ids`` so duplicates are skipped.

        :param tl_guid: Sync GUID identifying the timeline in the manager.
        :param xs_tl: The xStudio Timeline object whose event group to join.
        """
        if tl_guid in self._timeline_item_sub_ids:
            return
        try:
            import functools
            cb = functools.partial(self._on_timeline_item_event, tl_guid)
            sub_id = self.subscribe_to_event_group(xs_tl, cb)
            self._timeline_item_sub_ids[tl_guid] = sub_id
            _log(f"[2F] subscribed to item_atom events for timeline {tl_guid[:8]}")
        except Exception:
            _log_exc(f"[2F] subscribe_to_event_group failed for timeline {tl_guid[:8]}")

    def _on_timeline_item_event(self, tl_guid: str, event) -> None:
        """Handle item_atom events from a tracked Timeline's event group.

        Marks *tl_guid* as dirty so the next poll-thread tick calls
        ``_poll_sequence_new_media`` for that timeline immediately rather than
        waiting for the next 0.5 s fallback scan.

        :param tl_guid: Sync GUID of the timeline that fired the event.
        :param event: Event tuple from xStudio's CAF message bus.
        """
        if not (len(event) > 1 and isinstance(event[0], event_atom)
                and isinstance(event[1], item_atom)):
            return
        hidden = event[3] if len(event) > 3 else False
        if hidden:
            return
        _log(f"[2F] item_atom fired for timeline {tl_guid[:8]} — queuing clip check")
        with self._timeline_item_lock:
            self._timeline_item_dirty.add(tl_guid)

    def _poll_and_broadcast_frame(self) -> None:
        """Broadcast the local playhead position when the user scrubs.

        Called from the poll thread on every tick.  Reads position and play
        state directly from ``active_playhead`` so it works regardless of
        whether xStudio event subscriptions are delivering events.

        Echo guard: when the poll loop itself applied a remote frame to the
        local playhead, ``_last_applied_frame`` records that frame.  If the
        polled position equals ``_last_applied_frame`` the change was caused
        by the remote apply, not by local user interaction, and we skip the
        broadcast to avoid an echo loop.
        """
        if not self.manager or self.manager.status != STATE_SYNCED:
            return
        if not self.active_playhead:
            # Retry lazy init — xStudio may not have had an active playhead at
            # connect time (e.g. no media loaded yet).
            try:
                self.active_playhead = self.current_playhead()
            except Exception:
                return
        try:
            playing: bool = self.active_playhead.playing
            frame: int = self.active_playhead.position
            fps: float = self.active_playhead.frame_rate.fps() or 25.0
        except Exception:
            return

        # Initialize play state on first run
        if self._last_polled_playing is None:
            self._last_polled_playing = playing
            self._last_polled_frame = frame
            return

        playing_changed = (playing != self._last_polled_playing)

        # Skip polling frame updates while actively playing if there's no state transition
        if playing and not playing_changed:
            return

        # Check frame/scrub changes if paused
        if not playing and not playing_changed:
            if frame == self._last_polled_frame:
                return
            self._last_polled_frame = frame
            if frame == self._last_applied_frame:
                # This change was caused by _apply_playback_state — skip re-broadcast.
                return

        broadcast_frame = frame
        # xStudio's async position write may not have settled yet; a negative
        # frame means the playhead hasn't reached the clip yet.
        if broadcast_frame < 0:
            return

        state = {
            "playing": playing,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(broadcast_frame),
                "rate": fps,
            },
            "looping": False,
        }

        # Update cache to prevent echo loops
        self._last_polled_playing = playing
        self._last_polled_frame = frame

        _log(f"Poll: broadcasting playback playing={playing} frame={frame} fps={fps}")
        self.manager.broadcast_playback_state(state)

    def _apply_pending_seek(self) -> None:
        """Apply a deferred sequence-playhead seek once its deadline has passed.

        After a remote clip-selection triggers ``set_on_screen_source``, xStudio
        fires two ``viewport_playhead_atom`` Form-2 events roughly 200 ms apart.
        Each one updates ``active_playhead`` via ``_on_global_playhead_event``.
        By waiting 300 ms before seeking we ensure the final, settled playhead
        actor is in place and its duration has been resolved — without needing a
        separate thread, a blocking timeout, or a retry loop.
        """
        if self._pending_seek_frame is None:
            return
        if time.monotonic() < self._pending_seek_deadline:
            return
        frame = self._pending_seek_frame
        self._pending_seek_frame = None
        if not self.active_playhead:
            return
        try:
            self.active_playhead.position = frame
            _log(f"Deferred seek: applied frame {frame}")
        except Exception:
            _log_exc(f"Deferred seek: failed at frame {frame}")

    def _poll_and_broadcast_selection(self) -> None:
        """Log xStudio viewport container and selection state on every change."""
        try:
            session_actor = self.connection.api.session.remote
            result = self.connection.request_receive_timeout(
                100, session_actor, viewport_active_media_container_atom()
            )[0]
            container_uuid = str(result.uuid)
            c = Container(self.connection, result.actor)
            try:
                c_type = c.type
            except RuntimeError as re:
                if "invalid_argument" in str(re):
                    return
                raise

            if c_type == "Timeline":
                container = Timeline(self.connection, result.actor, result.uuid)
            elif c_type == "Subset":
                container = Subset(self.connection, result.actor, result.uuid)
            elif c_type == "ContactSheet":
                container = ContactSheet(self.connection, result.actor, result.uuid)
            else:
                container = Playlist(self.connection, result.actor, result.uuid)

            is_timeline = isinstance(container, Timeline)
            is_playlist = isinstance(container, Playlist)
            self._viewport_container_is_playlist = is_playlist
            self._viewport_container_is_timeline = is_timeline

            clip_name = None
            if is_timeline:
                try:
                    selected_items = container.selection
                    sel_names = [f"{getattr(i, 'name', '')} ({type(i).__name__})" for i in selected_items]
                    if getattr(self, "_last_sel_names", None) != sel_names:
                        _log(f"[SEL] Timeline.selection changed: {sel_names}")
                        self._last_sel_names = sel_names
                    for item in selected_items:
                        if type(item).__name__ == "Clip":
                            clip_name = getattr(item, "name", None)
                            break
                except Exception:
                    _log_exc("[SEL] Timeline.selection poll failed")
            elif is_playlist:
                try:
                    sel = container.playhead_selection
                    selected_sources = sel.selected_sources
                    src_names = [s.name for s in selected_sources]
                    if getattr(self, "_last_src_names", None) != src_names:
                        _log(f"[SEL] Playlist.playhead_selection changed: {src_names}")
                        self._last_src_names = src_names
                    if selected_sources:
                        clip_name = selected_sources[0].name
                except Exception:
                    _log_exc("[SEL] Playlist.playhead_selection poll failed")

            if (container_uuid != self._last_logged_container_uuid
                    or clip_name != self._last_logged_clip_name):
                _log(f"[SEL] container={c_type} uuid={container_uuid[:8]} clip={clip_name!r}")
                self._last_logged_container_uuid = container_uuid
                self._last_logged_clip_name = clip_name

            # Update annotation fallback: flat-playlist path needs to know what clip
            # is currently viewed when _resolve_clip_at_frame returns None.
            if clip_name and self.manager:
                cg = self._clip_guid_for_media_name(clip_name)
                if cg:
                    self._last_viewed_clip_guid = cg

            # Detect Pinned Source Mode transitions: False→True means the user
            # returned to sequence/timeline view without going through RV.
            if (self.active_playhead
                    and not self._applying_pinned_mode
                    and self.manager
                    and self.manager.status == STATE_SYNCED):
                try:
                    psm_attr = self.active_playhead.get_attribute("Pinned Source Mode")
                    if psm_attr is not None:
                        psm = psm_attr.value()
                        if (self._last_pinned_source_mode is not None
                                and psm != self._last_pinned_source_mode):
                            _log(f"[SEL] Pinned Source Mode: {self._last_pinned_source_mode} → {psm}")
                            if psm is True:
                                # User re-pinned to the timeline — broadcast clear so
                                # peers exit single-clip mode too.
                                seq_tl_guid = self.manager.sequence_timeline_guid
                                if seq_tl_guid:
                                    self.manager.active_timeline_guid = seq_tl_guid
                                self.manager.broadcast_selection("")
                                _log("[SEL] → broadcast selection clear (returned to sequence view)")
                        self._last_pinned_source_mode = psm
                except Exception:
                    _log_exc("[SEL] Pinned Source Mode poll failed")

        except Exception as e:
            _log_exc(f"[SEL] poll failed: {e}")

    def _current_playback_state(self) -> dict | None:
        """Return the local playback state dict for inclusion in a state snapshot."""
        if not self.active_playhead:
            return None
        try:
            frame = self.active_playhead.position
            fps = self.active_playhead.frame_rate.fps() or 25.0
            playing = self.active_playhead.playing
            return {
                "playing": playing,
                "current_time": {
                    "OTIO_SCHEMA": "RationalTime.1",
                    "value": float(frame),
                    "rate": fps,
                },
                "looping": False,
            }
        except Exception:
            return None

    def _get_local_viewed_timeline_guid(self) -> str | None:
        """Query the active container from the viewport and map it to its sync GUID.

        :returns: GUID string, or ``None`` if it cannot be resolved.
        :rtype: str or None
        """
        if not self.manager:
            return None
        try:
            session_actor = self.connection.api.session.remote
            result = self.connection.request_receive_timeout(
                100, session_actor, viewport_active_media_container_atom()
            )[0]
            container_uuid = str(result.uuid)
            c = Container(self.connection, result.actor)
            c_type = c.type
        except Exception:
            return None

        if c_type == "Timeline":
            # Check if this container UUID is one of our synced sequence timelines.
            for tl_guid, (pl, xs_tl) in self._sync_playlists.items():
                if xs_tl and str(xs_tl.uuid) == container_uuid:
                    return tl_guid
            return container_uuid
        else:
            # Viewing a Playlist (or Subset/ContactSheet).
            # Check if it's a flat playlist.
            for tl_guid, (pl, xs_tl) in self._sync_playlists.items():
                if xs_tl is None and str(pl.uuid) == container_uuid:
                    return tl_guid

            # Check if we are viewing a sequence's parent playlist (source view of a clip).
            matching_pl = None
            for tl_guid, (pl, xs_tl) in self._sync_playlists.items():
                if str(pl.uuid) == container_uuid:
                    matching_pl = pl
                    break

            if matching_pl:
                try:
                    sel = matching_pl.playhead_selection
                    selected_sources = sel.selected_sources
                    if len(selected_sources) == 1:
                        clip_guid = self._clip_guid_for_media_name(selected_sources[0].name)
                        if clip_guid:
                            return self.manager.get_or_create_clip_timeline(clip_guid)
                except Exception:
                    pass
                return self.manager.sequence_timeline_guid

            return container_uuid

    def _apply_playback_state(self, state: dict) -> None:
        """Apply an incoming playback state dict to the local xStudio playhead.

        Called from the poll thread via the ``on_playback_changed`` callback.
        xStudio's actor-based attribute writes are thread-safe.

        Updates ``_last_applied_frame``, ``_last_polled_frame``, and
        ``_last_polled_playing`` so that ``_poll_and_broadcast_frame``
        recognises the resulting changes as remote applies and does not
        echo them back to the session.
        """
        if not self.active_playhead:
            return

        incoming_tl_guid = state.get("timeline_guid")
        if incoming_tl_guid and self.manager:
            # Check against target active_timeline_guid first (handles the selection change transition)
            if incoming_tl_guid != self.manager.active_timeline_guid:
                # Query actual viewed container GUID as a fallback in case active_timeline_guid is transitioning
                local_tl_guid = self._get_local_viewed_timeline_guid()
                if local_tl_guid and local_tl_guid != incoming_tl_guid:
                    _log(f"RECV playback state: mismatched timeline_guid (local={local_tl_guid[:8]}, "
                         f"target={self.manager.active_timeline_guid[:8]}, incoming={incoming_tl_guid[:8]}) — ignoring")
                    return

        playing = state.get("playing", False)
        current_time = state.get("current_time", {})
        # Protocol value is 0-based (RV sends frame-1; xStudio frames are 0-based).
        frame = max(0, int(current_time.get("value", 0)))

        playing_changed = (playing != self.active_playhead.playing)

        # Update cache to prevent poll loop from echoing back this change
        self._last_polled_playing = playing

        if playing_changed:
            self.active_playhead.playing = playing

        # Apply position if we are paused, or if the play/pause state has transitioned
        if not playing or playing_changed:
            self._last_applied_frame = frame
            self._last_polled_frame = frame
            self.active_playhead.position = frame

    # ── display state ──────────────────────────────────────────────────────────

    # xStudio's ColourPipeline.channel attribute uses these string values.
    # Map to/from the protocol's single-letter convention.
    _XS_TO_PROTO_CHANNEL = {
        "RGB": "RGBA", "RGBA": "RGBA",
        "Red": "R", "Green": "G", "Blue": "B", "Alpha": "A",
        "R": "R", "G": "G", "B": "B", "A": "A",
    }
    _PROTO_TO_XS_CHANNEL = {
        "RGBA": "RGB", "R": "Red", "G": "Green", "B": "Blue", "A": "Alpha",
    }

    def _get_viewport(self) -> "Viewport | None":
        """Return a cached Viewport for the active xStudio window, or None on error."""
        if self._viewport is not None:
            if self._pending_on_screen_source is not None:
                try:
                    self.connection.api.session.set_on_screen_source(
                        self._pending_on_screen_source
                    )
                    _log(f"Applied deferred on-screen source: {getattr(self._pending_on_screen_source, 'name', '?')}")
                except Exception:
                    pass
                self._pending_on_screen_source = None
            return self._viewport
        try:
            self._viewport = Viewport(self.connection, active_viewport=True)
            _log("Viewport acquired")
        except Exception as e:
            now = time.monotonic()
            if now - getattr(self, "_last_viewport_error_log_time", 0.0) >= 5.0:
                _log(f"_get_viewport: {e}")
                self._last_viewport_error_log_time = now
            return self._viewport
        # Viewport just became available — apply any deferred on-screen source
        # so the session panel refreshes to show all loaded playlists.
        if self._pending_on_screen_source is not None:
            try:
                self.connection.api.session.set_on_screen_source(
                    self._pending_on_screen_source
                )
                _log(f"Applied deferred on-screen source: {getattr(self._pending_on_screen_source, 'name', '?')}")
            except Exception:
                pass
            self._pending_on_screen_source = None
        return self._viewport

    def _read_xs_display_state(self) -> dict:
        """Return a display state dict read from the active xStudio viewport.

        Uses ``Viewport.colour_pipeline`` for exposure and channel.  Zoom
        (scale) is read via ``serialise_atom`` and normalised against the
        fit-to-window baseline.  Pan is always ``None`` — xStudio's internal
        ``translate_`` is in image-space units incompatible with RV's
        normalised translation, and applying them causes a ~50% pan jump on
        join.  Pan sync requires ``viewport_pan_atom`` in ``py_atoms.cpp``.
        """
        state: dict = {
            "pan": None,
            "zoom": None,
            "exposure": 0.0,
            "channel": "RGBA",
        }
        vp = self._get_viewport()
        if vp is None:
            return state

        try:
            cp = vp.colour_pipeline
            state["exposure"] = float(cp.exposure.value())
        except Exception as e:
            _log(f"_read_xs_display_state: exposure read failed: {e}")

        try:
            cp = vp.colour_pipeline
            xs_ch = cp.channel.value()
            state["channel"] = self._XS_TO_PROTO_CHANNEL.get(str(xs_ch), "RGBA")
        except Exception as e:
            _log(f"_read_xs_display_state: channel read failed: {e}")

        vp_state = None
        try:
            js = self.connection.request_receive_timeout(
                100, vp.remote, serialise_atom()
            )[0]
            vp_state = json.loads(js.dump())["base"]
            raw_scale = float(vp_state["scale"])
            # state_.scale_ is image_pixels/viewport_pixels — a larger value
            # means more zoomed OUT (opposite of RV's convention).  Normalise
            # to RV's 1.0 = fit-to-window by recording the first-seen scale as
            # the baseline and dividing subsequent values by it.
            if self._xs_base_scale is None and raw_scale > 0.0:
                self._xs_base_scale = raw_scale
                _log(f"xStudio base scale set to {raw_scale:.4f}")
            if self._xs_base_scale:
                state["zoom"] = raw_scale / self._xs_base_scale
            else:
                state["zoom"] = 1.0
        except Exception as e:
            _log(f"_read_xs_display_state: zoom read failed: {e}")
        # Pan is intentionally left as None.
        # xStudio's state_.translate_ is in internal image-space units that are
        # not compatible with RV's normalised translation coordinates.  Sending
        # the raw translate values causes RV to jump ~50% on join.  Pan sync
        # requires viewport_pan_atom to be exposed in py_atoms.cpp (see TODO).

        return state

    def _playhead_for_clip(self, clip_guid: str) -> "Playhead | None":
        """Return the xStudio Playhead for the sequence playlist that contains *clip_guid*.

        Iterates ``_sync_playlists`` (which holds only the sequence-level
        playlists created by :meth:`_do_load_timelines`) and checks each
        OTIO timeline's media track for the clip.  This avoids relying on the
        OTIO parent chain, which may point at the clip-timeline copy rather
        than the original sequence clip.

        Falls back to ``None`` so the caller can fall back to
        ``self.active_playhead``.
        """
        if not self.manager:
            return None
        try:
            for tl_guid, (playlist, _) in self._sync_playlists.items():
                otio_tl = self.manager.timelines.get(tl_guid)
                if otio_tl is None:
                    continue
                for track in otio_tl.tracks:
                    for child in track:
                        if child.metadata.get("sync", {}).get("guid") == clip_guid:
                            ph = playlist.playhead
                            _log(f"_playhead_for_clip: {clip_guid[:8]} found in tl={tl_guid[:8]} → playhead ok")
                            return ph
            _log(f"_playhead_for_clip: {clip_guid[:8]} not found in any sequence timeline")
            return None
        except Exception:
            _log_exc("_playhead_for_clip: exception")
            return None

    def _apply_selection(self, data: dict) -> None:
        """Apply a remotely broadcast clip selection.

        Switches the viewed container to the sequence's parent playlist and sets
        the playlist playhead selection to the targeted clip (mimicking RV source view).
        If selection is cleared, switches back to the sequence timeline and selects all.
        """
        if not self.active_playhead:
            return
        clip_guid = data.get("clip_guid", "")
        view_mode = data.get("view_mode", "source")

        if not clip_guid:
            # Clear / container switch.
            _log(f"RECV selection: clear → {'sequence' if view_mode == 'sequence' else 'source/playlist'} view (mode={view_mode})")
            if self.manager:
                seq_tl_guid = self.manager.sequence_timeline_guid
                if seq_tl_guid:
                    self.manager.active_timeline_guid = seq_tl_guid
                    if seq_tl_guid in self._sync_playlists:
                        pl, tl = self._sync_playlists[seq_tl_guid]
                        try:
                            # Switch viewed_container and on_screen_source based on view_mode.
                            viewed_c = tl if (view_mode == "sequence" and tl is not None) else pl
                            self.connection.api.session.viewed_container = viewed_c
                            
                            # Update the viewport source
                            if view_mode == "sequence" and tl:
                                self.connection.api.session.set_on_screen_source(tl)
                                _log("RECV selection clear: set_on_screen_source to timeline (Sequence)")
                                try:
                                    from xstudio.core import UuidActorVec, item_selection_atom
                                    self.connection.send(tl.remote, item_selection_atom(), UuidActorVec())
                                except Exception:
                                    pass
                                # Restore sequence view: pinnedSourceMode=True pins the playhead
                                # to the full timeline rather than any single selected media item.
                                if self.active_playhead:
                                    try:
                                        self._applying_pinned_mode = True
                                        self.active_playhead.set_attribute("Pinned Source Mode", True)
                                        self._last_pinned_source_mode = True
                                        _log("RECV selection clear: set Pinned Source Mode = True")
                                    except Exception:
                                        _log_exc("RECV selection: failed to set Pinned Source Mode")
                                    finally:
                                        self._applying_pinned_mode = False
                            else:
                                self.connection.api.session.set_on_screen_source(pl)
                                _log("RECV selection clear: set_on_screen_source to playlist (Source)")

                            pl.playhead_selection.select_all()
                            # select_all() fires show_atom for every media item in the
                            # playlist.  Suppress those for 2 s so the echo doesn't
                            # push peers back into single-clip mode.
                            self._selection_broadcast_suppress_until = time.monotonic() + 2.0
                        except Exception:
                            _log_exc("RECV selection clear: failed to switch container")

                        # active_playhead is refreshed by the Form-2 viewport_playhead_atom
                        # event that fires after set_on_screen_source completes.
            return

        # Skip if we already broadcast this same clip — this is an echo from RV
        if not self.manager:
            return
        clip = self.manager._object_map.get(clip_guid)
        if clip is None or not isinstance(clip, otio.schema.Clip):
            _log(f"RECV selection: guid={clip_guid} not found in object_map")
            return
        _log(f"RECV selection: clip '{clip.name}' guid={clip_guid[:8]} mode={view_mode}")

        # Switch active_timeline_guid to the clip's own single-clip timeline.
        clip_tl_guid = self.manager.get_or_create_clip_timeline(clip_guid)
        if clip_tl_guid:
            self.manager.active_timeline_guid = clip_tl_guid

        # Find the best playlist to use for switching the viewport.
        # Strategy:
        #   Pass 1 — look for a single-clip individual playlist whose OTIO clip
        #            name matches.  On the host, individual clip playlists may
        #            carry a different clip GUID than the sequence clip (they are
        #            exported from separate xStudio Timeline objects), so name
        #            matching is required.  set_on_screen_source on a single-clip
        #            Timeline reliably fires show_atom.
        #   Pass 2 — GUID-based fallback: the first playlist whose OTIO contains
        #            a clip with the target GUID (covers flat playlists and any
        #            case where no individual playlist exists).  Uses the classic
        #            viewed_container + set_selection path which works for flat
        #            playlists.
        clip_name = getattr(clip, "name", "")
        clip_stem = os.path.splitext(os.path.basename(clip_name))[0]

        playlist = None
        playlist_xs_tl = None
        use_source = False  # True → set_on_screen_source; False → set_selection

        if view_mode == "source":
            for tl_guid, (pl, xs_tl) in self._sync_playlists.items():
                otio_tl = self.manager.timelines.get(tl_guid)
                if otio_tl is None:
                    continue
                video_clips = [
                    c for t in otio_tl.tracks
                    if t.kind == otio.schema.TrackKind.Video
                    for c in t if isinstance(c, otio.schema.Clip)
                ]
                if len(video_clips) != 1:
                    continue
                cname = video_clips[0].name or ""
                if (cname == clip_name
                        or os.path.splitext(os.path.basename(cname))[0] == clip_stem):
                    playlist = pl
                    playlist_xs_tl = xs_tl
                    use_source = True
                    _log(f"RECV selection: matched individual playlist "
                         f"{getattr(pl, 'name', '?')!r} for clip {clip_guid[:8]} ({clip_name!r})")
                    break

        matched_tl_guid = None  # set during pass-2 fallback
        if playlist is None:
            for tl_guid, (pl, xs_tl) in self._sync_playlists.items():
                otio_tl = self.manager.timelines.get(tl_guid)
                if otio_tl is None:
                    continue
                for track in otio_tl.tracks:
                    for child in track:
                        if child.metadata.get("sync", {}).get("guid") == clip_guid:
                            playlist = pl
                            playlist_xs_tl = xs_tl
                            matched_tl_guid = tl_guid
                            break
                    if playlist:
                        break
                if playlist:
                    break

        if playlist is not None:
            # Decide which switching mechanism to use.
            # use_source=True  → pass-1 single-clip individual playlist found.
            # multi-clip seq   → set_on_screen_source + seek to clip start frame.
            # flat playlist    → viewed_container + set_selection (still works for those).
            is_multi_clip = False
            if not use_source and matched_tl_guid is not None and playlist_xs_tl is not None and view_mode == "sequence":
                otio_tl = self.manager.timelines.get(matched_tl_guid)
                if otio_tl is not None:
                    n_video = sum(
                        1 for t in otio_tl.tracks
                        if t.kind == otio.schema.TrackKind.Video
                        for c in t if isinstance(c, otio.schema.Clip)
                    )
                    is_multi_clip = n_video > 1

            try:
                # Switch the viewed container in the sidebar.
                # If we are in sequence view and have a timeline, view the timeline. Otherwise view the playlist.
                viewed_c = playlist_xs_tl if (view_mode == "sequence" and playlist_xs_tl is not None) else playlist
                self.connection.api.session.viewed_container = viewed_c

                if use_source and playlist_xs_tl is not None:
                    # Single-clip individual playlist: just show it.
                    self.connection.api.session.set_on_screen_source(playlist_xs_tl)
                    _log(f"RECV selection: set_on_screen_source (individual) → "
                         f"{getattr(playlist_xs_tl, 'name', '?')!r}")
                elif is_multi_clip:
                    # Multi-clip sequence: seek the playhead after the source switch
                    # to avoid invalid_request errors.
                    start_frame = 0
                    try:
                        start_frame = int(clip.range_in_parent().start_time.value)
                    except Exception:
                        # Fallback: Sum duration of all preceding items in the track
                        otio_tl = self.manager.timelines.get(matched_tl_guid) if self.manager else None
                        if otio_tl:
                            for track in otio_tl.tracks:
                                if track.kind == otio.schema.TrackKind.Video:
                                    current_time = 0
                                    for item in track:
                                        if item.metadata.get("sync", {}).get("guid") == clip_guid:
                                            start_frame = current_time
                                            break
                                        sr = getattr(item, "source_range", None)
                                        if sr is not None:
                                            current_time += int(sr.duration.value)

                    self.connection.api.session.set_on_screen_source(playlist_xs_tl)
                    _log(f"RECV selection: set_on_screen_source (sequence) → "
                         f"{getattr(playlist_xs_tl, 'name', '?')!r}")

                    # Defer the seek until Form-2 events have settled the playhead (~200 ms).
                    self._pending_seek_frame = start_frame
                    self._pending_seek_deadline = time.monotonic() + 0.300

                    # Programmatically select/highlight the clip in the timeline track.
                    if otio_tl:
                        target_track_idx = -1
                        target_child_idx = -1
                        for track_idx, track in enumerate(otio_tl.tracks):
                            for child_idx, child in enumerate(track):
                                if child.metadata.get("sync", {}).get("guid") == clip_guid:
                                    target_track_idx = track_idx
                                    target_child_idx = child_idx
                                    break
                            if target_track_idx != -1:
                                break

                        if target_track_idx != -1 and target_child_idx != -1:
                            try:
                                xs_track = playlist_xs_tl.stack.children[target_track_idx]
                                xs_child = xs_track.children[target_child_idx]
                                from xstudio.core import UuidActor, UuidActorVec, item_selection_atom
                                ua = UuidActor(xs_child.uuid, xs_child.remote)
                                ua_vec = UuidActorVec()
                                ua_vec.push_back(ua)
                                self.connection.send(playlist_xs_tl.remote, item_selection_atom(), ua_vec)
                                _log(f"RECV selection: set timeline selection to track={target_track_idx} child={target_child_idx}")
                            except Exception:
                                _log_exc("RECV selection: failed to set timeline item selection")
                else:
                    # Flat playlist: viewed_container + set_on_screen_source + set_selection
                    self.connection.api.session.set_on_screen_source(playlist)
                    media, _ = self._find_media_for_clip_guid(clip_guid)
                    if media:
                        playlist.playhead_selection.set_selection([media.uuid])
                        _log(f"RECV selection: set_selection "
                             f"→ {getattr(media, 'name', '?')!r} ({str(media.uuid)[:8]})")
                    else:
                        _log(f"RECV selection: media not found for clip {clip_guid[:8]}")
            except Exception:
                _log_exc("RECV selection: container switch or selection failed")

            # active_playhead is refreshed by Form-2 viewport_playhead_atom events
            # that fire as the source switch completes (~200 ms).  _apply_pending_seek
            # then applies the deferred seek once the deadline passes.
            _log("RECV selection: source switch dispatched")
        else:
            _log(f"RECV selection: no playlist found for clip")

    def _apply_display_state(self, state: dict) -> None:
        """Apply a received display state dict to the local xStudio viewport.

        :param state: Display state dict with pan, zoom, exposure, channel keys.
        """
        vp = self._get_viewport()
        if vp is None:
            return

        pan = state.get("pan")      # None means sender doesn't support pan
        zoom = state.get("zoom")    # None means sender doesn't support zoom
        exposure = state.get("exposure", 0.0)
        channel = state.get("channel", "RGBA")

        try:
            vp.colour_pipeline.exposure.set_value(float(exposure))
        except Exception as e:
            _log(f"RECV display: exposure set failed: {e}")

        try:
            xs_ch = self._PROTO_TO_XS_CHANNEL.get(channel, "RGB")
            vp.colour_pipeline.channel.set_value(xs_ch)
        except Exception as e:
            _log(f"RECV display: channel set failed: {e}")

        # Pan/zoom cannot be safely written from the xStudio Python API —
        # deserialise_atom round-trips through Python JSON and crashes on
        # ColourTriplet deserialization in the viewport settings.  Read
        # back the actual viewport state so the echo-guard sees the real
        # current values and doesn't re-broadcast them as a spurious change.
        readback = self._read_xs_display_state()
        self._last_display_state = {
            "pan": readback["pan"],
            "zoom": readback["zoom"],
            "exposure": exposure,
            "channel": channel,
        }
        _log(f"RECV display exposure={exposure:.3f} channel={channel} "
             f"(zoom={zoom} pan={pan} received but not applied — write not safe)")

    def _poll_flat_playlist_reorders(self) -> None:
        """Detect and broadcast clip reorders in flat (media-bin) Playlists.

        Only runs on the master.  For each flat Playlist registered in
        ``_xs_flat_playlists``, reads the current ``playlist.media`` order from
        xStudio and compares it to the stored name list.  When a difference is
        found the clip at the first mismatched position is moved via
        :meth:`~otio_sync_core.manager.SyncManager.broadcast_move_child`.
        Because user drags move one clip at a time this converges in a single
        poll cycle for the typical case; multi-hop reorders converge in subsequent
        cycles.
        """
        if not self.manager or not self.manager.is_master:
            return

        for tl_guid, (xs_playlist, stored_order) in list(self._xs_flat_playlists.items()):
            try:
                current_media = xs_playlist.media
                current_order = [m.name for m in current_media]
            except Exception:
                continue

            if current_order == stored_order:
                continue

            otio_tl = self.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue

            video_track = next(
                (t for t in otio_tl.tracks
                 if t.kind == otio.schema.TrackKind.Video),
                None,
            )
            if video_track is None:
                continue

            track_guid = video_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                continue

            # Build name → clip-GUID map from the current OTIO track state.
            name_to_clip_guid: dict[str, str] = {}
            for clip in video_track:
                if isinstance(clip, otio.schema.Clip):
                    cg = clip.metadata.get("sync", {}).get("guid")
                    if cg:
                        name_to_clip_guid[clip.name] = cg

            # Find the first position where orders differ; broadcast that clip
            # moving to its new index.
            for new_idx, name in enumerate(current_order):
                if new_idx >= len(stored_order) or stored_order[new_idx] != name:
                    child_guid = name_to_clip_guid.get(name)
                    if child_guid:
                        self.manager.broadcast_move_child(track_guid, child_guid, new_idx)
                        _log(f"Flat playlist reorder: {name!r} → index {new_idx}")
                    # Update stored order whether or not we found a GUID, so we
                    # don't re-fire on the same state next tick.
                    self._xs_flat_playlists[tl_guid] = (xs_playlist, list(current_order))
                    break

    def _poll_flat_playlist_new_media(self) -> None:
        """Detect and broadcast media items added to flat Playlists.

        Only runs on the master.  Compares the current media count against the
        stored order; when new items are found it builds OTIO Clips from their
        media references and calls ``manager.insert_child`` so all peers receive
        the new clip via INSERT_CHILD.
        """
        if not self.manager or not self.manager.is_master:
            return

        for tl_guid, (xs_playlist, stored_order) in list(self._xs_flat_playlists.items()):
            try:
                current_media = xs_playlist.media
            except Exception:
                continue
            if len(current_media) <= len(stored_order):
                continue

            otio_tl = self.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue
            video_track = next(
                (t for t in otio_tl.tracks if t.kind == otio.schema.TrackKind.Video), None
            )
            if video_track is None:
                continue
            track_guid = video_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                continue

            stored_names = set(stored_order)
            current_order = [m.name for m in current_media]
            for media in current_media:
                if media.name in stored_names:
                    continue
                # New media item — build an OTIO Clip and broadcast.
                try:
                    ms = media.media_source()
                    uri = str(ms.media_reference.uri())
                    fps = 25.0
                    rate_obj = ms.rate
                    if rate_obj:
                        fps = rate_obj.fps() or fps
                    frame_count = None
                    try:
                        info = media.display_info
                        for key in ("frames", "Frames", "frame_count", "num_frames"):
                            v = info.get(key)
                            if v:
                                frame_count = int(v)
                                break
                    except Exception:
                        pass
                    if frame_count:
                        sr = otio.opentime.TimeRange(
                            otio.opentime.RationalTime(0, fps),
                            otio.opentime.RationalTime(frame_count, fps),
                        )
                        clip = otio.schema.Clip(
                            name=media.name,
                            media_reference=otio.schema.ExternalReference(
                                target_url=uri, available_range=sr
                            ),
                            source_range=sr,
                        )
                    else:
                        clip = otio.schema.Clip(
                            name=media.name,
                            media_reference=otio.schema.ExternalReference(target_url=uri),
                        )
                    new_index = current_order.index(media.name)
                    self.manager.insert_child(track_guid, clip, new_index)
                    _log(f"flat playlist new media: {media.name!r} inserted at {new_index}")
                except Exception:
                    _log_exc(f"flat playlist new media: failed for {media.name!r}")

            self._xs_flat_playlists[tl_guid] = (xs_playlist, current_order)

    def _build_single_sequence_otio(
        self, playlist, xs_tl
    ) -> "otio.schema.Timeline | None":
        """Build an OTIO Timeline from a single xStudio Timeline container.

        Counterpart to :meth:`_build_otio_timelines` for use when a new
        sequence is detected after initial connection.  Assigns deterministic
        sync GUIDs to all tracks and clips using the same hashing scheme as
        :meth:`_build_otio_timelines`.

        :param playlist: Parent xStudio :class:`Playlist`.
        :param xs_tl: xStudio :class:`Timeline` to export.
        :returns: OTIO Timeline, or ``None`` on failure.
        :rtype: opentimelineio.schema.Timeline or None
        """
        try:
            if hasattr(xs_tl, "to_otio_string"):
                otio_str = xs_tl.to_otio_string()
            else:
                from xstudio.api.auxiliary.otio import timeline_to_otio_string as _tl_str
                otio_str = _tl_str(xs_tl)
            tl = otio.adapters.read_from_string(otio_str)
            tl_guid = str(xs_tl.uuid)
            tl.metadata.setdefault("sync", {})["guid"] = tl_guid
            tl.metadata["xs_playlist_name"] = playlist.name
            import hashlib
            for track_idx, track in enumerate(tl.tracks):
                track_seed = f"{tl_guid}:{track.kind}:{track_idx}:{track.name}"
                track_guid = hashlib.sha1(track_seed.encode("utf-8")).hexdigest()
                track.metadata.setdefault("sync", {})["guid"] = track_guid
                clip_idx = 0
                for child in track:
                    if isinstance(child, otio.schema.Clip):
                        clip_seed = f"{track_guid}:{clip_idx}:{child.name}"
                        clip_guid = hashlib.sha1(clip_seed.encode("utf-8")).hexdigest()
                        child.metadata.setdefault("sync", {})["guid"] = clip_guid
                        clip_idx += 1
            _log(f"_build_single_sequence_otio: {tl.name!r}")
            return tl
        except Exception:
            _log_exc(
                f"_build_single_sequence_otio: failed for "
                f"{getattr(xs_tl, 'name', '?')!r}"
            )
            return None

    def _poll_new_playlists(self) -> None:
        """Detect newly created playlists or timelines and broadcast them.

        Runs on any synced peer (not just the master).  Scans
        ``session.playlists`` for containers not yet in ``_sync_playlists``
        and broadcasts each new one via
        :meth:`~otio_sync_core.manager.SyncManager.broadcast_add_timeline`.
        Sequence (Timeline-backed) and flat (media-bin) playlists are both
        handled.
        """
        if not self.manager:
            return
        if self.manager.status != STATE_SYNCED:
            return
        try:
            playlists = self.connection.api.session.playlists
        except Exception:
            return

        known_pl_uuids: set[str] = set()
        for pl, _ in self._sync_playlists.values():
            try:
                known_pl_uuids.add(str(pl.uuid))
            except Exception:
                pass

        for playlist in playlists:
            try:
                pl_uuid = str(playlist.uuid)
            except Exception:
                continue
            if pl_uuid in known_pl_uuids:
                continue

            # Unknown playlist — determine type and register.
            try:
                containers = playlist.containers
            except Exception:
                _log_exc(
                    f"_poll_new_playlists: cannot get containers for "
                    f"{getattr(playlist, 'name', '?')!r}"
                )
                continue

            timelines = [c for c in containers if isinstance(c, Timeline)]
            if timelines:
                for xs_tl in timelines:
                    tl_guid = str(xs_tl.uuid)
                    if tl_guid in self._sync_playlists:
                        continue
                    tl = self._build_single_sequence_otio(playlist, xs_tl)
                    if tl is None:
                        continue
                    self.manager.register_timeline(tl)
                    self._xs_sequence_playlists[tl_guid] = (playlist, xs_tl)
                    self._sync_playlists[tl_guid] = (playlist, xs_tl)
                    self._subscribe_timeline_item_events(tl_guid, xs_tl)
                    self.manager.broadcast_add_timeline(tl_guid)
                    _log(
                        f"New sequence timeline {xs_tl.name!r} "
                        f"(playlist={playlist.name!r}) → broadcast"
                    )
            else:
                tl = self._build_otio_from_playlist_media(playlist)
                if tl is None:
                    continue
                tl_guid = tl.metadata.get("sync", {}).get("guid", "")
                if not tl_guid:
                    continue
                # _build_otio_from_playlist_media already adds to _sync_playlists
                # as a side effect, so do NOT check tl_guid in _sync_playlists here —
                # it would always be True and suppress the broadcast.
                # Deduplication is handled by the known_pl_uuids check at the top.
                self.manager.register_timeline(tl)
                self.manager.broadcast_add_timeline(tl_guid)
                _log(f"New flat playlist {playlist.name!r} → broadcast")

    def _poll_playlist_renames(self) -> None:
        """Detect and broadcast playlist or timeline name changes.

        Runs on any synced peer (not just the master).  Compares the current
        xStudio name against the OTIO timeline name stored in the manager for
        each tracked playlist.  When a change is detected,
        :meth:`~otio_sync_core.manager.SyncManager.broadcast_timeline_rename`
        propagates it to all peers.
        """
        if not self.manager:
            return
        if self.manager.status != STATE_SYNCED:
            return
        for tl_guid, (pl, xs_tl) in list(self._sync_playlists.items()):
            otio_tl = self.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue
            try:
                current_name = xs_tl.name if xs_tl is not None else pl.name
            except Exception:
                continue
            if current_name and current_name != (otio_tl.name or ""):
                _log(
                    f"Timeline rename: {otio_tl.name!r} → {current_name!r} "
                    f"({tl_guid[:8]})"
                )
                self.manager.broadcast_timeline_rename(tl_guid, current_name)

    @staticmethod
    def _clips_match(c1: "otio.schema.Clip", c2: "otio.schema.Clip") -> bool:
        """Check if two OTIO Clips refer to the same media item.

        Compares the names and target URLs (if present) of the two clips.

        :param c1: The first clip to compare.
        :type c1: otio.schema.Clip
        :param c2: The second clip to compare.
        :type c2: otio.schema.Clip
        :return: True if they refer to the same media, False otherwise.
        :rtype: bool
        """
        if c1.name != c2.name:
            return False
        mr1 = getattr(c1, "media_reference", None)
        mr2 = getattr(c2, "media_reference", None)
        url1 = getattr(mr1, "target_url", None) if mr1 else None
        url2 = getattr(mr2, "target_url", None) if mr2 else None
        if url1 and url2 and url1 != url2:
            return False
        return True

    def _poll_sequence_new_media(self, only_guid: str | None = None) -> None:
        """Detect and broadcast clips added to sequence Timelines.

        Only runs on the master.  Re-exports each tracked xStudio Timeline via
        ``to_otio_string()`` and compares the clip sequence against the stored
        OTIO track using an index-based alignment loop. New clips are broadcast
        via ``manager.insert_child``.

        :param only_guid: When given, only checks the timeline with this sync
            GUID (used by the [2F] event-driven path to avoid re-scanning all
            timelines on every item_atom event).
        """
        if not self.manager or not self.manager.is_master:
            return

        items = list(self._xs_sequence_playlists.items())
        if only_guid is not None:
            items = [(g, v) for g, v in items if g == only_guid]

        for tl_guid, (_, xs_tl) in items:
            otio_tl = self.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue
            video_track = next(
                (t for t in otio_tl.tracks if t.kind == otio.schema.TrackKind.Video), None
            )
            if video_track is None:
                continue
            track_guid = video_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                continue

            try:
                fresh_otio_str = xs_tl.to_otio_string()
                fresh_tl = otio.adapters.read_from_string(fresh_otio_str)
            except Exception:
                continue

            for fresh_track in fresh_tl.tracks:
                if fresh_track.kind != otio.schema.TrackKind.Video:
                    continue
                fresh_clips = [c for c in fresh_track if isinstance(c, otio.schema.Clip)]
                stored_clips = [c for c in video_track if isinstance(c, otio.schema.Clip)]

                fresh_idx = 0
                stored_idx = 0
                while fresh_idx < len(fresh_clips):
                    fresh_clip = fresh_clips[fresh_idx]

                    # Check if it matches the current stored clip
                    match = False
                    if stored_idx < len(stored_clips):
                        if self._clips_match(fresh_clip, stored_clips[stored_idx]):
                            match = True

                    if match:
                        fresh_idx += 1
                        stored_idx += 1
                    else:
                        # Check if fresh_clip matches any later stored clip
                        found_later = False
                        for temp_idx in range(stored_idx + 1, len(stored_clips)):
                            if self._clips_match(fresh_clip, stored_clips[temp_idx]):
                                stored_idx = temp_idx
                                found_later = True
                                break

                        if found_later:
                            # Now fresh_clip matches stored_clips[stored_idx]
                            fresh_idx += 1
                            stored_idx += 1
                        else:
                            # Truly a new clip insert
                            # Deep-copy the clip using serialization to avoid ValueError: child already has a parent
                            from otio_sync_core.patcher import _otio_to_dict, _dict_to_otio
                            clip_copy = _dict_to_otio(_otio_to_dict(fresh_clip))
                            self.manager.insert_child(track_guid, clip_copy, fresh_idx)
                            _log(f"sequence new clip: {fresh_clip.name!r} at index {fresh_idx}")
                            stored_clips.insert(fresh_idx, clip_copy)
                            fresh_idx += 1
                            stored_idx += 1

    def _apply_remote_clip_insert(self, clip_obj: "otio.schema.Clip") -> None:
        """Route a received non-annotation INSERT_CHILD clip to the right handler.

        Searches ``_sync_playlists`` for the playlist whose OTIO track now
        contains *clip_obj* (the manager has already inserted it).  Dispatches
        to :meth:`_apply_flat_playlist_insert` or :meth:`_apply_sequence_insert`
        depending on the timeline type.

        :param clip_obj: The newly-inserted OTIO Clip.
        """
        clip_guid = clip_obj.metadata.get("sync", {}).get("guid", "")
        if not clip_guid:
            return
        for tl_guid, (pl, xs_tl) in self._sync_playlists.items():
            otio_tl = self.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue
            for track in otio_tl.tracks:
                if track.kind != otio.schema.TrackKind.Video:
                    continue
                for child in track:
                    if child.metadata.get("sync", {}).get("guid") == clip_guid:
                        if otio_tl.metadata.get("xs_flat_playlist"):
                            self._apply_flat_playlist_insert(clip_obj, pl, xs_tl)
                        else:
                            self._apply_sequence_insert(tl_guid, otio_tl, xs_tl)
                        return

    def _poll_and_broadcast_display(self) -> None:
        """Broadcast display state when exposure or channel changes.

        Called from the poll thread on every tick.  Compares the current viewport
        state against ``_last_display_state`` and broadcasts only on change.
        """
        if not self.manager or self.manager.status != STATE_SYNCED:
            return
        state = self._read_xs_display_state()
        if state == self._last_display_state:
            return
        self._last_display_state = state
        _log(f"Poll display: broadcasting exposure={state['exposure']:.3f} "
             f"channel={state['channel']}")
        self.manager.broadcast_display_state(state)

    # ── annotation send ────────────────────────────────────────────────────────

    #: How long to wait after the last annotation_atom before scanning bookmarks.
    DEBOUNCE_SECONDS = 0.25
    #: Stop hot-scanning a frame after this many seconds of no new strokes.
    HOT_SCAN_TIMEOUT = 0.6

    def _on_annotation_event(self, data) -> None:
        """Called by xStudio when the user completes a stroke in the viewport.

        Fired by the AnnotationsUI plugin's event group whenever a stroke is
        committed (``annotation_atom``).  Records the time so the poll thread
        can find and broadcast the new bookmark after debounce.

        :param data: Event tuple from the AnnotationsUI plugin events group.
            Shape: ``(event_atom, annotation_atom, JsonStore)``.
        """
        # [TEST annotation_atom] Log every event from this subscription so we
        # can see whether annotation_atom actually arrives in this xStudio build.
        t1 = type(data[1]).__name__ if len(data) > 1 else "n/a"
        matched = (
            len(data) >= 3
            and isinstance(data[0], event_atom)
            and isinstance(data[1], annotation_atom)
        )
        _log(f"[TEST annotation_atom] event len={len(data)}, t1={t1}, matched={matched}")
        if not matched:
            return
        if not self.manager or self.manager.status != STATE_SYNCED:
            return
        _log("Annotation event from AnnotationsUI — scheduling broadcast scan")
        self._annotation_pending_time = time.monotonic()

    def _on_core_annotation_event(self, data) -> None:
        """[2C] Called when AnnotationsCore broadcasts a live stroke event.

        Fired on every PaintStart/PaintPoint/PaintEnd via the simplified
        Python-accessible broadcast added to ``broadcast_live_stroke``.

        Shape: ``(event_atom, annotation_data_atom, user_id, stroke_completed)``

        ``stroke_completed=True`` at PaintEnd (pen-up): schedule annotation flush.
        ``stroke_completed=False`` at PaintStart/PaintPoint: activate hot scan.

        :param data: Event tuple from AnnotationsCore plugin_events_.
        """
        if not (len(data) >= 4
                and isinstance(data[0], event_atom)
                and isinstance(data[1], annotation_data_atom)):
            return
        if not self.manager or self.manager.status != STATE_SYNCED:
            return
        stroke_completed = bool(data[3])
        if stroke_completed:
            _log("[2C] AnnotationsCore: pen-up (stroke_completed=True) — scheduling flush")
            self._annotation_pending_time = time.monotonic()
            # Deactivate hot scan; final stroke will be picked up by flush
            self._hot_scan_active = False
        else:
            # Mid-stroke: ensure hot scan is running on the current frame
            if not self._hot_scan_active:
                if self.active_playhead:
                    try:
                        self._hot_scan_frame = self.active_playhead.position
                        self._hot_scan_active = True
                        self._hot_scan_last_change = time.monotonic()
                        _log(f"[2C] AnnotationsCore: mid-stroke — hot scan at frame {self._hot_scan_frame}")
                    except Exception:
                        pass
            else:
                self._hot_scan_last_change = time.monotonic()

    def _hot_scan_active_annotation(self) -> None:
        """Poll the active drawing frame every tick to stream partial strokes.

        Activated when ``show_atom`` fires (user starts drawing on a new frame).
        Runs on every poll tick (33 ms) so that partial strokes are broadcast to
        peers before pen-up, giving an interactive feel.

        Uses ``_stroke_uuid_cache`` to assign stable UUIDs to strokes at each
        index, so that a receiver that already rendered the partial via
        ``_apply_partial_annotation_xs`` can update in-place rather than create
        a duplicate when the final ``INSERT_CHILD`` arrives.
        """
        if not self._hot_scan_active:
            return
        if not self.manager or self.manager.status != STATE_SYNCED:
            self._hot_scan_active = False
            return
        now = time.monotonic()
        if now - self._hot_scan_last_change > self.HOT_SCAN_TIMEOUT:
            _log("Hot scan timed out — deactivating")
            self._hot_scan_active = False
            return

        frame = self._hot_scan_frame
        if frame is None:
            return

        tl = self.manager.root_timeline
        if tl is None:
            return

        try:
            clip_guid, clip_local_time = self._resolve_clip_at_frame(tl, frame)
        except Exception:
            return
        if clip_guid is None:
            # Flat-playlist fallback: clips have no source_range so
            # _resolve_clip_at_frame always returns None.  Use the last
            # broadcast/received selection clip GUID; for flat playlists the
            # user views one clip at a time so this is always the right clip.
            fb = self._last_viewed_clip_guid
            if fb and fb in self._flat_clip_to_media:
                clip_guid = fb
                ph_fps = 25.0
                if self.active_playhead:
                    try:
                        ph_fps = self.active_playhead.frame_rate.fps() or ph_fps
                    except Exception:
                        pass
                clip_local_time = otio.opentime.RationalTime(frame, ph_fps)
            else:
                return

        local_frame = int(clip_local_time.value)
        fps = float(clip_local_time.rate) if clip_local_time.rate else 25.0

        # Find a local (non-remote) bookmark at this frame.
        try:
            all_bms = self.connection.api.session.bookmarks.bookmarks
        except Exception:
            return

        target_bm = None
        for bm in all_bms:
            bm_uuid_str = str(bm.uuid)
            if bm_uuid_str in self._our_bookmark_clip_frame:
                continue  # remote bookmark, skip
            with self._our_bookmark_uuids_lock:
                is_remote = bm_uuid_str in self._our_bookmark_uuids
            if is_remote:
                continue
            try:
                detail = bm.detail
                if detail is None or detail.start is None:
                    continue
                bm_frame = int(round(detail.start.total_seconds() * fps))
                if bm_frame == frame:
                    target_bm = bm
                    break
            except Exception:
                continue

        if target_bm is None:
            return

        try:
            ann_data = target_bm.annotation_data
            if not ann_data:
                return
        except Exception:
            return

        canvas = ann_data.get("Data", ann_data)
        all_strokes = canvas.get("pen_strokes", [])
        if not all_strokes:
            return

        key = f"{clip_guid}:{local_frame}"
        last_sent_strokes = self._hot_scan_stroke_counts.get(key, 0)
        last_sent_points = self._hot_scan_point_counts.get(key, 0)

        current_stroke_points = len(all_strokes[-1].get("points", [])) if all_strokes else 0

        if len(all_strokes) == last_sent_strokes and current_stroke_points <= last_sent_points:
            return  # no new strokes or points since last hot broadcast

        self._hot_scan_last_change = now
        self._hot_scan_stroke_counts[key] = len(all_strokes)
        self._hot_scan_point_counts[key] = current_stroke_points

        # Ensure UUID cache covers all strokes (including pre-existing ones).
        if key not in self._stroke_uuid_cache:
            self._stroke_uuid_cache[key] = []
        cache = self._stroke_uuid_cache[key]
        while len(cache) < len(all_strokes):
            cache.append(str(uuid.uuid4()))

        _, aspect_half = self._find_media_for_clip_guid(clip_guid)

        # Send ALL current strokes so peers can update from any starting point.
        events_obj = xs_strokes_to_sync_events(all_strokes, aspect_half, uuid_list=cache)
        if not events_obj:
            return

        events_dicts = []
        for e in events_obj:
            try:
                events_dicts.append(
                    json.loads(otio.adapters.write_to_string(e, "otio_json", indent=-1))
                )
            except Exception:
                pass
        if not events_dicts:
            return

        _log(
            f"Hot scan: broadcasting {len(all_strokes)} stroke(s) as partial"
            f" at frame={frame} clip={clip_guid[:8]}"
        )
        self.manager.broadcast_partial_annotation(
            clip_guid=clip_guid,
            frame=float(local_frame),
            fps=fps,
            events=events_dicts,
        )

    def _flush_pending_annotations(self) -> None:
        """Scan all bookmarks we don't own and broadcast any new strokes.

        Called from the poll thread after every tick.  Runs when either:

        * An event (``show_atom`` or ``annotation_atom``) set
          ``_annotation_pending_time`` and the debounce has expired, OR
        * No event fired but ``ANNOTATION_SCAN_INTERVAL`` seconds have elapsed
          since the last scan (fallback for strokes added to an *existing*
          bookmark where ``show_atom`` does not fire).

        Iterates ``session.bookmarks.bookmarks``, skips UUIDs in
        ``_our_bookmark_uuids`` (bookmarks we created from remote annotations),
        and broadcasts any strokes not yet present in the OTIO timeline.
        """
        now = time.monotonic()
        if self._annotation_pending_time is not None:
            if now - self._annotation_pending_time < self.DEBOUNCE_SECONDS:
                return
            # Event-triggered flush — clear the pending flag.
            self._annotation_pending_time = None
        else:
            # No event — run the periodic fallback scan.
            if now - self._last_annotation_scan < self.ANNOTATION_SCAN_INTERVAL:
                return
        self._last_annotation_scan = now

        if not self.manager or self.manager.status != STATE_SYNCED:
            return
        try:
            all_bms = self.connection.api.session.bookmarks.bookmarks
        except Exception:
            _log_exc("_flush_pending_annotations: could not list bookmarks")
            return

        # Scan all bookmarks, not just unowned ones.  When the user draws on a
        # frame that already has a remote annotation, xStudio adds to the existing
        # bookmark in-place (same UUID).  That UUID is in _our_bookmark_uuids, so
        # filtering it out would silently drop the new local stroke.  The OTIO
        # delta check inside _broadcast_local_bookmark correctly handles
        # deduplication — remote strokes are already in the timeline so delta=0.
        scan_uuids = [bm.uuid for bm in all_bms]
        if not scan_uuids:
            return

        stale_any = False
        for bm_uuid in scan_uuids:
            try:
                result = self._broadcast_local_bookmark(bm_uuid)
                if result is None:
                    stale_any = True
            except Exception:
                _log_exc("_flush_pending_annotations: failed to broadcast bookmark")

        # xStudio may not have committed annotation_data yet when the debounce fires.
        # Only retry when a bookmark explicitly returned None (empty annotation_data);
        # if all bookmarks returned False the timeline is already up-to-date.
        if stale_any and self._annotation_flush_retries < 5:
            self._annotation_flush_retries += 1
            _log(f"_flush_pending_annotations: stale annotation_data, retry {self._annotation_flush_retries}/5")
            self._annotation_pending_time = time.monotonic()
        else:
            self._annotation_flush_retries = 0

    def _broadcast_local_bookmark(self, bm_uuid) -> "bool | None":
        """Read a locally-drawn bookmark's annotation and broadcast it to the session.

        Uses the local OTIO timeline as the authoritative record of what has
        already been broadcast.  This is robust even when xStudio replaces a
        bookmark with a new UUID that contains both old and new strokes, because
        the timeline count always reflects exactly what was sent — regardless of
        which bookmark UUID carried those strokes.

        :param bm_uuid: The ``Uuid`` of the bookmark to broadcast.
        :returns: ``True`` if new events were broadcast; ``False`` if everything
            is already in the timeline (no retry needed); ``None`` if
            ``annotation_data`` was empty (xStudio hasn't committed the stroke
            yet — caller should retry after a short delay).
        """
        if not self.manager or self.manager.status != STATE_SYNCED:
            return False

        try:
            bm = self.connection.api.session.bookmarks.get_bookmark(bm_uuid)
        except Exception:
            _log_exc("_broadcast_local_bookmark: get_bookmark failed")
            return False

        # Read timing to determine which frame this annotation sits on.
        fps = 25.0
        if self.active_playhead:
            fps = self.active_playhead.frame_rate.fps() or fps
        try:
            detail = bm.detail
            if detail is None or detail.start is None:
                return False
            frame = int(round(detail.start.total_seconds() * fps))
        except Exception:
            _log_exc("_broadcast_local_bookmark: could not read timing")
            return False

        # Read stroke/caption data.
        # annotation_data returns {"plugin_uuid": ..., "Data": {"pen_strokes": [...], ...}}
        try:
            ann_data = bm.annotation_data
            if not ann_data:
                # xStudio hasn't committed the stroke to annotation_data yet.
                _log("_broadcast_local_bookmark: annotation_data is empty — will retry")
                return None
        except Exception:
            _log_exc("_broadcast_local_bookmark: could not read annotation data")
            return False

        # The canvas dict lives under the "Data" key; fall back to the top-level
        # dict in case the format has changed.
        canvas = ann_data.get("Data", ann_data)

        # Resolve clip_guid first — annotation_track_guid_for_clip requires it.
        # Remote-sourced bookmarks have their correct (clip_guid, clip-local-frame)
        # stored in _our_bookmark_clip_frame; bm.detail.start is clip-local time,
        # not global sequence time, so _resolve_clip_at_frame would land on the
        # wrong clip when two clips share the same clip-local frame number.
        bm_uuid_str = str(bm_uuid)
        if bm_uuid_str in self._our_bookmark_clip_frame:
            clip_guid, _clip_frame_int = self._our_bookmark_clip_frame[bm_uuid_str]
            clip_local_time = otio.opentime.RationalTime(_clip_frame_int, fps)
        else:
            tl = self.manager.root_timeline
            if tl is None:
                _log("_broadcast_local_bookmark: no timeline registered")
                return False
            clip_guid, clip_local_time = self._resolve_clip_at_frame(tl, frame)
            if clip_guid is None:
                # Flat-playlist fallback: clips have no source_range so
                # _resolve_clip_at_frame always returns None.  Use the last
                # viewed clip GUID; for flat playlists the user views one
                # clip at a time so this is always the right clip, and the
                # bookmark frame is already clip-local.
                fb = self._last_viewed_clip_guid
                if fb and fb in self._flat_clip_to_media:
                    clip_guid = fb
                    clip_local_time = otio.opentime.RationalTime(frame, fps)
                    _log(
                        f"_broadcast_local_bookmark: flat-playlist fallback"
                        f" → clip {clip_guid[:8]} frame {frame}"
                    )
                else:
                    _log(f"_broadcast_local_bookmark: no clip at frame {frame}")
                    return False

        annotation_track_guid = self.manager.annotation_track_guid_for_clip(clip_guid)
        if annotation_track_guid is None:
            _log("_broadcast_local_bookmark: no Annotations track")
            return False

        _, aspect_half = self._find_media_for_clip_guid(clip_guid)
        all_strokes = canvas.get("pen_strokes", [])
        all_captions = canvas.get("captions", [])

        bm_key = (clip_guid, int(clip_local_time.value))
        # Register the local bookmark so _refresh_annotation_bookmark can update it
        # when a remote peer adds strokes to the same frame later.
        self._annotation_bookmarks[bm_key] = bm

        # Query the annotation track directly from _object_map (the same object that
        # broadcast_add_annotation mutates) to find how many strokes are already
        # broadcast for this (clip, frame).  Traversing tl.tracks could yield
        # wrapper objects that don't reflect mutations made through _object_map.
        sent_strokes, sent_captions = self.manager.count_annotation_commands(
            clip_guid, int(clip_local_time.value)
        )
        new_strokes = all_strokes[sent_strokes:]
        new_captions = all_captions[sent_captions:]

        # Ensure UUID cache covers all strokes so the final broadcast uses the
        # same UUIDs as any earlier partial broadcasts for this frame.
        uuid_key = f"{clip_guid}:{int(clip_local_time.value)}"
        if uuid_key not in self._stroke_uuid_cache:
            self._stroke_uuid_cache[uuid_key] = []
        uuid_cache = self._stroke_uuid_cache[uuid_key]
        while len(uuid_cache) < len(all_strokes):
            uuid_cache.append(str(uuid.uuid4()))
        # UUIDs for the delta strokes start at index sent_strokes.
        delta_uuids = uuid_cache[sent_strokes:len(all_strokes)]

        # Detect in-place text edits: caption count is unchanged but content
        # differs.  Delta tracking (count-based) misses these, so we replace the
        # full command list on the existing clip instead of appending a delta.
        #
        # Compare against *last sent xStudio captions* (not OTIO-stored positions)
        # because xStudio quantises float values internally, so reading back from
        # bm.annotation_data gives slightly different positions than what we set —
        # comparing against OTIO-reconverted values would loop forever.
        if sent_captions > 0 and sent_captions == len(all_captions):
            cap_key = f"{clip_guid}:{int(clip_local_time.value)}"
            current_sig = self._caption_signature(all_captions)
            if self._last_sent_captions.get(cap_key) != current_sig:
                ann_clip_guid = self.manager.annotation_clip_guid_at(
                    clip_guid, int(clip_local_time.value)
                )
                if ann_clip_guid:
                    existing_uuids = self._extract_caption_uuids(ann_clip_guid)
                    all_events = (
                        xs_strokes_to_sync_events(all_strokes, aspect_half, uuid_list=uuid_cache)
                        + xs_captions_to_sync_events(all_captions, aspect_half, existing_uuids)
                    )
                    _log(
                        f"Broadcasting annotation replace: {len(all_events)} event(s)"
                        f" (caption edit) at frame={frame} clip={clip_guid[:8]}"
                    )
                    self.manager.broadcast_replace_annotation_commands(ann_clip_guid, all_events)
                    self._last_sent_captions[cap_key] = current_sig
                    return True

        events = (
            xs_strokes_to_sync_events(new_strokes, aspect_half, uuid_list=delta_uuids)
            + xs_captions_to_sync_events(new_captions, aspect_half)
        )
        if not events:
            return False

        _log(
            f"Broadcasting local annotation: {len(events)} SyncEvent(s)"
            f" (+{len(new_strokes)} strokes, +{len(new_captions)} captions)"
            f" at frame={frame} clip={clip_guid[:8]}"
        )
        self.manager.broadcast_add_annotation(
            annotation_track_guid=annotation_track_guid,
            clip_guid=clip_guid,
            clip_local_time=clip_local_time,
            events=events,
        )
        # Record caption signature so the next scan doesn't re-broadcast them.
        if new_captions:
            cap_key = f"{clip_guid}:{int(clip_local_time.value)}"
            self._last_sent_captions[cap_key] = self._caption_signature(all_captions)
        return True

    # ── OTIO timeline delta helpers ───────────────────────────────────────────

    @staticmethod
    def _caption_signature(xs_captions: list) -> str:
        """Return a stable JSON string representing the xStudio caption content.

        Used to detect real user edits without comparing against OTIO-reconverted
        coordinates (which suffer float quantisation on every xStudio round-trip).

        :param xs_captions: Caption dicts from ``bm.annotation_data["Data"]["captions"]``.
        :returns: JSON string that changes when text, position, or colour changes.
        :rtype: str
        """
        return json.dumps(
            [
                {
                    "text": c.get("text", ""),
                    "pos": c.get("position", []),
                    "colour": c.get("colour", []),
                    "opacity": c.get("opacity", 1.0),
                }
                for c in xs_captions
            ],
            sort_keys=True,
        )

    def _extract_caption_uuids(self, ann_clip_guid: str) -> "list[str]":
        """Return the ordered UUIDs of all TextAnnotation commands in an annotation clip.

        Used when building replacement events so that the same UUIDs are reused
        and remote peers (e.g. RV) can find and update existing paint nodes in place.

        :param ann_clip_guid: Sync GUID of the annotation clip in ``manager._object_map``.
        :returns: List of UUID strings, one per TextAnnotation, in command order.
        :rtype: list
        """
        clip = self.manager._object_map.get(ann_clip_guid) if self.manager else None
        if clip is None:
            return []
        uuids: list[str] = []
        for cmd in clip.metadata.get("annotation_commands", []):
            schema = sync_event_schema(cmd)
            if not schema:
                continue
            if schema.startswith("TextAnnotation"):
                uid = getattr(cmd, "uuid", None)
                if uid is None and isinstance(cmd, dict):
                    uid = cmd.get("uuid")
                if uid:
                    uuids.append(uid)
        return uuids

    # ── annotation receive ─────────────────────────────────────────────────────

    def _load_snapshot_annotations(
        self, otio_tl: otio.schema.Timeline, playlist
    ) -> None:
        """
        Create xStudio bookmarks for annotation clips already present in a snapshot.

        ``_apply_remote_annotation`` only fires for *new* ``insert_child`` events
        received after joining.  Annotation clips that arrived inside the initial
        state snapshot must be converted to bookmarks here, immediately after the
        playlist is created from the OTIO timeline.

        :param otio_tl: The OTIO timeline that was just loaded into xStudio.
        :param playlist: The xStudio playlist created from *otio_tl*.
        """
        if not self.manager:
            return
        # Build a name → media lookup from the playlist so each annotation clip
        # can find its target without re-scanning for every clip.
        try:
            name_to_media: dict = {m.name: m for m in playlist.media}
        except Exception:
            _log_exc("_load_snapshot_annotations: could not iterate playlist.media")
            return

        _log(f"  Playlist media names: {list(name_to_media.keys())}")

        # Group annotation clips by (clip_guid, frame) — old snapshots may have
        # multiple separate clips per frame (one per stroke) because the Gap/merge
        # logic was not yet in place.  Grouping ensures we create one bookmark per
        # frame regardless of how many clips represent it.
        groups: dict[tuple, dict] = {}  # (clip_guid, frame) → {commands, fps, media}
        for track in otio_tl.tracks:
            if "annotation" not in track.name.lower():
                continue
            for ann_clip in track:
                if not isinstance(ann_clip, otio.schema.Clip):
                    continue
                commands = ann_clip.metadata.get("annotation_commands")
                if not commands:
                    continue
                clip_guid = ann_clip.metadata.get("clip_guid")
                if not clip_guid:
                    continue

                otio_clip = self.manager._object_map.get(clip_guid)
                if otio_clip is None:
                    _log(f"  Snapshot ann: clip_guid {clip_guid[:8]} not in object_map")
                    continue
                media = name_to_media.get(otio_clip.name)
                if media is None:
                    _log(
                        f"  Snapshot ann: no playlist media named {otio_clip.name!r}"
                        f" (available: {list(name_to_media.keys())})"
                    )
                    continue

                frame = 0
                fps = 25.0
                if ann_clip.source_range:
                    frame = int(ann_clip.source_range.start_time.value)
                    rate = ann_clip.source_range.start_time.rate
                    if rate and rate > 0:
                        fps = float(rate)

                key = (clip_guid, frame)
                if key in groups:
                    groups[key]["commands"].extend(commands)
                else:
                    groups[key] = {
                        "commands": list(commands),
                        "fps": fps,
                        "frame": frame,
                        "media": media,
                        "clip_guid": clip_guid,
                        "clip_name": otio_clip.name,
                    }

        count = 0
        for (clip_guid, frame), grp in groups.items():
            media = grp["media"]
            fps = grp["fps"]
            aspect_half = 0.8889
            try:
                ms = media.media_source()
                streams = ms.streams()
                if streams:
                    res = streams[0].media_stream_detail.resolution()
                    if res.y > 0:
                        aspect_half = res.x / (2.0 * res.y)
            except Exception:
                pass

            pen_strokes = sync_events_to_xs_strokes(grp["commands"], aspect_half)
            captions = sync_events_to_xs_captions(grp["commands"], aspect_half)
            if not pen_strokes and not captions:
                continue

            try:
                bm = self.connection.api.session.bookmarks.add_bookmark(target=media)
                detail = BookmarkDetail()
                detail.start = datetime.timedelta(seconds=frame / fps)
                detail.duration = datetime.timedelta(seconds=0.9 / fps)
                self.connection.request_receive(bm.remote, bookmark_detail_atom(), detail)
                bm.set_annotation(strokes=pen_strokes, captions=captions)
                self._annotation_bookmarks[(clip_guid, frame)] = bm
                with self._our_bookmark_uuids_lock:
                    self._our_bookmark_uuids.add(bm.uuid)
                self._our_bookmark_clip_frame[str(bm.uuid)] = (clip_guid, frame)
                # Pre-populate with xStudio's quantized readback so the first
                # periodic scan sees these captions as already broadcast.
                if captions:
                    try:
                        rb = bm.annotation_data
                        if rb:
                            rb_caps = rb.get("Data", rb).get("captions", [])
                            if rb_caps:
                                self._last_sent_captions[f"{clip_guid}:{frame}"] = (
                                    self._caption_signature(rb_caps)
                                )
                    except Exception:
                        pass
                count += 1
            except Exception:
                _log_exc(
                    f"  Snapshot ann: failed bookmark for {grp['clip_name']!r} f{frame}"
                )

        if count:
            _log(f"  Loaded {count} snapshot annotation(s) as bookmarks")

    def _refresh_annotation_bookmark(
        self, merged_clip: otio.schema.Clip
    ) -> None:
        """Re-render an existing bookmark after new commands were merged into *merged_clip*.

        Called when the manager fires ``annotation_commands_added`` — the clip
        already holds the full merged command list; we just need to re-derive the
        strokes and overwrite the bookmark's annotation canvas.

        :param merged_clip: The annotation clip in the manager's timeline, now
            containing all commands including the newly merged ones.
        """
        frame = 0
        if merged_clip.source_range:
            frame = int(merged_clip.source_range.start_time.value)

        clip_guid = merged_clip.metadata.get("clip_guid")
        if not clip_guid:
            return

        bm_key = (clip_guid, frame)
        bm = self._annotation_bookmarks.get(bm_key)
        if bm is None:
            _log(f"_refresh_annotation_bookmark: no tracked bookmark for {bm_key}")
            return

        media, aspect_half = self._find_media_for_clip_guid(clip_guid)
        if media is None:
            return

        all_commands = merged_clip.metadata.get("annotation_commands", [])
        pen_strokes = sync_events_to_xs_strokes(all_commands, aspect_half)
        captions = sync_events_to_xs_captions(all_commands, aspect_half)
        if not pen_strokes and not captions:
            return

        try:
            self._bookmark_strokes_cache[bm_key] = pen_strokes
            self._bookmark_captions_cache[bm_key] = captions
            bm.set_annotation(strokes=pen_strokes, captions=captions)
            _log(
                f"Refreshed annotation bookmark: {len(pen_strokes)} stroke(s), {len(captions)} caption(s)"
                f" at frame {frame}"
            )
        except Exception:
            _log_exc("_refresh_annotation_bookmark: failed")

    def _apply_partial_annotation_xs(self, payload: dict) -> None:
        """Render a mid-stroke partial annotation from a remote peer (visual only).

        Constructs a temporary OTIO Clip from the payload and delegates to
        :meth:`_apply_remote_annotation`, which handles both create and
        update-in-place for the xStudio bookmark.  The clip is never inserted
        into the timeline — it is used only to carry frame/fps/clip_guid.

        Because :meth:`_apply_remote_annotation` adds the bookmark UUID to
        ``_our_bookmark_uuids``, the periodic scan will not re-broadcast the
        partial stroke as a local annotation.

        :param payload: Dict with ``clip_guid``, ``frame``, ``fps``, ``events``.
        """
        clip_guid = payload.get("clip_guid")
        frame = float(payload.get("frame", 0))
        fps = float(payload.get("fps", 25.0))
        events_raw = payload.get("events", [])

        if not clip_guid or not events_raw:
            return

        commands: list = []
        for ev_dict in events_raw:
            try:
                if isinstance(ev_dict, dict):
                    # Use json.dumps → read_from_string (the correct round-trip for a
                    # plain OTIO-JSON dict).  write_to_string expects a SerializableObject
                    # and would fail on a plain Python dict.
                    ev_dict = otio.adapters.read_from_string(
                        json.dumps(ev_dict), "otio_json"
                    )
                commands.append(ev_dict)
            except Exception as e:
                _log(f"_apply_partial_annotation_xs: failed to deserialise event: {e}")

        if not commands:
            return

        temp_clip = otio.schema.Clip()
        temp_clip.source_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(frame, fps),
            otio.opentime.RationalTime(1.0, fps),
        )
        temp_clip.metadata["clip_guid"] = clip_guid

        self._apply_remote_annotation(temp_clip, commands)

    def _apply_remote_annotation(
        self, ann_clip: otio.schema.Clip, commands: list
    ) -> None:
        """
        Convert a received annotation clip into an xStudio bookmark with strokes.

        Uses the xStudio bookmark API (``Bookmarks.add_bookmark`` +
        ``Bookmark.set_annotation``) rather than raw actor messaging, which
        mirrors how ``ori_annotations.py`` reads and writes annotation data.

        :param ann_clip: The 1-frame annotation clip inserted into the Annotations track.
        :param commands: Sequence of SyncEvent objects (``PaintStart``, ``PaintPoints``).
        """
        frame = 0
        fps = 25.0
        if ann_clip.source_range:
            frame = int(ann_clip.source_range.start_time.value)
            rate = ann_clip.source_range.start_time.rate
            if rate and rate > 0:
                fps = float(rate)

        clip_guid = ann_clip.metadata.get("clip_guid")
        if not clip_guid:
            _log("_apply_remote_annotation: no clip_guid in metadata — skipping")
            return

        media, aspect_half = self._find_media_for_clip_guid(clip_guid)
        if media is None:
            _log(
                f"_apply_remote_annotation: no xStudio media for clip {clip_guid[:8]}"
            )
            return

        pen_strokes = sync_events_to_xs_strokes(commands, aspect_half)
        captions = sync_events_to_xs_captions(commands, aspect_half)
        _log(f"DEBUG: parsed pen_strokes: {pen_strokes}")
        if not pen_strokes and not captions:
            _log("_apply_remote_annotation: no strokes or captions decoded — skipping")
            return

        bm_key = (clip_guid, frame)
        existing_bm = self._annotation_bookmarks.get(bm_key)
        try:
            if existing_bm is not None:
                # Retrieve existing strokes from cache, falling back to reading from bookmark.
                cached_strokes = self._bookmark_strokes_cache.get(bm_key)
                if cached_strokes is None:
                    cached_strokes = []
                    ann_data = existing_bm.annotation_data
                    if ann_data:
                        canvas = ann_data.get("Data", ann_data)
                        cached_strokes = canvas.get("pen_strokes", [])

                cached_captions = self._bookmark_captions_cache.get(bm_key)
                if cached_captions is None:
                    cached_captions = []
                    ann_data = existing_bm.annotation_data
                    if ann_data:
                        canvas = ann_data.get("Data", ann_data)
                        cached_captions = canvas.get("captions", [])

                # Merge strokes: replace by UUID if matched, otherwise append.
                merged_strokes = list(cached_strokes)
                for new_s in pen_strokes:
                    uuid_val = new_s.get("uuid")
                    replaced = False
                    if uuid_val:
                        for idx, s in enumerate(merged_strokes):
                            if s.get("uuid") == uuid_val:
                                merged_strokes[idx] = new_s
                                replaced = True
                                break
                    if not replaced:
                        merged_strokes.append(new_s)

                # Merge captions: replace by UUID if matched, otherwise append.
                merged_captions = list(cached_captions)
                for new_c in captions:
                    uuid_val = new_c.get("uuid")
                    replaced = False
                    if uuid_val:
                        for idx, c in enumerate(merged_captions):
                            if c.get("uuid") == uuid_val:
                                merged_captions[idx] = new_c
                                replaced = True
                                break
                    if not replaced:
                        merged_captions.append(new_c)

                self._bookmark_strokes_cache[bm_key] = merged_strokes
                self._bookmark_captions_cache[bm_key] = merged_captions

                existing_bm.set_annotation(strokes=merged_strokes, captions=merged_captions)
                _log(
                    f"Updated annotation bookmark (non-destructive): {len(merged_strokes)} stroke(s), {len(merged_captions)} caption(s)"
                    f" at frame {frame}"
                )
                target_bm = existing_bm
            else:
                bm = self.connection.api.session.bookmarks.add_bookmark(target=media)
                # Set start and duration in a single BookmarkDetail message.
                detail = BookmarkDetail()
                detail.start = datetime.timedelta(seconds=frame / fps)
                detail.duration = datetime.timedelta(seconds=0.9 / fps)
                self.connection.request_receive(bm.remote, bookmark_detail_atom(), detail)
                try:
                    readback = bm.detail
                    _log(
                        f"  Bookmark timing: start={readback.start},"
                        f" duration={readback.duration}"
                    )
                except Exception:
                    pass

                self._bookmark_strokes_cache[bm_key] = pen_strokes
                self._bookmark_captions_cache[bm_key] = captions

                bm.set_annotation(strokes=pen_strokes, captions=captions)
                self._annotation_bookmarks[bm_key] = bm
                with self._our_bookmark_uuids_lock:
                    self._our_bookmark_uuids.add(bm.uuid)
                _log(
                    f"Applied remote annotation: {len(pen_strokes)} stroke(s)"
                    f" at frame {frame}"
                )
                target_bm = bm
            self._our_bookmark_clip_frame[str(target_bm.uuid)] = (clip_guid, frame)
            # Pre-populate caption signature using xStudio's quantized readback
            if captions:
                try:
                    rb = target_bm.annotation_data
                    if rb:
                        rb_caps = rb.get("Data", rb).get("captions", [])
                        if rb_caps:
                            self._last_sent_captions[f"{clip_guid}:{frame}"] = (
                                self._caption_signature(rb_caps)
                            )
                except Exception:
                    pass
        except Exception:
            _log_exc("_apply_remote_annotation: failed to set annotation")

    def _clip_guid_for_media_name(self, media_name: str) -> "str | None":
        """Return the OTIO clip GUID for an xStudio media item by its display name.

        Handles two cases:
        - Normal playlists (loaded via ``load_otio``): media name == OTIO clip name.
        - Flat playlists (loaded via ``add_media``): xStudio uses the full file path
          as the media name.  Falls back to basename-stem comparison.

        :param media_name: ``media.name`` as returned by xStudio.
        :returns: GUID string, or ``None`` if not found.
        :rtype: str or None
        """
        import os
        stem = os.path.splitext(os.path.basename(media_name))[0]
        for otio_tl in self.manager.timelines.values():
            for track in otio_tl.tracks:
                for child in track:
                    if not isinstance(child, otio.schema.Clip):
                        continue
                    cname = child.name or ""
                    if cname == media_name or cname == stem:
                        return child.metadata.get("sync", {}).get("guid")
        return None

    def _find_media_for_clip_guid(
        self, clip_guid: str
    ) -> tuple:
        """
        Look up the xStudio media item corresponding to an OTIO clip GUID.

        Searches all synced playlists for a media item whose name matches the
        OTIO clip name.  Also derives ``aspect_half`` (``W / (2H)``) from the
        media stream resolution so that coordinate conversion is accurate.

        :param clip_guid: Sync GUID of the OTIO media clip.
        :returns: ``(media, aspect_half)`` or ``(None, 0.8889)`` on failure.
        :rtype: tuple
        """
        if not self.manager:
            return None, 0.8889
        otio_clip = self.manager._object_map.get(clip_guid)
        if otio_clip is None:
            _log(f"_find_media_for_clip_guid: {clip_guid[:8]} not in object_map")
            return None, 0.8889
        clip_name = getattr(otio_clip, "name", None)

        def _aspect(media):
            try:
                ms = media.media_source()
                streams = ms.streams()
                if streams:
                    res = streams[0].media_stream_detail.resolution()
                    if res.y > 0:
                        return res.x / (2.0 * res.y)
            except Exception:
                pass
            return 0.8889

        # Fast path: direct GUID→Media mapping populated for flat playlists.
        if clip_guid in self._flat_clip_to_media:
            media = self._flat_clip_to_media[clip_guid]
            return media, _aspect(media)

        # Slow path: scan all playlists by name, path, or URI.
        import os
        clip_stem = os.path.splitext(os.path.basename(clip_name or ""))[0]
        clip_uri = ""
        clip_path = ""
        mr = getattr(otio_clip, "media_reference", None)
        if isinstance(mr, otio.schema.ExternalReference):
            clip_uri = mr.target_url or ""
            clip_path = _uri_to_posix_path(clip_uri)

        for playlist, _ in self._sync_playlists.values():
            try:
                for media in playlist.media:
                    mname = media.name or ""
                    if mname == clip_name or os.path.splitext(os.path.basename(mname))[0] == clip_stem:
                        return media, _aspect(media)
                    try:
                        ms = media.media_source()
                        m_uri = str(ms.media_reference.uri())
                        m_path = _uri_to_posix_path(m_uri)
                        if (clip_uri and m_uri == clip_uri) or (clip_path and m_path == clip_path):
                            return media, _aspect(media)
                    except Exception:
                        pass
            except Exception:
                _log_exc("_find_media_for_clip_guid: error scanning playlist")
        return None, 0.8889

    def _apply_flat_playlist_move(
        self,
        tl_guid: str,
        xs_playlist,
        otio_tl: otio.schema.Timeline,
        to_index: int,
    ) -> None:
        """Reorder a media item in a flat xStudio Playlist to match a MOVE_CHILD event.

        The OTIO track has already been updated by the manager.  We read the
        new clip order from the OTIO track, find the corresponding xStudio
        Media objects by name, and call ``playlist.move_media`` so that the
        bin order matches.

        :param tl_guid: GUID of the flat-playlist OTIO timeline.
        :param xs_playlist: xStudio Playlist object.
        :param otio_tl: Updated OTIO Timeline (MOVE_CHILD already applied).
        :param to_index: Target index from the MOVE_CHILD payload.
        """
        video_track = next(
            (t for t in otio_tl.tracks if t.kind == otio.schema.TrackKind.Video),
            None,
        )
        if video_track is None:
            return

        ordered_clips = [c for c in video_track if isinstance(c, otio.schema.Clip)]
        if to_index >= len(ordered_clips):
            return

        # Resolve clip GUIDs → Media objects via the direct mapping built at load
        # time.  This avoids fragile name matching when xStudio stores the full
        # file path as the media name (which happens after add_media(path)).
        def _media_for_clip(clip):
            cg = clip.metadata.get("sync", {}).get("guid", "")
            return self._flat_clip_to_media.get(cg)

        moved_media = _media_for_clip(ordered_clips[to_index])
        if not moved_media:
            _log(f"flat playlist move: no Media for clip {ordered_clips[to_index].name!r}")
            return

        before_media = None
        if to_index + 1 < len(ordered_clips):
            before_media = _media_for_clip(ordered_clips[to_index + 1])

        if before_media:
            xs_playlist.move_media(moved_media, before=before_media)
        else:
            xs_playlist.move_media(moved_media)  # move to end

        _log(f"flat playlist move: {ordered_clips[to_index].name!r} → index {to_index}")

    def _apply_flat_playlist_insert(
        self, clip_obj: "otio.schema.Clip", xs_playlist, xs_timeline
    ) -> None:
        """Add a newly-broadcast clip to a flat xStudio Playlist.

        Called when an INSERT_CHILD event arrives for a clip that belongs to a
        flat-playlist track.  Adds the media via ``add_media(path)``, records
        the GUID→Media mapping, then adds the media to the Timeline child so
        it appears in the sequence panel.

        :param clip_obj: The inserted OTIO Clip (manager has already inserted it
            into the OTIO track).
        :param xs_playlist: xStudio Playlist to add the media to.
        :param xs_timeline: xStudio Timeline child to add the media to.
        """
        mr = clip_obj.media_reference
        if not isinstance(mr, otio.schema.ExternalReference):
            return
        path = _uri_to_posix_path(mr.target_url or "")
        if not path:
            return
        try:
            media_obj = xs_playlist.add_media(path)
            clip_guid = clip_obj.metadata.get("sync", {}).get("guid", "")
            if clip_guid and media_obj:
                self._flat_clip_to_media[clip_guid] = media_obj
            if xs_timeline is not None:
                try:
                    xs_timeline.add_media(media_obj)
                except Exception:
                    _log_exc(f"flat insert: could not add {clip_obj.name!r} to timeline")
            _log(f"flat playlist insert: {clip_obj.name!r} ← {path!r}")
        except Exception:
            _log_exc(f"flat playlist insert: add_media failed for {path!r}")

    def _apply_sequence_insert(
        self, tl_guid: str, otio_tl: "otio.schema.Timeline", xs_timeline
    ) -> None:
        """Reload an xStudio sequence Timeline after a remote clip insertion.

        The manager has already inserted the new OTIO Clip into the track.
        We re-serialise the OTIO and call ``load_otio(clear=True)`` — the same
        approach used for MOVE_CHILD on sequences.

        :param tl_guid: GUID of the affected OTIO timeline.
        :param otio_tl: Updated OTIO Timeline.
        :param xs_timeline: xStudio Timeline to reload.
        """
        try:
            self._fill_source_ranges(otio_tl)
            otio_str = otio.adapters.write_to_string(otio_tl, "otio_json")
            self._reload_suppress_until = time.monotonic() + 2.0
            xs_timeline.load_otio(otio_str, clear=True)
            try:
                self.connection.api.session.set_on_screen_source(xs_timeline)
            except Exception:
                pass
            _log(f"sequence insert: reloaded timeline {tl_guid[:8]}")
        except Exception:
            self._reload_suppress_until = 0.0
            _log_exc(f"sequence insert: failed to reload timeline {tl_guid[:8]}")

    def _apply_remote_move_child(self, data: dict) -> None:
        """Reorder a media clip in the xStudio timeline to match a remote MOVE_CHILD event.

        ``track.move_children`` triggers xStudio's QML delegate model directly
        and causes "index out of range" errors in the timeline panel.  Instead
        we re-serialise the updated OTIO timeline (the manager has already
        applied the reorder) and call ``load_otio`` with ``clear=True``.

        ``load_otio`` rebuilds the xStudio Timeline's tracks and clips without
        touching the Playlist's Media items or the session's bookmarks, so
        annotation state survives intact.  The Annotations track clips will be
        reloaded at their OTIO-stored sequence positions, which are not updated
        by the manager on MOVE_CHILD — the same limitation exists today and is
        acceptable because xStudio renders annotations via bookmarks (which are
        clip-relative) rather than timeline track clips.

        :param data: Payload dict with keys ``parent_uuid``, ``child_uuid``, ``to_index``.
        """
        parent_uuid = data.get("parent_uuid")
        child_uuid = data.get("child_uuid")
        to_index: int = data.get("to_index", 0)

        if not parent_uuid or not child_uuid:
            return

        # Find the OTIO timeline that owns the reordered Media track.
        tl_guid = None
        for guid, tl in self.manager.timelines.items():
            for track in tl.tracks:
                if track.metadata.get("sync", {}).get("guid") == parent_uuid:
                    tl_guid = guid
                    break
            if tl_guid:
                break

        if tl_guid is None:
            _log(f"move_child: no timeline found for track {parent_uuid[:8]}")
            return

        playlist_tuple = self._sync_playlists.get(tl_guid)
        if playlist_tuple is None:
            _log(f"move_child: no xStudio playlist for timeline {tl_guid[:8]}")
            return
        xs_playlist, xs_timeline = playlist_tuple

        otio_tl = self.manager.timelines.get(tl_guid)
        if otio_tl is None:
            _log(f"move_child: timeline {tl_guid[:8]} not in manager.timelines")
            return

        # Flat playlists: reorder the media bin with move_media.
        # Their xStudio Timeline was built from add_media calls (not load_otio),
        # so load_otio cannot be used to reorder it.
        if xs_timeline is None or otio_tl.metadata.get("xs_flat_playlist"):
            self._apply_flat_playlist_move(tl_guid, xs_playlist, otio_tl, to_index)
            return

        try:
            self._fill_source_ranges(otio_tl)
            otio_str = otio.adapters.write_to_string(otio_tl, "otio_json")
            # Suppress show_atom bursts that xStudio fires when it re-triggers
            # existing bookmarks after the timeline is rebuilt.
            self._reload_suppress_until = time.monotonic() + 2.0
            xs_timeline.load_otio(otio_str, clear=True)
            # Re-activate the timeline in the UI — load_otio does not restore
            # the viewed source automatically.
            try:
                self.connection.api.session.set_on_screen_source(xs_timeline)
            except Exception:
                pass
            _log(f"move_child: reloaded timeline {tl_guid[:8]} — {child_uuid[:8]} now at index {to_index}")
        except Exception:
            self._reload_suppress_until = 0.0
            _log_exc(f"move_child: failed to reload timeline {tl_guid[:8]}")
            return

        # Keep tracked order in sync with the new OTIO Media track order.
        media_track = next(
            (t for t in otio_tl.tracks if t.name == "Media"), None
        )
        if media_track is not None:
            self._xs_media_order[tl_guid] = [
                c.metadata.get("sync", {}).get("guid")
                for c in media_track
                if isinstance(c, otio.schema.Clip)
            ]

    # ── OTIO helpers ───────────────────────────────────────────────────────────

    def _resolve_clip_at_frame(
        self,
        timeline: otio.schema.Timeline,
        frame: int,
    ) -> tuple[str | None, otio.opentime.RationalTime | None]:
        """
        Return ``(clip_guid, clip_local_time)`` for the media clip at *frame*.

        *frame* is 0-based (xStudio convention).  Returns ``(None, None)``
        when the frame cannot be resolved to any clip in the first content track.
        """
        fps = 24.0
        if self.active_playhead:
            fps = self.active_playhead.frame_rate.fps() or fps

        global_time = otio.opentime.RationalTime(frame, fps)
        try:
            for track in timeline.tracks:
                if "annotation" in track.name.lower():
                    continue
                for clip in track:
                    if not hasattr(clip, "source_range") or clip.source_range is None:
                        continue
                    clip_range = clip.range_in_parent()
                    if clip_range.contains(global_time):
                        clip_guid = clip.metadata.get("sync", {}).get("guid")
                        # clip_local_time: position relative to clip's source_range start
                        clip_local_time = otio.opentime.RationalTime(
                            global_time.value - clip_range.start_time.value,
                            fps,
                        )
                        return clip_guid, clip_local_time
        except Exception:
            _log_exc("_resolve_clip_at_frame error")
        return None, None


# ── xStudio entry points ───────────────────────────────────────────────────────


def create_plugin_instance(connection):
    return ORISyncPlugin(connection)


if __name__ == "__main__":
    XSTUDIO = Connection(auto_connect=True)
    create_plugin_instance(XSTUDIO)
    XSTUDIO.link.run_xstudio_message_loop()
