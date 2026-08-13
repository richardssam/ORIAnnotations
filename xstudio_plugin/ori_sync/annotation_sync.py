#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""AnnotationSyncController — owns annotation send/receive state and methods."""

import contextlib
import datetime
import json
import threading
import time
import uuid
import opentimelineio as otio
from xstudio.core import (
    BookmarkDetail, bookmark_detail_atom, JsonStore,
    add_bookmark_atom, remove_bookmark_atom, event_atom,
)
from otio_sync_core.manager import STATE_SYNCED, sync_event_schema
from otio_sync_core.xs_annotation_codec import (
    xs_strokes_to_sync_events, xs_captions_to_sync_events,
    sync_events_to_xs_strokes, sync_events_to_xs_captions,
)
from .utils import _log, _log_exc, bounded

# Bounded timeout (ms) for poll-thread bookmark-actor calls (set_annotation,
# annotation_data, add_bookmark, detail).  Well below the 100 s default so a
# busy/unresponsive bookmark actor can't freeze the poll thread; a skipped
# partial render is harmless — the final INSERT_CHILD re-renders the full state.
_ANNOTATION_TIMEOUT_MS = 2000


def _frame_start_timedelta(frame: int, fps: float) -> "datetime.timedelta":
    """Return a ``timedelta`` that lands inside frame *frame*'s time window.

    xStudio derives a bookmark's integer frame back from its stored time via
    ``std::floor(flicks / rate)`` (``FrameRateDuration::frame``) — it never
    rounds. ``frame / fps`` is the frame's exact *leading* edge, so any tiny
    shortfall (``datetime.timedelta`` truncates to microseconds, losing a
    sub-microsecond remainder for almost any non-multiple-of-fps frame) floors
    down to ``frame - 1`` instead of ``frame``. Requesting the frame's
    midpoint instead keeps the value safely inside ``[frame/fps,
    (frame+1)/fps)`` even after that truncation, so it always floors back to
    the intended frame.
    """
    return datetime.timedelta(seconds=(frame + 0.5) / fps)

