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

import datetime
import json
import logging
import os
import queue
import sys
import threading
import time
import uuid

import opentimelineio as otio
from xstudio.connection import Connection
from xstudio.core import (
    BookmarkDetail,
    LoopMode,
    annotation_atom,
    bookmark_detail_atom,
    event_atom,
    serialise_atom,
    show_atom,
    viewport_playhead_atom,
)
from xstudio.api.session.playhead import Playhead
from xstudio.api.intrinsic.viewport import Viewport

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

from otio_sync_core.manager import (  # noqa: E402
    STATE_DISCOVERING,
    STATE_SYNCED,
    SyncManager,
    sync_event_schema,
)
from otio_sync_core.rabbitmq_network import RabbitMQNetwork  # noqa: E402
from xstudio.plugin import PluginBase  # noqa: E402

SyncEvent = otio.schema.schemadef.module_from_name("SyncEvent")

# ── logging ────────────────────────────────────────────────────────────────────


def _make_logger() -> logging.Logger:
    logger = logging.getLogger("ori_sync")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d  %(message)s", datefmt="%H:%M:%S")
    # Always attach a console handler so output is visible in xStudio's Python output.
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    # ORI_SYNC_LOG_FILE adds a persistent file alongside the console output,
    # mirroring the RV_OTIO_SYNC_LOG_FILE pattern in the RV plugin.
    log_file = os.environ.get("ORI_SYNC_LOG_FILE")
    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


_logger = _make_logger()


def _log(msg: str) -> None:
    _logger.debug(msg)


