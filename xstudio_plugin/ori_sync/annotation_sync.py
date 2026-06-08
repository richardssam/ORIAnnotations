#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""AnnotationSyncController — owns annotation send/receive state and methods."""

import datetime
import json
import threading
import time
import uuid
import opentimelineio as otio
from xstudio.core import (
    BookmarkDetail, bookmark_detail_atom, event_atom,
    annotation_atom, annotation_data_atom, JsonStore,
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


class AnnotationSyncController:
    """Owns annotation send/receive state and methods.

    :param plugin: Back-reference to the parent ORISyncPlugin instance.
    """

    #: How long to wait after the last annotation_atom before scanning bookmarks.
    DEBOUNCE_SECONDS = 0.25
    #: Stop hot-scanning a frame after this many seconds of no new strokes.
    HOT_SCAN_TIMEOUT = 0.6
    #: Sentinel stored in ``_last_sent_captions`` immediately after a remote
    #: annotation is applied, before xStudio has committed the annotation_data.
    _CAPTION_SIG_UNCONFIRMED = "\x00unconfirmed\x00"

    def __init__(self, plugin):
        self.plugin = plugin

        # ── owned state ───────────────────────────────────────────────
        self._annotation_bookmarks: dict[tuple, object] = {}
        self._bookmark_strokes_cache: dict[tuple, list] = {}
        self._bookmark_captions_cache: dict[tuple, list] = {}
        self._our_bookmark_uuids: set = set()
        self._our_bookmark_uuids_lock = threading.Lock()
        self._our_annotation_clip_guids: set = set()
        self._our_bookmark_clip_frame: dict[str, tuple[str, int]] = {}
        self._last_sent_captions: dict[str, str] = {}
        self._annotation_flush_retries: int = 0
        self._core_events_received: int = 0
        self._stroke_uuid_cache: dict[str, list] = {}
        self._live_stroke_current_key: str | None = None
        self._hot_scan_stroke_counts: dict[str, int] = {}
        self._hot_scan_point_counts: dict[str, int] = {}
        self._last_annotation_scan: float = 0.0
        # Throttle partial set_annotation calls: tracks the last time we actually
        # called bm.set_annotation() for each (clip_guid, frame) key during a
        # partial-update session.  Prevents the poll thread from blocking on
        # increasingly large cumulative stroke lists at every PARTIAL arrival.
        self._last_partial_render_time: dict[tuple, float] = {}

    def reset_last_scan(self, t: float) -> None:
        """Set the last annotation scan timestamp (called from _on_synced)."""
        self._last_annotation_scan = t
        self._last_partial_render_time.clear()

    # ── annotation event handlers ──────────────────────────────────────

    def on_annotation_event(self, data) -> None:
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
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return
        _log("Annotation event from AnnotationsUI — scheduling broadcast scan")
        self.plugin._annotation_pending_time = time.monotonic()

    def on_core_annotation_event(self, data) -> None:
        """[2C] Called when AnnotationsCore broadcasts a live stroke event.

        Fired on every PaintStart/PaintPoint/PaintEnd via ``broadcast_live_stroke``.

        New shape (post C++ serialisation fix):
        ``(event_atom, annotation_data_atom, JsonStore, user_id, stroke_completed)``

        Legacy shape (pre-fix builds, no stroke data):
        ``(event_atom, annotation_data_atom, user_id, stroke_completed)``

        ``stroke_completed=True`` at PaintEnd (pen-up): schedule annotation flush.
        ``stroke_completed=False`` at PaintStart/PaintPoint: broadcast partial stroke
        directly from the live JSON data (no bookmark scan needed).

        :param data: Event tuple from AnnotationsCore plugin_events_.
        """
        # Raw invocation counter — logged before any guard so we can tell if
        # the callback fires but the guard rejects it.
        self._core_events_received += 1
        if self._core_events_received <= 3:
            types = [type(d).__name__ for d in data]
            _log(
                f"[2C] raw event #{self._core_events_received}:"
                f" len={len(data)} types={types}"
            )
        if not (
            len(data) >= 4
            and isinstance(data[0], event_atom)
            and isinstance(data[1], annotation_data_atom)
        ):
            _log(f"[2C] guard rejected event #{self._core_events_received}")
            return
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return

        # Discriminate by tuple length, NOT by data[2] type.
        # 5-element (new shape): data[2]=JsonStore/None, data[3]=user_id, data[4]=bool
        # 4-element (legacy):    data[2]=user_id, data[3]=bool
        is_new_shape = len(data) >= 5
        if is_new_shape:
            stroke_completed = bool(data[4])
            # data[2] may be JsonStore, dict, or None (if serialise threw and
            # anno_json was default-constructed to null)
            raw_json = data[2]
            has_json = isinstance(raw_json, (JsonStore, dict)) and bool(raw_json)
        else:
            stroke_completed = bool(data[3])
            has_json = False
            raw_json = None

        if stroke_completed:
            _log("[2C] AnnotationsCore: pen-up — scheduling flush")
            self.plugin._annotation_pending_time = time.monotonic()
            self.plugin._hot_scan_active = False
            # Signal poll thread to clear the live-stroke key so the next
            # paint gesture gets a fresh UUID slot in _stroke_uuid_cache.
            self.plugin._cmd_queue.put_nowait(("clear_live_stroke", None))
        elif has_json:
            # New path: broadcast the live stroke directly from the event JSON.
            self.plugin._cmd_queue.put_nowait(("live_stroke", raw_json))
        else:
            # Legacy path (old build without JSON): fall back to hot-scan.
            if not self.plugin._hot_scan_active:
                if self.plugin.active_playhead:
                    try:
                        self.plugin._hot_scan_frame = self.plugin.active_playhead.position
                        self.plugin._hot_scan_active = True
                        self.plugin._hot_scan_last_change = time.monotonic()
                        _log(
                            f"[2C] mid-stroke (legacy) — hot scan at frame"
                            f" {self.plugin._hot_scan_frame}"
                        )
                    except Exception:
                        pass
            else:
                self.plugin._hot_scan_last_change = time.monotonic()
            self.plugin._cmd_queue.put_nowait(("hot_scan", None))

    # ── hot scan ───────────────────────────────────────────────────────

    @bounded(_ANNOTATION_TIMEOUT_MS)
    def hot_scan_active_annotation(self) -> None:
        """Poll the active drawing frame every tick to stream partial strokes.

        Activated when ``show_atom`` fires (user starts drawing on a new frame).
        Runs on every poll tick (33 ms) so that partial strokes are broadcast to
        peers before pen-up, giving an interactive feel.

        Uses ``_stroke_uuid_cache`` to assign stable UUIDs to strokes at each
        index, so that a receiver that already rendered the partial via
        ``apply_partial_annotation_xs`` can update in-place rather than create
        a duplicate when the final ``INSERT_CHILD`` arrives.
        """
        if not self.plugin._hot_scan_active:
            return
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            self.plugin._hot_scan_active = False
            return
        now = time.monotonic()
        if now - self.plugin._hot_scan_last_change > self.HOT_SCAN_TIMEOUT:
            _log("Hot scan timed out — deactivating")
            self.plugin._hot_scan_active = False
            return

        frame = self.plugin._hot_scan_frame
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
            # Flat-playlist fallback: clips have no source_range so
            # resolve_clip_at_frame always returns None.  Use the last
            # broadcast/received selection clip GUID; for flat playlists the
            # user views one clip at a time so this is always the right clip.
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

        # Find a local (non-remote) bookmark at this frame.
        try:
            all_bms = self.plugin.connection.api.session.bookmarks.bookmarks
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

        current_stroke_points = (
            len(all_strokes[-1].get("points", [])) if all_strokes else 0
        )

        if len(all_strokes) == last_sent_strokes and current_stroke_points <= last_sent_points:
            return  # no new strokes or points since last hot broadcast

        self.plugin._hot_scan_last_change = now
        self._hot_scan_stroke_counts[key] = len(all_strokes)
        self._hot_scan_point_counts[key] = current_stroke_points

        # Ensure UUID cache covers all strokes (including pre-existing ones).
        if key not in self._stroke_uuid_cache:
            self._stroke_uuid_cache[key] = []
        cache = self._stroke_uuid_cache[key]
        while len(cache) < len(all_strokes):
            cache.append(str(uuid.uuid4()))

        _, aspect_half = self.plugin.media.media_for_sync_guid(clip_guid)

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
        self.plugin.manager.broadcast_partial_annotation(
            clip_guid=clip_guid,
            frame=float(local_frame),
            fps=fps,
            events=events_dicts,
        )

    # ── live stroke broadcast ──────────────────────────────────────────

    @bounded(_ANNOTATION_TIMEOUT_MS)
    def broadcast_live_stroke_from_json(self, anno_json) -> None:
        """Broadcast a partial annotation from a live-stroke JSON payload.

        Called on every PaintPoint by the poll loop when the AnnotationsCore
        ``plugin_events_`` broadcast includes a ``JsonStore``
        (post C++ serialisation fix).  The JSON contains exactly one pen stroke
        representing the in-progress drawing.

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

        # Extract the pen_strokes list from the serialised JSON.
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
        """
        ANNOTATION_SCAN_INTERVAL = self.plugin.ANNOTATION_SCAN_INTERVAL

        now = time.monotonic()
        if self.plugin._annotation_pending_time is not None:
            if now - self.plugin._annotation_pending_time < self.DEBOUNCE_SECONDS:
                return
            # Event-triggered flush — clear the pending flag.
            self.plugin._annotation_pending_time = None
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
        if not scan_uuids:
            return

        stale_any = False
        for bm_uuid in scan_uuids:
            try:
                result = self.broadcast_local_bookmark(bm_uuid)
                if result is None:
                    stale_any = True
            except Exception:
                _log_exc("flush_pending_annotations: failed to broadcast bookmark")

        # xStudio may not have committed annotation_data yet when the debounce fires.
        # Only retry when a bookmark explicitly returned None (empty annotation_data);
        # if all bookmarks returned False the timeline is already up-to-date.
        if stale_any and self._annotation_flush_retries < 5:
            self._annotation_flush_retries += 1
            _log(
                f"flush_pending_annotations: stale annotation_data,"
                f" retry {self._annotation_flush_retries}/5"
            )
            self.plugin._annotation_pending_time = time.monotonic()
        else:
            self._annotation_flush_retries = 0

    # ── broadcast local bookmark ───────────────────────────────────────

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
                    _log(f"broadcast_local_bookmark: no clip at frame {frame}")
                    return False

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

        # Detect in-place text edits: caption count is unchanged but content
        # differs.  Delta tracking (count-based) misses these, so we replace the
        # full command list on the existing clip instead of appending a delta.
        #
        # Guard: only REPLACE when ann_clip_guid is in _our_annotation_clip_guids
        # (a clip this peer created or previously broadcast to).
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
            if saved_sig != current_sig:
                with self._our_bookmark_uuids_lock:
                    is_remote_bookmark = str(bm_uuid) in self._our_bookmark_uuids

                ann_clip_guid = self.plugin.manager.annotation_clip_guid_at(
                    clip_guid, int(clip_local_time.value)
                )
                if ann_clip_guid:
                    if is_remote_bookmark or ann_clip_guid not in self._our_annotation_clip_guids:
                        # Broadcast as a new independent annotation to avoid
                        # overwriting a remote peer's annotation clip.
                        all_events = (
                            xs_strokes_to_sync_events(
                                all_strokes, aspect_half, uuid_list=uuid_cache
                            )
                            + xs_captions_to_sync_events(all_captions, aspect_half)
                        )
                        reason = (
                            "local edit on remote bookmark"
                            if is_remote_bookmark
                            else "new local annotation at remote-owned frame"
                        )
                        _log(
                            f"Broadcasting annotation add: {len(all_events)} event(s)"
                            f" ({reason}) at frame={frame} clip={clip_guid[:8]}"
                        )
                        new_guid = self.plugin.manager.broadcast_add_annotation(
                            annotation_track_guid=annotation_track_guid,
                            clip_guid=clip_guid,
                            clip_local_time=clip_local_time,
                            events=all_events,
                        )
                        if new_guid:
                            self._our_annotation_clip_guids.add(new_guid)
                        with self._our_bookmark_uuids_lock:
                            self._our_bookmark_uuids.discard(str(bm_uuid))
                    else:
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
                detail.start = datetime.timedelta(seconds=frame / fps)
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
            _log(
                f"apply_remote_annotation: no xStudio media for clip {clip_guid[:8]}"
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
                detail.start = datetime.timedelta(seconds=frame / fps)
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
                self.plugin._reload_suppress_until = time.monotonic() + 0.5
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