class AnnotationSyncController:
    """Owns annotation send/receive state and methods.

    :param plugin: Back-reference to the parent ORISyncPlugin instance.
    """

    #: How long to wait after the last annotation_atom before scanning bookmarks.
    DEBOUNCE_SECONDS = 0.25
    #: Minimum interval between outbound partial-stroke broadcasts per
    #: (clip, frame), to coalesce PaintPoint bursts instead of sending one
    #: broadcast per point.
    PARTIAL_BROADCAST_INTERVAL = 0.5
    #: Sentinel stored in ``_last_sent_captions`` immediately after a remote
    #: annotation is applied, before xStudio has committed the annotation_data.
    _CAPTION_SIG_UNCONFIRMED = "\x00unconfirmed\x00"
    #: How long to keep retrying a flush that finds a not-yet-ready bookmark.
    #: Bounded by time rather than attempt count: the old 5-attempt budget was
    #: ~1.25s, and the mapping can legitimately take longer while media loads.
    STALE_RETRY_WINDOW = 10.0

    def __init__(self, plugin):
        self.plugin = plugin

        # ── owned state ───────────────────────────────────────────────
        self._annotation_bookmarks: dict[tuple, object] = {}
        self._bookmark_strokes_cache: dict[tuple, list] = {}
        self._bookmark_captions_cache: dict[tuple, list] = {}
        # UUIDs of bookmarks we created from *remote* annotations.
        # show_atom scans skip these so we never re-broadcast them back.
        self._our_bookmark_uuids: set = set()
        self._our_bookmark_uuids_lock = threading.Lock()
        # Sync GUIDs of annotation clips that THIS peer has created or broadcast to.
        # Used to guard broadcast_replace_annotation_commands: only replace a clip
        # that we own.  If ann_clip_guid is not in this set, use broadcast_add_annotation
        # (parallel annotation) instead of overwriting the remote peer's clip.
        self._our_annotation_clip_guids: set = set()
        self._our_bookmark_clip_frame: dict[str, tuple[str, int]] = {}
        # (clip_guid, frame) -> bookmark uuid str, recorded whenever
        # broadcast_local_bookmark successfully broadcasts for that key.  Used
        # by flush_pending_annotations to detect a bookmark that has since
        # disappeared (the usual outcome of clearing a drawing) and broadcast
        # the clear peers otherwise never receive.
        self._broadcast_annotation_keys: dict[tuple, str] = {}
        self._last_sent_captions: dict[str, str] = {}
        self._annotation_flush_retries: int = 0
        # Deadline while a flush keeps finding a bookmark that is not ready
        # yet (no annotation_data committed, or no clip mapping for its
        # frame).  Bookmark add/remove events arrive as soon as the bookmark
        # exists, which can be before either is true.
        self._annotation_retry_deadline: float | None = None
        self._core_events_received: int = 0
        self._stroke_uuid_cache: dict[str, list] = {}
        self._live_stroke_current_key: str | None = None
        self._last_annotation_scan: float = 0.0
        # Throttle partial set_annotation calls: tracks the last time we actually
        # called bm.set_annotation() for each (clip_guid, frame) key during a
        # partial-update session.  Prevents the poll thread from blocking on
        # increasingly large cumulative stroke lists at every PARTIAL arrival.
        self._last_partial_render_time: dict[tuple, float] = {}

        # Suppresses show_atom annotation flushes so xStudio's bookmark-
        # re-trigger burst is not mistaken for new local strokes. Two parts,
        # replacing the previous single wall-clock deadline (broadcast-
        # ownership, xstudio-plugin-module-structure spec: "apply-scope guard,
        # not a wall-clock window"):
        #
        # * _reload_apply_depth — depth-counted, true for exactly as long as a
        #   remote structural apply (e.g. a load_otio rebuild) is actually in
        #   progress, however long that takes. A fixed deadline armed *before*
        #   a slow rebuild could expire mid-rebuild; this cannot.
        #   See remote_structural_apply_scope().
        # * _reload_residual_until — a short, genuinely-fixed wall-clock window
        #   armed *after* a suppressed operation completes, covering xStudio's
        #   asynchronous echo events that arrive after the call has already
        #   returned (both after a load_otio rebuild and after a single
        #   bookmark set_annotation() call — see arm_reload_residual()).
        self._reload_apply_depth: int = 0
        self._reload_residual_until: float = 0.0

        # Cross-thread annotation trigger: set on xStudio thread by
        # _on_annotation_draw_event; read and cleared on poll thread by
        # flush_pending_annotations.
        self._annotation_pending_time: float | None = None

    def reset_last_scan(self, t: float) -> None:
        """Set the last annotation scan timestamp (called from _on_synced)."""
        self._last_annotation_scan = t
        self._last_partial_render_time.clear()

    def reset(self) -> None:
        """Clear per-session broadcast bookkeeping (called from disconnect()).

        Deliberately does NOT clear the annotation identity/dedup caches
        (``_our_bookmark_uuids``, ``_our_annotation_clip_guids``,
        ``_our_bookmark_clip_frame``, ``_annotation_bookmarks``, the stroke and
        caption caches).  Those record which local bookmarks originated from a
        *remote* peer; dropping them would make the next session's flush scan
        treat those bookmarks as new local annotations and re-broadcast them as
        duplicates.  They survive disconnect today and must keep doing so.
        """
        self._broadcast_annotation_keys.clear()
        # Transient per-session state: guards, counters and scan timestamps.
        self._annotation_pending_time = None
        self._reload_apply_depth = 0
        self._reload_residual_until = 0.0
        self._annotation_flush_retries = 0
        self._annotation_retry_deadline = None
        self._core_events_received = 0
        self._last_annotation_scan = 0.0
        self._last_partial_render_time.clear()
        self._live_stroke_current_key = None

    @contextlib.contextmanager
    def remote_structural_apply_scope(self):
        """Scope around a remote structural apply (e.g. a ``load_otio`` rebuild).

        Active for exactly as long as the apply is actually in progress —
        covering a slow rebuild fully, unlike the previous implementation's
        fixed deadline armed *before* the call, which could expire mid-rebuild
        on a sufficiently large timeline. Reentrant (depth-counted) in case a
        structural apply is ever nested.

        Does **not** by itself cover xStudio's asynchronous echo events that
        arrive after the call returns — call :meth:`arm_reload_residual` on
        the success path for that, or :meth:`clear_reload_state` on failure.
        """
        self._reload_apply_depth += 1
        try:
            yield
        finally:
            self._reload_apply_depth = max(0, self._reload_apply_depth - 1)

    def arm_reload_residual(self, seconds: float = 0.5) -> None:
        """Arm a short post-apply window covering xStudio's async echo events.

        Called after a successful structural reload (or directly after a
        single ``bm.set_annotation()`` call, which has no need for the
        depth-counted scope above since it is not a variable-duration
        rebuild) — either way, this covers the asynchronous show_atom burst
        xStudio fires once the call has already returned.
        """
        self._reload_residual_until = time.monotonic() + seconds

    def clear_reload_state(self) -> None:
        """Clear both the apply-scope depth and the residual window immediately.

        Called on a failed structural apply, matching the previous
        implementation's behaviour of un-suppressing immediately on exception
        rather than honouring the deadline it never got to arm properly.
        """
        self._reload_apply_depth = 0
        self._reload_residual_until = 0.0

    def reload_in_progress(self) -> bool:
        """True while a remote structural apply is active or in its post-apply echo window."""
        return self._reload_apply_depth > 0 or time.monotonic() < self._reload_residual_until

    # ── annotation event handlers ──────────────────────────────────────

    #: AnnotationsUI event names that mean "annotation visibility changed",
    #: handled by broadcasting display_settings instead of scheduling a
    #: bookmark scan. See annotations_ui_plugin.cpp's toggle_visibility_hotkey_.
    _VISIBILITY_EVENTS = ("HideDrawings", "ShowDrawings")

    #: Draw interactions that arrive at pointer rate.  Handled normally, but not
    #: logged — one line per point buries everything else in the log.
    _HIGH_RATE_EVENTS = ("PaintPoint",)

    def on_bookmarks_event(self, event) -> None:
        """Schedule a flush when a bookmark appears or disappears.

        ``BookmarksActor`` broadcasts ``(event_atom, add_bookmark_atom, UuidActor)``
        and ``(event_atom, remove_bookmark_atom, uuid)`` on its event group
        (bookmarks_actor.cpp:384, :449, :75).  Draw events only cover annotations
        made with the paint tools; a bookmark created or destroyed by any other
        route — the notes panel, a script, another plugin — produces no draw
        event at all, and would otherwise wait up to ``ANNOTATION_SCAN_INTERVAL``
        to be noticed.

        Both directions matter: an add carries a new annotation to broadcast, and
        a remove is what the disappearance diff in
        :meth:`flush_pending_annotations` needs to see promptly to send the clear.

        :param event: Event tuple from the bookmarks actor's event group.
        """
        if not (len(event) >= 2 and isinstance(event[0], event_atom)):
            return
        if not isinstance(event[1], (add_bookmark_atom, remove_bookmark_atom)):
            return
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return
        self._annotation_pending_time = time.monotonic()

    def on_draw_event(self, event_data, user_id, stroke_completed) -> None:
        """[2C] Entry point for every event on AnnotationsCore's draw-events group.

        ``plugin_base.subscribe_to_annotation_draw_events`` decodes the
        ``JsonStore`` payload and flattens the two message shapes that group
        carries into a single callback signature:

        * ``stroke_completed is None`` — a raw draw *interaction*
          (``(event_atom, annotation_atom, JsonStore)``).  ``event_data`` is the
          interaction payload whose ``"event"`` field names the action
          (``PaintClear``, ``HideDrawings``, tool changes, …).  Routed to
          :meth:`on_annotation_event`.
        * otherwise — a serialised *live stroke*
          (``(event_atom, annotation_data_atom, JsonStore, user_id, bool)``).
          ``event_data`` is the annotation JSON.  Routed to
          :meth:`on_core_annotation_event`.

        :param event_data: Decoded JSON payload for the event.
        :param user_id: :class:`Uuid` of the drawing user, ``None`` for
            interactions.
        :param stroke_completed: ``True`` at pen-up, ``False`` mid-stroke,
            ``None`` when this is an interaction rather than a stroke.
        """
        self._core_events_received += 1
        if self._core_events_received == 1:
            _log("[2C] First AnnotationsCore event received")
        if self._core_events_received <= 3:
            shape = (
                f"keys={sorted(event_data)[:6]}"
                if isinstance(event_data, dict)
                else type(event_data).__name__
            )
            _log(
                f"[2C] raw event #{self._core_events_received}: {shape}"
                f" user_id={user_id} stroke_completed={stroke_completed}"
            )
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return

        if stroke_completed is None:
            self.on_annotation_event(event_data)
        else:
            self.on_core_annotation_event(event_data, stroke_completed)

    def on_annotation_event(self, payload) -> None:
        """Handle a raw draw interaction from AnnotationsCore's draw-events group.

        Fired for a variety of actions (``annotation_atom``), including
        ``PaintClear``, ``HideDrawings``/``ShowDrawings``, tool switches, etc. —
        the payload's ``"event"`` field discriminates which. Visibility events
        are broadcast immediately as a display-state change; every other
        recognised or unrecognised event (including ``PaintClear``) schedules
        the existing debounced bookmark scan, unchanged from prior behavior.

        These interactions used to be sought on the AnnotationsUI plugin's own
        events group, where they never arrived: ``AnnotationsUI::send_event()``
        sends them point-to-point to the AnnotationsCore actor, which is what
        re-broadcasts them here (annotations_core_plugin.cpp:95-105).

        :param payload: Decoded interaction payload; ``"event"`` names the action.
        """
        event_name = payload.get("event") if isinstance(payload, dict) else None

        if event_name in self._VISIBILITY_EVENTS:
            _log(f"Draw interaction: {event_name} — broadcasting visibility")
            self.plugin.display.poll_and_broadcast_display()
            return

        # PaintPoint arrives at pointer rate (~130 events for three strokes), so
        # log the gesture boundaries and anything unfamiliar, not every point.
        if event_name not in self._HIGH_RATE_EVENTS:
            _log(f"Draw interaction (event={event_name!r}) — scheduling broadcast scan")
        self._annotation_pending_time = time.monotonic()

    def on_core_annotation_event(self, anno_json, stroke_completed) -> None:
        """[2C] Handle a live stroke broadcast by AnnotationsCore.

        Fired on every PaintStart/PaintPoint/PaintEnd via ``broadcast_live_stroke``.

        ``stroke_completed=True`` at PaintEnd (pen-up): schedule annotation flush.
        ``stroke_completed=False`` at PaintStart/PaintPoint: broadcast partial stroke
        directly from the live JSON data (no bookmark scan needed).

        A local clear no longer arrives here — ``AnnotationsCore::clear_annotation``
        broadcasts its post-clear state on ``live_edit_event_group_``, which is not
        reachable from Python.  The equivalent signal is the ``PaintClear``
        interaction handled by :meth:`on_annotation_event`, which schedules the
        same debounced flush.

        :param anno_json: Serialised annotation for the in-progress stroke, shape
            ``{"Annotation Serialiser Version": N, "Data": {"pen_strokes": [...]}}``.
            Empty when the C++ serialisation produced null, and for shape tools
            mid-drag (AnnotationsCore only serialises those once completed).
        :param stroke_completed: ``True`` at pen-up, ``False`` mid-stroke.
        """
        has_json = isinstance(anno_json, (JsonStore, dict)) and bool(anno_json)

        if stroke_completed:
            _log("[2C] AnnotationsCore: pen-up — scheduling flush")
            self._annotation_pending_time = time.monotonic()
            # Signal poll thread to clear the live-stroke key so the next
            # paint gesture gets a fresh UUID slot in _stroke_uuid_cache.
            self.plugin._cmd_queue.put_nowait(("clear_live_stroke", None))
        elif has_json:
            # Sole mid-stroke path: broadcast the live stroke directly from the
            # event JSON. No bookmark hot-scan — nothing exists to scan mid-stroke.
            self.plugin._cmd_queue.put_nowait(("live_stroke", anno_json))
        # else: empty JSON — no geometry to send. No partial is broadcast; the
        # committed stroke still syncs via the pen-up flush above.

    # ── live stroke broadcast ──────────────────────────────────────────

    @bounded(_ANNOTATION_TIMEOUT_MS)
    def broadcast_live_stroke_from_json(self, anno_json) -> None:
        """Broadcast a partial annotation from a live-stroke JSON payload.

        Called on every PaintPoint by the poll loop when the AnnotationsCore
        draw-events broadcast includes stroke geometry.  The JSON contains
        exactly one pen stroke representing the in-progress drawing.

        Resolves the current clip/frame from the active playhead, assigns a
        stable UUID (so peers can update in-place on subsequent PaintPoints),
        converts the stroke to a SyncEvent, and broadcasts as a partial
        annotation.

        :param anno_json: ``JsonStore``/dict from AnnotationsCore — shape
            ``{"Annotation Serialiser Version": N, "Data": {"pen_strokes": [...]}}``.
        """
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return

        # Resolve current frame and clip from playhead.
        frame = None
        if self.plugin.active_playhead:
            try:
                frame = self.plugin.active_playhead.position
            except Exception:
                return
        if frame is None:
            return

        tl = self.plugin.manager.root_timeline
        if tl is None:
            return

        try:
            clip_guid, clip_local_time = self.plugin.playback.resolve_clip_at_frame(tl, frame)
        except Exception:
            return
        if clip_guid is None:
            fb = self.plugin.playback._last_viewed_clip_guid
            if fb and fb in self.plugin.media._flat_clip_to_media:
                clip_guid = fb
                ph_fps = 25.0
                if self.plugin.active_playhead:
                    try:
                        ph_fps = self.plugin.active_playhead.frame_rate.fps() or ph_fps
                    except Exception:
                        pass
                clip_local_time = otio.opentime.RationalTime(frame, ph_fps)
            else:
                return

        local_frame = int(clip_local_time.value)
        fps = float(clip_local_time.rate) if clip_local_time.rate else 25.0

        # Throttle outbound partials: at most one broadcast per
        # PARTIAL_BROADCAST_INTERVAL per (clip, frame), reusing the same
        # scaffolding apply_partial_annotation_xs uses on the receive side.
        # The committed stroke is flushed separately on pen-up and is never
        # subject to this throttle.
        throttle_key = (clip_guid, local_frame)
        now = time.monotonic()
        last_broadcast = self._last_partial_render_time.get(throttle_key, 0.0)
        if now - last_broadcast < self.PARTIAL_BROADCAST_INTERVAL:
            return
        self._last_partial_render_time[throttle_key] = now

        # Extract the pen_strokes list from the serialised JSON. anno_json is
        # a JsonStore, not a plain dict — JsonStore.get() is a JSON-Pointer
        # lookup, not a dict-style get(key, default), so convert to a native
        # dict via dump()/loads() first (same pattern as Bookmark.annotation_data).
        if isinstance(anno_json, JsonStore):
            anno_json = json.loads(anno_json.dump())
        canvas = anno_json.get("Data", anno_json) if isinstance(anno_json, dict) else {}
        live_strokes = canvas.get("pen_strokes", [])
        if not live_strokes:
            return

        # Assign a stable UUID for the live stroke so the receiver can update
        # in-place on subsequent PaintPoints for the same gesture.
        key = f"{clip_guid}:{local_frame}"
        if key not in self._stroke_uuid_cache:
            self._stroke_uuid_cache[key] = []
        cache = self._stroke_uuid_cache[key]

        if self._live_stroke_current_key != key:
            # New stroke gesture (different key or first PaintPoint after PaintEnd).
            # Append a fresh UUID at the next free slot so _flush reuses it.
            self._live_stroke_current_key = key
            cache.append(str(uuid.uuid4()))

        # The live stroke always occupies the last slot in the cache.
        stroke_idx = len(cache) - 1

        _, aspect_half = self.plugin.media.media_for_sync_guid(clip_guid)

        events_obj = xs_strokes_to_sync_events(
            live_strokes, aspect_half, uuid_list=[cache[stroke_idx]]
        )
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
            f"[2C] Live stroke: broadcasting partial at frame={local_frame}"
            f" clip={clip_guid[:8]} points={len(live_strokes[0].get('points', []))}"
        )
        self.plugin.manager.broadcast_partial_annotation(
            clip_guid=clip_guid,
            frame=float(local_frame),
            fps=fps,
            events=events_dicts,
        )

    # ── flush pending annotations ──────────────────────────────────────

    @bounded(_ANNOTATION_TIMEOUT_MS)
    def flush_pending_annotations(self) -> None:
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

        Also diffs ``_broadcast_annotation_keys`` against the bookmarks that
        currently exist and broadcasts an empty ``REPLACE_ANNOTATION_COMMANDS``
        for any key whose bookmark has disappeared entirely — the usual result
        of clearing a drawing with no note text, which xStudio deletes outright
        rather than leaving empty (``AnnotationsCore::clear_annotation``). That
        leaves no surviving bookmark for the count-decrease path below to
        observe, so it is detected here instead, after the per-bookmark scan
        (so a bookmark recreated under a new uuid this same tick re-records its
        key first, rather than being mistaken for a deletion).
        """
        ANNOTATION_SCAN_INTERVAL = self.plugin.ANNOTATION_SCAN_INTERVAL

        now = time.monotonic()
        if self._annotation_pending_time is not None:
            if now - self._annotation_pending_time < self.DEBOUNCE_SECONDS:
                return
            # Event-triggered flush — clear the pending flag.
            self._annotation_pending_time = None
        else:
            # No event — run the periodic fallback scan.
            if now - self._last_annotation_scan < ANNOTATION_SCAN_INTERVAL:
                return
        self._last_annotation_scan = now

        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return
        try:
            all_bms = self.plugin.connection.api.session.bookmarks.bookmarks
        except Exception:
            _log_exc("flush_pending_annotations: could not list bookmarks")
            return

        # Scan all bookmarks, not just unowned ones.  When the user draws on a
        # frame that already has a remote annotation, xStudio adds to the existing
        # bookmark in-place (same UUID).  That UUID is in _our_bookmark_uuids, so
        # filtering it out would silently drop the new local stroke.  The OTIO
        # delta check inside broadcast_local_bookmark correctly handles
        # deduplication — remote strokes are already in the timeline so delta=0.
        scan_uuids = [bm.uuid for bm in all_bms]
        scan_uuid_strs = {str(u) for u in scan_uuids}

        stale_any = False
        for bm_uuid in scan_uuids:
            try:
                result = self.broadcast_local_bookmark(bm_uuid)
                if result is None:
                    stale_any = True
            except Exception:
                _log_exc("flush_pending_annotations: failed to broadcast bookmark")

        # Detect bookmarks that disappeared since we broadcast for them.  Runs
        # after the per-bookmark scan above, not before it: xStudio itself can
        # replace a bookmark with a new uuid on the same frame when a fresh
        # bookmark hasn't propagated back yet (see the "really awkward" comment
        # in AnnotationsCore::push_live_edit_to_bookmark) -- scanning first lets
        # a still-alive bookmark re-record its key under the new uuid before we
        # ever look for it, so that race isn't mistaken for a deletion. Runs
        # unconditionally, including when scan_uuids is empty -- the
        # all-annotations-cleared case leaves zero live bookmarks and is
        # exactly the case that must not be skipped.
        for key in list(self._broadcast_annotation_keys):
            bm_uuid_str = self._broadcast_annotation_keys[key]
            if bm_uuid_str in scan_uuid_strs:
                continue
            del self._broadcast_annotation_keys[key]
            try:
                clip_guid, frame = key
                ann_clip_guid = self.plugin.manager.annotation_clip_guid_at(clip_guid, frame)
                if ann_clip_guid is None:
                    continue
                _log(
                    f"flush_pending_annotations: bookmark {bm_uuid_str[:8]} disappeared"
                    f" — broadcasting clear at frame={frame} clip={clip_guid[:8]}"
                )
                # An *empty* replacement is a clear, not an edit: annotation
                # clips are merged per clip-and-frame across peers, so emptying
                # one removes whatever other participants drew on that frame
                # too. The plugin states what the call is; the core decides
                # whether this peer's role allows it. The replacements further
                # down carry surviving content and are ordinary edits.
                self.plugin.manager.broadcast_replace_annotation_commands(
                    ann_clip_guid, [], destructive=True
                )
            except Exception:
                _log_exc("flush_pending_annotations: failed to broadcast clear for disappeared bookmark")

        if not scan_uuids:
            return

        # xStudio may not have committed annotation_data yet when the debounce fires.
        # Only retry when a bookmark explicitly returned None (empty annotation_data);
        # if all bookmarks returned False the timeline is already up-to-date.
        if stale_any:
            now = time.monotonic()
            if self._annotation_retry_deadline is None:
                self._annotation_retry_deadline = now + self.STALE_RETRY_WINDOW
            if now < self._annotation_retry_deadline:
                self._annotation_flush_retries += 1
                _log(
                    f"flush_pending_annotations: not ready,"
                    f" retry {self._annotation_flush_retries}"
                    f" ({self._annotation_retry_deadline - now:.1f}s left)"
                )
                self._annotation_pending_time = now
            else:
                _log(
                    f"flush_pending_annotations: gave up after"
                    f" {self.STALE_RETRY_WINDOW:.0f}s"
                    f" ({self._annotation_flush_retries} retries)"
                )
                self._annotation_retry_deadline = None
                self._annotation_flush_retries = 0
        else:
            self._annotation_retry_deadline = None
            self._annotation_flush_retries = 0

    # ── broadcast local bookmark ───────────────────────────────────────

    def _record_broadcast_key(self, bm_key: tuple, bm_uuid_str: str) -> None:
        """Record that annotations were just broadcast for *bm_key*.

        Skipped for bookmarks in ``_our_bookmark_uuids`` — those were created
        locally to mirror a remote peer's annotation, so when the remote peer
        clears it and our mirror disappears too, that disappearance must not
        be echoed back as a clear of our own.

        :param bm_key: ``(clip_guid, frame)`` key, as resolved in
            :meth:`broadcast_local_bookmark`.
        :param bm_uuid_str: The originating bookmark's uuid, as a string.
        """
        with self._our_bookmark_uuids_lock:
            if bm_uuid_str in self._our_bookmark_uuids:
                return
        self._broadcast_annotation_keys[bm_key] = bm_uuid_str

    @bounded(_ANNOTATION_TIMEOUT_MS)
    def broadcast_local_bookmark(self, bm_uuid) -> "bool | None":
        """Read a locally-drawn bookmark's annotation and broadcast it to the session.

        Uses the local OTIO timeline as the authoritative record of what has
        already been broadcast.

        :param bm_uuid: The ``Uuid`` of the bookmark to broadcast.
        :returns: ``True`` if new events were broadcast; ``False`` if everything
            is already in the timeline (no retry needed); ``None`` if
            ``annotation_data`` was empty (xStudio hasn't committed the stroke
            yet — caller should retry after a short delay).
        """
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return False

        try:
            bm = self.plugin.connection.api.session.bookmarks.get_bookmark(bm_uuid)
        except Exception:
            _log_exc("broadcast_local_bookmark: get_bookmark failed")
            return False

        # Read timing to determine which frame this annotation sits on.
        fps = 25.0
        if self.plugin.active_playhead:
            fps = self.plugin.active_playhead.frame_rate.fps() or fps
        try:
            detail = bm.detail
            if detail is None or detail.start is None:
                return False
            frame = int(round(detail.start.total_seconds() * fps))
        except Exception:
            _log_exc("broadcast_local_bookmark: could not read timing")
            return False

        # Read stroke/caption data.
        try:
            ann_data = bm.annotation_data
            if not ann_data:
                # xStudio hasn't committed the stroke to annotation_data yet.
                _log("broadcast_local_bookmark: annotation_data is empty — will retry")
                return None
        except Exception:
            _log_exc("broadcast_local_bookmark: could not read annotation data")
            return False

        # The canvas dict lives under the "Data" key; fall back to the top-level
        # dict in case the format has changed.
        canvas = ann_data.get("Data", ann_data)

        # Resolve clip_guid first — annotation_track_guid_for_clip requires it.
        # Remote-sourced bookmarks have their correct (clip_guid, clip-local-frame)
        # stored in _our_bookmark_clip_frame; bm.detail.start is clip-local time,
        # not global sequence time, so resolve_clip_at_frame would land on the
        # wrong clip when two clips share the same clip-local frame number.
        bm_uuid_str = str(bm_uuid)
        if bm_uuid_str in self._our_bookmark_clip_frame:
            clip_guid, _clip_frame_int = self._our_bookmark_clip_frame[bm_uuid_str]
            clip_local_time = otio.opentime.RationalTime(_clip_frame_int, fps)
        else:
            tl = self.plugin.manager.root_timeline
            if tl is None:
                _log("broadcast_local_bookmark: no timeline registered")
                return False
            clip_guid, clip_local_time = self.plugin.playback.resolve_clip_at_frame(tl, frame)
            if clip_guid is None:
                # Flat-playlist fallback.
                fb = self.plugin.playback._last_viewed_clip_guid
                if fb and fb in self.plugin.media._flat_clip_to_media:
                    clip_guid = fb
                    clip_local_time = otio.opentime.RationalTime(frame, fps)
                    _log(
                        f"broadcast_local_bookmark: flat-playlist fallback"
                        f" → clip {clip_guid[:8]} frame {frame}"
                    )
                else:
                    # Return None, not False, so flush_pending_annotations
                    # retries.  A bookmark can be seen before the clip mapping
                    # that resolves its frame is ready — the add_bookmark event
                    # arrives as soon as the bookmark exists, which is earlier
                    # than the periodic scan ever looked.  Treating "no clip
                    # yet" as terminal drops the annotation permanently; it is
                    # a not-ready condition, exactly like empty annotation_data.
                    _log(f"broadcast_local_bookmark: no clip at frame {frame} — will retry")
                    return None

        annotation_track_guid = self.plugin.manager.annotation_track_guid_for_clip(clip_guid)
        if annotation_track_guid is None:
            _log("broadcast_local_bookmark: no Annotations track")
            return False

        _, aspect_half = self.plugin.media.media_for_sync_guid(clip_guid)
        all_strokes = canvas.get("pen_strokes", [])
        all_captions = canvas.get("captions", [])

        bm_key = (clip_guid, int(clip_local_time.value))
        # Register the local bookmark so refresh_annotation_bookmark can update it
        # when a remote peer adds strokes to the same frame later.
        self._annotation_bookmarks[bm_key] = bm

        # Query the annotation track directly from _object_map to find how many
        # strokes are already broadcast for this (clip, frame).
        sent_strokes, sent_captions = self.plugin.manager.count_annotation_commands(
            clip_guid, int(clip_local_time.value)
        )
        # Guard against echoing remote strokes that arrived via PARTIAL but
        # whose INSERT_CHILD hasn't been processed yet (OTIO count is still 0
        # while the bookmark already holds remote strokes from partial updates).
        # Use the cache length as an additional lower bound: if the bookmark
        # was last set by apply_remote_annotation / refresh_annotation_bookmark,
        # _bookmark_strokes_cache[bm_key] reflects what the remote peer sent;
        # nothing new has been drawn locally if the stroke count hasn't grown.
        cached_remote_count = len(self._bookmark_strokes_cache.get(bm_key, []))
        sent_strokes = max(sent_strokes, cached_remote_count)
        sent_captions = max(sent_captions, len(self._bookmark_captions_cache.get(bm_key, [])))
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

        # Detect local deletion: a stroke/caption count that *dropped* below
        # what's already broadcast (e.g. Ctrl+D "Delete all strokes", or any
        # gesture that removes some but not all annotations on this frame).
        # The plain delta above (`all_strokes[sent_strokes:]`) silently
        # computes an empty slice on a decrease and would otherwise drop the
        # deletion entirely -- rebuild and broadcast the complete surviving
        # state instead, reusing existing uuids so unaffected items are
        # matched in place (not duplicated) on every peer.
        if len(all_strokes) < sent_strokes or len(all_captions) < sent_captions:
            ann_clip_guid = self.plugin.manager.annotation_clip_guid_at(
                clip_guid, int(clip_local_time.value)
            )
            if ann_clip_guid:
                existing_caption_uuids = self.extract_caption_uuids(ann_clip_guid)
                all_events = (
                    xs_strokes_to_sync_events(all_strokes, aspect_half, uuid_list=uuid_cache)
                    + xs_captions_to_sync_events(all_captions, aspect_half, existing_caption_uuids)
                )
                _log(
                    f"Broadcasting annotation replace: {len(all_events)} event(s)"
                    f" (local delete: strokes {sent_strokes}->{len(all_strokes)},"
                    f" captions {sent_captions}->{len(all_captions)})"
                    f" at frame={frame} clip={clip_guid[:8]}"
                )
                self.plugin.manager.broadcast_replace_annotation_commands(
                    ann_clip_guid, all_events
                )
                self._our_annotation_clip_guids.add(ann_clip_guid)
                with self._our_bookmark_uuids_lock:
                    self._our_bookmark_uuids.discard(str(bm_uuid))
                # Keep caches consistent with the new (smaller) authoritative
                # state so future scans diff against the post-delete baseline.
                self._bookmark_strokes_cache[bm_key] = all_strokes
                self._bookmark_captions_cache[bm_key] = all_captions
                if all_captions:
                    self._last_sent_captions[str(bm_uuid)] = self.caption_signature(all_captions)
                self._record_broadcast_key(bm_key, bm_uuid_str)
                return True
            # No annotation clip on record for this bookmark (never broadcast
            # in the first place) -- nothing to replace.
            return False

        # Detect in-place text edits: caption count is unchanged but content
        # differs.  Delta tracking (count-based) misses these, so we replace the
        # full command list on the existing clip instead of appending a delta —
        # regardless of whether that clip originated locally or from a remote
        # peer (see the comment below on why "add a parallel clip instead" is
        # unsafe).
        if sent_captions > 0 and sent_captions == len(all_captions):
            cap_key = str(bm_uuid)
            current_sig = self.caption_signature(all_captions)
            saved_sig = self._last_sent_captions.get(cap_key)
            if saved_sig == self._CAPTION_SIG_UNCONFIRMED:
                # First scan after a remote annotation was applied — xStudio has
                # now committed the data.  Record the actual quantized signature
                # so subsequent scans detect only real user edits.
                self._last_sent_captions[cap_key] = current_sig
                saved_sig = current_sig  # fall through with no mismatch
                _log(
                    f"[DRAG] caption baseline confirmed for bm={str(bm_uuid)[:8]}"
                    f" ({len(all_captions)} captions)"
                )
            if saved_sig != current_sig:
                ann_clip_guid = self.plugin.manager.annotation_clip_guid_at(
                    clip_guid, int(clip_local_time.value)
                )
                if ann_clip_guid:
                    # Always replace the existing clip's commands in place —
                    # regardless of whether it originated locally or from a
                    # remote peer — rather than broadcasting a parallel new
                    # clip ("add"). An "add" here used to be the only way to
                    # avoid clobbering a remote peer's clip, but it leaves the
                    # remote peer's *stale* original clip sitting in the OTIO
                    # tree alongside the new one: count_annotation_commands
                    # then counts both, sent_captions permanently exceeds the
                    # real (single) caption count, and every future edit to
                    # this bookmark silently no-ops (delta = all_captions[N:]
                    # on a list shorter than N). Reusing the existing caption
                    # uuids (extract_caption_uuids) is what keeps this safe:
                    # the peer matches nodes by uuid and updates them in
                    # place, so replacing the clip's contents doesn't drop or
                    # duplicate anything it already has.
                    existing_uuids = self.extract_caption_uuids(ann_clip_guid)
                    all_events = (
                        xs_strokes_to_sync_events(
                            all_strokes, aspect_half, uuid_list=uuid_cache
                        )
                        + xs_captions_to_sync_events(
                            all_captions, aspect_half, existing_uuids
                        )
                    )
                    _log(
                        f"Broadcasting annotation replace: {len(all_events)} event(s)"
                        f" (caption edit) at frame={frame} clip={clip_guid[:8]}"
                    )
                    self.plugin.manager.broadcast_replace_annotation_commands(
                        ann_clip_guid, all_events
                    )
                    self._our_annotation_clip_guids.add(ann_clip_guid)
                    with self._our_bookmark_uuids_lock:
                        self._our_bookmark_uuids.discard(str(bm_uuid))
                    self._last_sent_captions[cap_key] = current_sig
                    # Keep cache consistent so future ADD scans don't re-detect captions.
                    self._bookmark_captions_cache[bm_key] = all_captions
                    self._record_broadcast_key(bm_key, bm_uuid_str)
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
        new_guid = self.plugin.manager.broadcast_add_annotation(
            annotation_track_guid=annotation_track_guid,
            clip_guid=clip_guid,
            clip_local_time=clip_local_time,
            events=events,
        )
        if new_guid:
            self._our_annotation_clip_guids.add(new_guid)
        # Record caption signature so the next scan doesn't re-broadcast them.
        if new_captions:
            cap_key = str(bm_uuid)
            self._last_sent_captions[cap_key] = self.caption_signature(all_captions)
        self._record_broadcast_key(bm_key, bm_uuid_str)
        return True

    # ── caption helpers ────────────────────────────────────────────────

    @staticmethod
    def caption_signature(xs_captions: list) -> str:
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

    def extract_caption_uuids(self, ann_clip_guid: str) -> "list[str]":
        """Return the ordered UUIDs of all TextAnnotation commands in an annotation clip.

        Used when building replacement events so that the same UUIDs are reused
        and remote peers (e.g. RV) can find and update existing paint nodes in place.

        :param ann_clip_guid: Sync GUID of the annotation clip in ``manager._object_map``.
        :returns: List of UUID strings, one per TextAnnotation, in command order.
        :rtype: list
        """
        clip = self.plugin.manager._object_map.get(ann_clip_guid) if self.plugin.manager else None
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

    # ── snapshot annotation loading ────────────────────────────────────

    @bounded(_ANNOTATION_TIMEOUT_MS)
    def load_snapshot_annotations(
        self, otio_tl: "otio.schema.Timeline", playlist
    ) -> None:
        """
        Create xStudio bookmarks for annotation clips already present in a snapshot.

        ``apply_remote_annotation`` only fires for *new* ``insert_child`` events
        received after joining.  Annotation clips that arrived inside the initial
        state snapshot must be converted to bookmarks here, immediately after the
        playlist is created from the OTIO timeline.

        :param otio_tl: The OTIO timeline that was just loaded into xStudio.
        :param playlist: The xStudio playlist created from *otio_tl*.
        """
        if not self.plugin.manager:
            return

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

                otio_clip = self.plugin.manager._object_map.get(clip_guid)
                if otio_clip is None:
                    _log(f"  Snapshot ann: clip_guid {clip_guid[:8]} not in object_map")
                    continue
                media = self.plugin.media.media_for_sync_guid(clip_guid)[0]
                if media is None:
                    _log(
                        f"  Snapshot ann: no playlist media found for"
                        f" clip_guid {clip_guid[:8]}"
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
                bm = self.plugin.connection.api.session.bookmarks.add_bookmark(
                    target=media
                )
                detail = BookmarkDetail()
                detail.start = _frame_start_timedelta(frame, fps)
                detail.duration = datetime.timedelta(seconds=0)
                self.plugin.connection.request_receive(
                    bm.remote, bookmark_detail_atom(), detail
                )
                bm.set_annotation(strokes=pen_strokes, captions=captions)
                self._annotation_bookmarks[(clip_guid, frame)] = bm
                with self._our_bookmark_uuids_lock:
                    self._our_bookmark_uuids.add(str(bm.uuid))
                self._our_bookmark_clip_frame[str(bm.uuid)] = (clip_guid, frame)
                # Mark as unconfirmed keyed by bookmark UUID (same key the scan
                # uses via cap_key = str(bm_uuid)) so the first scan confirms the
                # post-quantization signature without broadcasting.
                if captions:
                    self._last_sent_captions[str(bm.uuid)] = (
                        self._CAPTION_SIG_UNCONFIRMED
                    )
                count += 1
            except Exception:
                _log_exc(
                    f"  Snapshot ann: failed bookmark for"
                    f" {grp['clip_name']!r} f{frame}"
                )

        if count:
            _log(f"  Loaded {count} snapshot annotation(s) as bookmarks")

    # ── refresh annotation bookmark ────────────────────────────────────

    @bounded(_ANNOTATION_TIMEOUT_MS)
    def refresh_annotation_bookmark(
        self, merged_clip: "otio.schema.Clip"
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
            _log(f"refresh_annotation_bookmark: no tracked bookmark for {bm_key}")
            return

        media, aspect_half = self.plugin.media.media_for_sync_guid(clip_guid)
        if media is None:
            return

        all_commands = merged_clip.metadata.get("annotation_commands", [])
        pen_strokes = sync_events_to_xs_strokes(all_commands, aspect_half)
        captions = sync_events_to_xs_captions(all_commands, aspect_half)
        if not pen_strokes and not captions:
            if not all_commands:
                # Authoritatively empty (see otio-annotation-sync's
                # "Authoritative Empty Replace Semantics") -- unlike a
                # non-empty command list that happens to decode to nothing,
                # an empty incoming command list means the sending peer's
                # clip now genuinely has zero annotations. Apply it
                # unconditionally rather than early-returning below, which
                # would otherwise leave stale strokes/captions on the bookmark.
                try:
                    self._bookmark_strokes_cache[bm_key] = []
                    self._bookmark_captions_cache[bm_key] = []
                    bm.set_annotation(strokes=[], captions=[])
                    _log(f"Hard-cleared annotation bookmark at frame {frame}")
                except Exception:
                    _log_exc("refresh_annotation_bookmark: hard-clear failed")
            return

        try:
            self._bookmark_strokes_cache[bm_key] = pen_strokes
            self._bookmark_captions_cache[bm_key] = captions
            bm.set_annotation(strokes=pen_strokes, captions=captions)
            _log(
                f"Refreshed annotation bookmark: {len(pen_strokes)} stroke(s),"
                f" {len(captions)} caption(s) at frame {frame}"
            )
            # Mark as unconfirmed so the first scan after this refresh confirms
            # the post-quantization signature without broadcasting.  The refresh
            # result is remote data, not a local edit.
            if captions:
                self._last_sent_captions[str(bm.uuid)] = (
                    self._CAPTION_SIG_UNCONFIRMED
                )
                # Trigger a fast confirmation scan (~DEBOUNCE_SECONDS = 250 ms)
                # instead of waiting for the full 1-second periodic scan.  This
                # shrinks the window where a concurrent user drag gets silently
                # captured as the post-refresh baseline.
                if self._annotation_pending_time is None:
                    self._annotation_pending_time = time.monotonic()
        except Exception:
            _log_exc("refresh_annotation_bookmark: failed")

    # ── apply remote annotation ────────────────────────────────────────

    def apply_partial_annotation_xs(self, payload: dict) -> None:
        """Render a mid-stroke partial annotation from a remote peer (visual only).

        Constructs a temporary OTIO Clip from the payload and delegates to
        ``apply_remote_annotation``, which handles both create and
        update-in-place for the xStudio bookmark.  The clip is never inserted
        into the timeline — it is used only to carry frame/fps/clip_guid.

        Because ``apply_remote_annotation`` adds the bookmark UUID to
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
                _log(f"apply_partial_annotation_xs: failed to deserialise event: {e}")

        if not commands:
            return

        temp_clip = otio.schema.Clip()
        temp_clip.source_range = otio.opentime.TimeRange(
            otio.opentime.RationalTime(frame, fps),
            otio.opentime.RationalTime(1.0, fps),
        )
        temp_clip.metadata["clip_guid"] = clip_guid

        self.apply_remote_annotation(temp_clip, commands)

    @bounded(_ANNOTATION_TIMEOUT_MS)
    def apply_remote_annotation(
        self, ann_clip: "otio.schema.Clip", commands: list
    ) -> None:
        """
        Convert a received annotation clip into an xStudio bookmark with strokes.

        Uses the xStudio bookmark API (``Bookmarks.add_bookmark`` +
        ``Bookmark.set_annotation``) rather than raw actor messaging.

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
            _log("apply_remote_annotation: no clip_guid in metadata — skipping")
            return

        media, aspect_half = self.plugin.media.media_for_sync_guid(clip_guid)
        if media is None:
            # This annotation is now lost: nothing re-delivers it. The retry
            # machinery in this module (flush_pending_annotations) is for
            # *outgoing* broadcasts only, and both callers of this method drop
            # on return. Logged as DROPPED rather than a bare "no media" so the
            # consequence is legible in the log, not just the cause.
            _log(
                f"apply_remote_annotation: DROPPED annotation for clip "
                f"{clip_guid[:8]} frame={frame} — no xStudio media"
            )
            return

        pen_strokes = sync_events_to_xs_strokes(commands, aspect_half)
        captions = sync_events_to_xs_captions(commands, aspect_half)
        if not pen_strokes and not captions:
            _log("apply_remote_annotation: no strokes or captions decoded — skipping")
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

                # Throttle set_annotation to at most ~10fps during live partial
                # updates.  Each PARTIAL from the sender carries ALL strokes
                # cumulatively, so the merged list grows with every call; the
                # blocking C++ actor roundtrip gets progressively slower and
                # eventually starves the poll thread.  We always update the
                # cache so the next rendered frame is current, and the final
                # INSERT_CHILD triggers refresh_annotation_bookmark which
                # renders the complete state unconditionally.
                _PARTIAL_RENDER_INTERVAL = 0.1  # seconds (~10fps)
                now = time.monotonic()
                last_render = self._last_partial_render_time.get(bm_key, 0.0)
                if now - last_render >= _PARTIAL_RENDER_INTERVAL:
                    existing_bm.set_annotation(
                        strokes=merged_strokes, captions=merged_captions
                    )
                    self._last_partial_render_time[bm_key] = now
                    _log(
                        f"Updated annotation bookmark (non-destructive):"
                        f" {len(merged_strokes)} stroke(s), {len(merged_captions)} caption(s)"
                        f" at frame {frame}"
                    )
                target_bm = existing_bm
            else:
                bm = self.plugin.connection.api.session.bookmarks.add_bookmark(
                    target=media
                )
                # Set start and duration in a single BookmarkDetail message.
                detail = BookmarkDetail()
                detail.start = _frame_start_timedelta(frame, fps)
                detail.duration = datetime.timedelta(seconds=0)
                detail.author = "ORI Sync"
                detail.note = "Annotation"
                self.plugin.connection.request_receive(
                    bm.remote, bookmark_detail_atom(), detail
                )
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
                    self._our_bookmark_uuids.add(str(bm.uuid))
                # Suppress the show_atom burst that xStudio fires when displaying
                # the new bookmark — without this, the flush scan re-runs and
                # echoes the remote strokes back as if they were drawn locally.
                self.arm_reload_residual(0.5)
                _log(
                    f"Applied remote annotation: {len(pen_strokes)} stroke(s),"
                    f" {len(captions)} caption(s) at frame {frame}"
                )
                target_bm = bm
            self._our_bookmark_clip_frame[str(target_bm.uuid)] = (clip_guid, frame)
            # Mark as unconfirmed so the first periodic scan confirms the
            # post-quantization signature without broadcasting.  We cannot read
            # back the committed annotation_data here because xStudio's actor
            # may not have processed set_annotation() yet.
            if captions:
                self._last_sent_captions[str(target_bm.uuid)] = (
                    self._CAPTION_SIG_UNCONFIRMED
                )
        except Exception:
            _log_exc("apply_remote_annotation: failed to set annotation")