def _log_exc(msg: str) -> None:
    _logger.exception(msg)


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
    ANNOTATION_SCAN_INTERVAL = 0.1

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

        # Commands enqueued by xStudio callbacks; drained by poll thread.
        # Items are (command_name, payload_dict).
        self._cmd_queue: queue.Queue[tuple[str, dict]] = queue.Queue()

        # Tracks the xStudio Bookmark created for each (clip_guid, frame) pair
        # so that additional strokes on the same frame can be merged into the
        # existing bookmark rather than creating a new one.
        self._annotation_bookmarks: dict[tuple, object] = {}

        # UUIDs of bookmarks we created from *remote* annotations.
        # show_atom scans skip these so we never re-broadcast them back.
        self._our_bookmark_uuids: set = set()
        self._our_bookmark_uuids_lock = threading.Lock()

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
        self._hot_scan_last_change: float = 0.0

        # Polling-based scrub detection: last frame seen by the poll loop and
        # last frame applied from a remote PLAYBACK_SETTINGS message.
        # When the poll sees a frame change that matches _last_applied_frame the
        # change came from a remote apply, so we skip re-broadcasting (echo guard).
        self._last_polled_frame: int | None = None
        self._last_applied_frame: int | None = None

        # Last display state broadcast; compared each poll tick to detect changes.
        self._last_display_state: dict = {}
        # xStudio's internal viewport scale at the first successful read.  Used
        # to normalise state_.scale_ (which is image_pixels/viewport_pixels, not
        # a zoom multiplier) to RV's convention (1.0 = fit-to-window).
        self._xs_base_scale: float | None = None
        # Cached Viewport object; created lazily, cleared on disconnect.
        self._viewport: "Viewport | None" = None

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

        # Self-elect if no master answers within DISCOVERY_TIMEOUT.
        threading.Thread(
            target=self._discovery_timeout_task, daemon=True
        ).start()

        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="ori_sync_poll", daemon=True
        )
        self._poll_thread.start()

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
        self._last_display_state = {}
        self._xs_base_scale = None
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
            tl = self._build_otio_timeline()
            if tl:
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
                self._poll_and_broadcast_display()
            except Exception:
                _log_exc("Poll loop error")
            self._poll_stop.wait(self.POLL_INTERVAL)

    def _drain_cmd_queue(self) -> None:
        """Execute all enqueued commands on the poll thread."""
        while True:
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
                tl = self._build_otio_timeline()
                if tl:
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

        elif action == "display_settings":
            self._apply_display_state(data)

        elif action == "selection_changed":
            _log(f"Remote selection: {data.get('clip_guid', '?')}")

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

        first_xs_timeline = None
        for guid, otio_tl in self.manager.timelines.items():
            if guid in self._sync_playlists:
                continue  # already created

            name = otio_tl.name or guid[:8]

            # Backfill source_range so xStudio can position and size each clip.
            self._fill_source_ranges(otio_tl)

            tracks = list(otio_tl.tracks)
            _log(f"OTIO Timeline {name!r}: {len(tracks)} track(s)")
            for i, track in enumerate(tracks):
                children = list(track)
                _log(f"  Track {i} {track.name!r} kind={track.kind}: {len(children)} child(ren)")
                for j, child in enumerate(children[:8]):
                    sr = getattr(child, "source_range", None)
                    _log(f"    [{j}] {type(child).__name__} {getattr(child, 'name', '?')!r} sr={sr}")

            try:
                playlist = self.connection.api.session.create_playlist(name)[1]
                xs_timeline = playlist.create_timeline(name)[1]
                otio_str = otio.adapters.write_to_string(otio_tl, "otio_json")
                xs_timeline.load_otio(otio_str, clear=True)
                self._sync_playlists[guid] = (playlist, xs_timeline)
                if first_xs_timeline is None:
                    first_xs_timeline = xs_timeline
                _log(f"Created playlist {name!r} from OTIO timeline {guid[:8]}")
                # Convert any annotation clips already in the snapshot to bookmarks.
                self._load_snapshot_annotations(otio_tl, playlist)
            except Exception:
                _log_exc(f"Failed to create playlist for {name!r}")

        if first_xs_timeline is not None:
            try:
                self.connection.api.session.set_on_screen_source(first_xs_timeline)
            except Exception:
                pass

    # ── OTIO construction ──────────────────────────────────────────────────────

    def _build_otio_timeline(self) -> otio.schema.Timeline | None:
        """Convert the current xStudio session into an OTIO Timeline."""
        try:
            container = self.connection.api.session.viewed_container
            if container is None:
                return otio.schema.Timeline(name="ori-sync")

            if hasattr(container, "to_otio_string"):
                otio_str = container.to_otio_string()
            else:
                from xstudio.api.auxiliary.otio import timeline_to_otio_string
                otio_str = timeline_to_otio_string(container)

            tl = otio.adapters.read_from_string(otio_str)
            _log(f"Built OTIO timeline: {tl.name!r}")
            return tl
        except Exception:
            _log_exc("Could not build OTIO from xStudio session — using empty timeline")
            return otio.schema.Timeline(name="ori-sync")

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

        # show_atom: fires when a bookmark/annotation is shown or created.
        # This is the signal that the user has drawn in xStudio.
        if isinstance(event[1], show_atom):
            _log("show_atom fired — queuing annotation flush + activating hot scan")
            if self.manager and self.manager.status == STATE_SYNCED:
                self._annotation_pending_time = time.monotonic()
                # Start hot-scanning the current frame on every poll tick so that
                # partial strokes are streamed before pen-up.
                try:
                    if self.active_playhead:
                        self._hot_scan_frame = self.active_playhead.position
                        self._hot_scan_active = True
                        self._hot_scan_last_change = time.monotonic()
                        _log(f"Hot scan activated at frame {self._hot_scan_frame}")
                except Exception:
                    pass
            return

        if not isinstance(event[1], viewport_playhead_atom):
            return
        # Only Form 2 carries a reliable playhead: (event_atom, viewport_playhead_atom,
        # viewport_name, playhead_actor).  Form 1 (len==3) omits the viewport name and
        # its playhead actor may differ from the one the user is actually scrubbing.
        if len(event) <= 3:
            return
        ph_remote = event[3]
        try:
            self.active_playhead = Playhead(self.connection, ph_remote)
            _log("Active playhead updated (form=2)")
        except Exception:
            _log_exc("_on_global_playhead_event: failed to update playhead")

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
            if self.active_playhead.playing:
                return
            frame: int = self.active_playhead.position
            fps: float = self.active_playhead.frame_rate.fps() or 25.0
        except Exception:
            return
        if frame == self._last_polled_frame:
            return
        self._last_polled_frame = frame
        if frame == self._last_applied_frame:
            # This change was caused by _apply_playback_state — skip re-broadcast.
            return
        state = {
            "playing": False,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(frame),
                "rate": fps,
            },
            "looping": False,
        }
        _log(f"Poll: broadcasting playback frame={frame} fps={fps}")
        self.manager.broadcast_playback_state(state)

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

    def _apply_playback_state(self, state: dict) -> None:
        """Apply an incoming playback state dict to the local xStudio playhead.

        Called from the poll thread via the ``on_playback_changed`` callback.
        xStudio's actor-based attribute writes are thread-safe.

        Updates ``_last_applied_frame`` and ``_last_polled_frame`` so that
        ``_poll_and_broadcast_frame`` recognises the resulting position change
        as a remote apply and does not echo it back to the session.
        """
        if not self.active_playhead:
            return
        playing = state.get("playing", False)
        current_time = state.get("current_time", {})
        # Protocol value is 0-based (RV sends frame-1; xStudio frames are 0-based).
        frame = max(0, int(current_time.get("value", 0)))

        if playing != self.active_playhead.playing:
            self.active_playhead.playing = playing
        if not playing:
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
            return self._viewport
        try:
            self._viewport = Viewport(self.connection, active_viewport=True)
            _log("Viewport acquired")
        except Exception as e:
            _log(f"_get_viewport: {e}")
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
            js = self.connection.request_receive(vp.remote, serialise_atom())[0]
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
        if not (
            len(data) >= 3
            and isinstance(data[0], event_atom)
            and isinstance(data[1], annotation_atom)
        ):
            return
        if not self.manager or self.manager.status != STATE_SYNCED:
            return
        _log("Annotation event from AnnotationsUI — scheduling broadcast scan")
        self._annotation_pending_time = time.monotonic()

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
        last_sent = self._hot_scan_stroke_counts.get(key, 0)
        if len(all_strokes) <= last_sent:
            return  # no new strokes since last hot broadcast

        self._hot_scan_last_change = now
        self._hot_scan_stroke_counts[key] = len(all_strokes)

        # Ensure UUID cache covers all strokes (including pre-existing ones).
        if key not in self._stroke_uuid_cache:
            self._stroke_uuid_cache[key] = []
        cache = self._stroke_uuid_cache[key]
        while len(cache) < len(all_strokes):
            cache.append(str(uuid.uuid4()))

        _, aspect_half = self._find_media_for_clip_guid(clip_guid)

        # Send ALL current strokes so peers can update from any starting point.
        try:
            otio.schema.schemadef.module_from_name("SyncEvent")
        except Exception:
            pass
        events_obj = self._strokes_to_sync_events(all_strokes, aspect_half, uuid_list=cache)
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
        _log(f"_flush_pending_annotations: scanning {len(scan_uuids)} bookmark(s)")

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
                        self._strokes_to_sync_events(all_strokes, aspect_half, uuid_list=uuid_cache)
                        + self._captions_to_sync_events(all_captions, aspect_half, existing_uuids)
                    )
                    _log(
                        f"Broadcasting annotation replace: {len(all_events)} event(s)"
                        f" (caption edit) at frame={frame} clip={clip_guid[:8]}"
                    )
                    self.manager.broadcast_replace_annotation_commands(ann_clip_guid, all_events)
                    self._last_sent_captions[cap_key] = current_sig
                    return True

        events = (
            self._strokes_to_sync_events(new_strokes, aspect_half, uuid_list=delta_uuids)
            + self._captions_to_sync_events(new_captions, aspect_half)
        )
        if not events:
            _log(f"_broadcast_local_bookmark: no new strokes at frame={frame} — already in timeline")
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

            pen_strokes = self._commands_to_xs_strokes(grp["commands"], aspect_half)
            captions = self._commands_to_xs_captions(grp["commands"], aspect_half)
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
        pen_strokes = self._commands_to_xs_strokes(all_commands, aspect_half)
        captions = self._commands_to_xs_captions(all_commands, aspect_half)
        if not pen_strokes and not captions:
            return

        try:
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
                    ev_dict = otio.adapters.read_from_string(
                        otio.adapters.write_to_string(ev_dict, "otio_json"), "otio_json"
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

        pen_strokes = self._commands_to_xs_strokes(commands, aspect_half)
        captions = self._commands_to_xs_captions(commands, aspect_half)
        if not pen_strokes and not captions:
            _log("_apply_remote_annotation: no strokes or captions decoded — skipping")
            return

        bm_key = (clip_guid, frame)
        existing_bm = self._annotation_bookmarks.get(bm_key)
        try:
            if existing_bm is not None:
                existing_bm.set_annotation(strokes=pen_strokes, captions=captions)
                _log(
                    f"Updated annotation bookmark: {len(pen_strokes)} stroke(s), {len(captions)} caption(s)"
                    f" at frame {frame}"
                )
                target_bm = existing_bm
            else:
                bm = self.connection.api.session.bookmarks.add_bookmark(target=media)
                # Set start and duration in a single BookmarkDetail message.
                # Doing them separately fires two bookmark_change_atom events and risks
                # a full_bookmarks_update running between them with duration=k_flicks_max
                # (which makes the annotation appear to hold for the whole media).
                # Duration < 1 frame: floor(0.9) = 0 → end_frame = start_frame (1-frame display).
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
            # so the next periodic scan doesn't re-broadcast these remote captions.
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
        _log(f"_find_media_for_clip_guid: looking for {clip_name!r}")

        for playlist, _ in self._sync_playlists.values():
            try:
                available = [m.name for m in playlist.media]
                _log(f"  playlist media: {available}")
                for media in playlist.media:
                    if media.name != clip_name:
                        continue
                    aspect_half = 0.8889  # 16:9 fallback
                    try:
                        ms = media.media_source()
                        streams = ms.streams()
                        if streams:
                            res = streams[0].media_stream_detail.resolution()
                            if res.y > 0:
                                aspect_half = res.x / (2.0 * res.y)
                    except Exception:
                        pass
                    return media, aspect_half
            except Exception:
                _log_exc("_find_media_for_clip_guid: error scanning playlist")
        return None, 0.8889

    def _commands_to_xs_strokes(
        self, commands: list, aspect_half: float
    ) -> list:
        """
        Convert a PaintStart / PaintPoints command sequence to xStudio stroke dicts.

        Inverts the H-normalised / Y-up (OTIO/RV) coordinate system to the
        W-normalised / Y-down system that xStudio expects:

        .. code-block:: text

            x_xs = x_otio / aspect_half
            y_xs = -y_otio / aspect_half

        :param commands: Sequence of SyncEvent objects from the annotation clip.
        :param aspect_half: ``W / (2H)`` derived from the target media resolution.
        :returns: List of xStudio pen-stroke dicts suitable for
            :meth:`Bookmark.set_annotation`.
        :rtype: list
        """
        pen_strokes: list[dict] = []
        current_stroke: dict | None = None

        for cmd in commands:
            schema = sync_event_schema(cmd)

            if schema.startswith("PaintStart"):
                rgba = getattr(cmd, "rgba", None) or [1.0, 1.0, 1.0, 1.0]
                # PaintStart has no width field; thickness is set from PaintVertices.size.
                is_erase = getattr(cmd, "type", "color") == "erase"
                current_stroke = {
                    "r": rgba[0] if len(rgba) > 0 else 1.0,
                    "g": rgba[1] if len(rgba) > 1 else 1.0,
                    "b": rgba[2] if len(rgba) > 2 else 1.0,
                    "opacity": rgba[3] if len(rgba) > 3 else 1.0,
                    "thickness": 0.003,
                    "softness": 0.0,
                    "size_sensitivity": 1.0,
                    "opacity_sensitivity": 1.0,
                    "is_erase_stroke": is_erase,
                    "points": [],
                }
                pen_strokes.append(current_stroke)

            # Python class is PaintPoints; serializable label is "PaintPoint.1".
            elif schema.startswith("PaintPoint") and current_stroke is not None:
                points_obj = getattr(cmd, "points", None)
                if points_obj is None:
                    continue
                xs = list(getattr(points_obj, "x", []))
                ys = list(getattr(points_obj, "y", []))
                sizes = list(getattr(points_obj, "size", []))
                raw_pts: list[float] = []
                for x, y in zip(xs, ys):
                    raw_pts.extend([
                        x / aspect_half,
                        -y / aspect_half,
                        1.0,  # size_pressure — protocol has no per-point pressure
                        1.0,  # opacity_pressure — 0.0 makes every point invisible
                    ])
                current_stroke["points"] = raw_pts
                if sizes:
                    current_stroke["thickness"] = sizes[0] / aspect_half

        return pen_strokes

    def _commands_to_xs_captions(
        self, commands: list, aspect_half: float
    ) -> list:
        """
        Convert a TextAnnotation command sequence to xStudio caption dicts.

        Inverts coordinate systems and scales font size.

        :param commands: Sequence of SyncEvent objects from the annotation clip.
        :param aspect_half: ``W / (2H)`` derived from the target media resolution.
        :returns: List of xStudio caption dicts suitable for
            :meth:`Bookmark.set_annotation`.
        :rtype: list
        """
        captions: list[dict] = []
        for cmd in commands:
            schema = sync_event_schema(cmd)

            if schema.startswith("TextAnnotation"):
                rgba = getattr(cmd, "rgba", None)
                if rgba is None and isinstance(cmd, dict):
                    rgba = cmd.get("rgba")
                if not rgba:
                    rgba = [1.0, 1.0, 1.0, 1.0]

                position = getattr(cmd, "position", None)
                if position is None and isinstance(cmd, dict):
                    position = cmd.get("position")
                if not position:
                    position = [0.0, 0.0]

                text = getattr(cmd, "text", None)
                if text is None and isinstance(cmd, dict):
                    text = cmd.get("text")
                if not text:
                    text = ""

                font = getattr(cmd, "font", None)
                if font is None and isinstance(cmd, dict):
                    font = cmd.get("font")
                if not font:
                    font = ""

                font_size = getattr(cmd, "font_size", None)
                if font_size is None and isinstance(cmd, dict):
                    font_size = cmd.get("font_size")
                if font_size is None:
                    font_size = 50.0

                # Convert coordinates and values
                x_xs = float(position[0]) / aspect_half
                y_xs = -float(position[1]) / aspect_half

                captions.append({
                    "colour": ["colour", 1, rgba[0], rgba[1], rgba[2]],
                    "opacity": rgba[3],
                    "position": ["vec2", 1, x_xs, y_xs],
                    "font_name": font,
                    "font_size": float(font_size),
                    "text": text,
                    "wrap_width": 0.0,
                    "justification": 0,
                })
        return captions

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

    # ── stroke / caption conversion ────────────────────────────────────────────

    def _strokes_to_sync_events(
        self,
        pen_strokes: list,
        aspect_half: float = 0.8889,
        uuid_list: "list[str] | None" = None,
    ) -> list:
        """
        Convert xStudio pen_strokes to OTIO SyncEvent objects.

        Mirrors ``ORIAnnotationsExporter._strokes_to_sync_events`` exactly so
        that strokes broadcast here are readable by the existing RV plugin and
        the OTIO export pipeline.

        :param uuid_list: If supplied, use ``uuid_list[i]`` as the UUID for stroke *i*
            instead of generating a fresh one.  Callers that need stable UUIDs across
            repeated broadcasts of the same frame (partial + final) maintain this list
            in ``_stroke_uuid_cache`` and pass the appropriate slice here.

        Coordinate convention:
        - xStudio: W-normalised, Y-down  (X ∈ [−1,+1])
        - OTIO/RV: H-normalised, Y-up    (X ∈ [−aspect/2,+aspect/2])
        Scale factor: ``aspect_half = W / (2H)``
        """
        events = []
        for i, stroke in enumerate(pen_strokes):
            stroke_uuid = (uuid_list[i] if uuid_list and i < len(uuid_list)
                           else str(uuid.uuid4()))
            rgba = [
                stroke.get("r", 1.0),
                stroke.get("g", 1.0),
                stroke.get("b", 1.0),
                stroke.get("opacity", 1.0),
            ]
            thickness = stroke.get("thickness", 0.003)
            is_erase = stroke.get("is_erase_stroke", False)
            raw_pts = stroke.get("points", [])

            xs = [x * aspect_half for x in raw_pts[0::4]]
            ys = [-y * aspect_half for y in raw_pts[1::4]]
            sps = raw_pts[2::4]
            widths = (
                [thickness * aspect_half * sp for sp in sps]
                if xs and any(sp != 0.0 for sp in sps)
                else [thickness * aspect_half] * len(xs)
            )

            start_evt = SyncEvent.PaintStart(
                brush="oval", rgba=rgba, friendly_name="", uuid=stroke_uuid
            )
            if is_erase:
                start_evt.type = "erase"
            events.append(start_evt)
            events.append(
                SyncEvent.PaintPoints(
                    uuid=stroke_uuid,
                    points=SyncEvent.PaintVertices(list(xs), list(ys), widths),
                )
            )
        return events

    def _captions_to_sync_events(
        self,
        captions: list,
        aspect_half: float = 0.8889,
        existing_uuids: "list[str] | None" = None,
    ) -> list:
        """Convert xStudio caption dicts to OTIO TextAnnotation SyncEvents.

        :param captions: xStudio caption dicts from ``bm.annotation_data``.
        :param aspect_half: ``W / (2H)`` coordinate scale factor.
        :param existing_uuids: When provided, reuse these UUIDs (by index) instead
            of generating new ones.  Pass the UUIDs from the OTIO clip when building
            a replacement so that RV can update existing text nodes in place.
        """
        events = []
        for i, caption in enumerate(captions):
            caption_uuid = (
                existing_uuids[i]
                if existing_uuids and i < len(existing_uuids)
                else str(uuid.uuid4())
            )
            colour = caption.get("colour", ["colour", 1, 1.0, 1.0, 1.0])
            if isinstance(colour, list) and len(colour) >= 5:
                r, g, b = float(colour[2]), float(colour[3]), float(colour[4])
            else:
                r, g, b = 1.0, 1.0, 1.0
            opacity = float(caption.get("opacity", 1.0))
            pos = caption.get("position", ["vec2", 1, 0.0, 0.0])
            position = (
                [float(pos[2]) * aspect_half, -float(pos[3]) * aspect_half]
                if isinstance(pos, list) and len(pos) >= 4
                else [0.0, 0.0]
            )
            fs = float(caption.get("font_size", 50.0))
            _log(f"  caption font_size={fs!r} text={caption.get('text', '')!r}")
            events.append(
                SyncEvent.TextAnnotation(
                    rgba=[r, g, b, opacity],
                    position=position,
                    spacing=0.0,
                    friendly_name=caption.get("font_name", ""),
                    font_size=fs,
                    font=caption.get("font_name", ""),
                    text=caption.get("text", ""),
                    rotation=0.0,
                    scale=1.0,
                    uuid=caption_uuid,
                )
            )
        return events


# ── xStudio entry points ───────────────────────────────────────────────────────


def create_plugin_instance(connection):
    return ORISyncPlugin(connection)


if __name__ == "__main__":
    XSTUDIO = Connection(auto_connect=True)
    create_plugin_instance(XSTUDIO)
    XSTUDIO.link.run_xstudio_message_loop()
