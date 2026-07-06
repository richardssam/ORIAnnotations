#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""PlaybackSyncController — owns playback/selection state and methods."""

import os
import time
import opentimelineio as otio
from xstudio.api.session.playhead import Playhead
from xstudio.api.session.playlist.timeline import Timeline
from xstudio.api.session.playlist import Playlist
from xstudio.api.session.container import Container
from xstudio.api.session.playlist.subset import Subset
from xstudio.api.session.playlist.contact_sheet import ContactSheet
from xstudio.core import (
    event_atom, show_atom, viewport_playhead_atom,
    viewport_active_media_container_atom, item_selection_atom,
    selection_actor_atom, selection_changed_atom, source_atom,
    position_atom, play_forward_atom, play_atom,
)
from otio_sync_core.manager import STATE_SYNCED
from .utils import _log, _log_exc, bounded, bounded_timeout

# Bounded timeout (ms) for quick poll-thread playhead/viewport reads.  Generous
# for a healthy actor (which replies in a few ms) but far below the 100 s default
# so a stale/destroyed actor fails fast and the poll thread keeps running.
_PLAYHEAD_TIMEOUT_MS = 2000

# Native "Loop Mode" playhead attribute string <-> wire playback_mode string.
_LOOP_MODE_TO_WIRE = {"Loop": "loop", "Play Once": "play-once", "Ping Pong": "ping-pong"}
_WIRE_TO_LOOP_MODE = {v: k for k, v in _LOOP_MODE_TO_WIRE.items()}

# Minimum interval (s) between position-only scrub broadcasts.  xStudio fires a
# "Logical Frame" attribute change per rendered frame while dragging the
# playhead (~60 Hz); peers don't need updates that often, and the flood backs
# up the single poll thread on the receiving end.  ~20 Hz stays visually smooth.
_SCRUB_BROADCAST_INTERVAL = 0.05

# Programmatically highlighting the selected clip inside a sequence track (via a
# raw item_selection_atom send) can crash xStudio: the send into a recently-rebuilt
# timeline races with that timeline's clip actors being torn down, and the
# resulting broadcast_down_atom is delivered to a Python event callback that
# segfaults (signal 11 in execute_event_callback) — a C++-level crash no Python
# try/except can catch.  The highlight is now gated behind a timeline-stability
# guard (_timeline_recently_rebuilt) that skips the send for a settle window
# after any structural rebuild, which closes most of the race (see design D2 of
# the fix-xstudio-selection-and-playhead-sync change).  This flag remains as an
# instant kill-switch: flip it to False to disable all item_selection_atom sends
# outright if crashes recur during live testing.
_ENABLE_TIMELINE_ITEM_HIGHLIGHT = True


class PlaybackSyncController:
    """Owns playback and selection sync state and methods.

    :param plugin: Back-reference to the parent ORISyncPlugin instance.
    """

    def __init__(self, plugin):
        self.plugin = plugin

        # ── owned state ───────────────────────────────────────────────
        self._pending_seek_frame: int | None = None
        self._pending_seek_deadline: float = 0.0
        self._last_known_playback_mode: str | None = None
        self._loop_mode_apply_suppress_until: float = 0.0
        self._current_selection_sub_id: int | None = None
        self._current_selection_container_uuid: str | None = None
        self._last_logged_container_uuid: str | None = None
        self._last_logged_clip_name: str | None = None
        self._last_viewed_clip_guid: str | None = None
        self._last_pinned_source_mode: bool | None = None
        self._last_sel_names = None
        self._last_src_names = None
        self._last_playhead_check: float = 0.0
        self._cached_viewed_tl_guid: str | None = None
        self._cached_viewed_tl_at: float = 0.0
        # Current local view state (unify-view-state-sync): the mode and active
        # clip we last observed locally, so the position-change broadcast can
        # carry them in the single PLAYBACK_SETTINGS view-state message.
        self._cur_view_mode: str = "sequence"
        self._cur_clip_guid: "str | None" = None
        # Last remote view (mode, clip) we applied, so apply only re-switches the
        # on-screen source / mode when it actually changes (the frame updates
        # every message but the view rarely does).
        self._last_applied_view_mode: "str | None" = None
        self._last_applied_clip_guid: "str | None" = None
        self._last_applied_tl_guid: "str | None" = None
        # Timestamp until which remote view-switch messages are suppressed after a
        # local user action (view selection / double-click).  Prevents in-flight
        # stale remote messages from hijacking the local selection the user just made.
        self._local_view_action_until: float = 0.0
        # Extended clip-specific echo guard: when apply_selection sets a remote
        # clip, the resulting show_atom can be delayed by POLL-SLOW (2+ s) and
        # outlast the short _selection_broadcast_suppress_until window.  Track the
        # exact guid and a longer window so the delayed show_atom is still caught.
        self._applied_clip_echo_guid: "str | None" = None
        self._applied_clip_echo_until: float = 0.0
        # Scrub-broadcast throttle: xStudio fires a "Logical Frame" attribute
        # change per rendered frame while dragging the playhead (~60 Hz), far
        # faster than peers need to stay in sync.  Position-only updates are
        # rate-limited to _SCRUB_BROADCAST_INTERVAL; the most recent state is
        # held in _pending_scrub_state and flushed by the poll loop once its
        # deadline passes, so the final scrub position is never dropped.
        self._last_scrub_broadcast_at: float = 0.0
        self._pending_scrub_state: "dict | None" = None
        self._pending_scrub_due: float = 0.0

    def reset(self) -> None:
        """Clear all owned state (called from plugin disconnect)."""
        if self._current_selection_sub_id is not None:
            try:
                self.plugin.unsubscribe_from_event_group(self._current_selection_sub_id)
            except Exception:
                pass
            self._current_selection_sub_id = None
        self._current_selection_container_uuid = None
        self._last_logged_container_uuid = None
        self._last_logged_clip_name = None
        self._last_viewed_clip_guid = None
        self._pending_seek_frame = None
        self._pending_seek_deadline = 0.0
        self._last_known_playback_mode = None
        self._loop_mode_apply_suppress_until = 0.0
        self._last_pinned_source_mode = None
        self._last_sel_names = None
        self._last_src_names = None
        self._last_playhead_check = 0.0
        self._cached_viewed_tl_guid = None
        self._cached_viewed_tl_at = 0.0
        self._cur_view_mode = "sequence"
        self._cur_clip_guid = None
        self._last_applied_view_mode = None
        self._last_applied_clip_guid = None
        self._last_applied_tl_guid = None
        self._local_view_action_until = 0.0
        self._applied_clip_echo_guid = None
        self._applied_clip_echo_until = 0.0
        self._last_scrub_broadcast_at = 0.0
        self._pending_scrub_state = None
        self._pending_scrub_due = 0.0

    def cached_viewed_timeline_guid(self, ttl: float = 0.5) -> str | None:
        """Return the viewed timeline guid, cached for *ttl* seconds.

        ``get_local_viewed_timeline_guid`` does a blocking viewport read; calling
        it on every playback broadcast throttles the scrub broadcast rate.  The
        viewed timeline rarely changes mid-scrub, so a short TTL cache keeps
        broadcasts cheap while still tracking container switches quickly.
        """
        now = time.monotonic()
        if now - self._cached_viewed_tl_at < ttl and self._cached_viewed_tl_guid is not None:
            return self._cached_viewed_tl_guid
        guid = self.get_local_viewed_timeline_guid()
        if guid is not None:
            self._cached_viewed_tl_guid = guid
            self._cached_viewed_tl_at = now
        return guid

    # ── global playhead event ──────────────────────────────────────────

    @bounded(_PLAYHEAD_TIMEOUT_MS)
    def on_global_playhead_event(self, event) -> None:
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
                self.check_and_update_active_playhead()
                media_ua = event[2]
                media_uuid_str = str(media_ua.uuid)
                is_playlist = getattr(self.plugin, "_viewport_container_is_playlist", False)
                is_timeline = getattr(self.plugin, "_viewport_container_is_timeline", False)
                _container_label = "playlist" if is_playlist else ("timeline" if is_timeline else "unknown")
                _media_name_hint = None
                # Resolve via the timeline actually on screen right now, not
                # manager.active_timeline_guid — that field is only updated by
                # this same handler (further below) and a few apply paths, so it
                # still holds the *previous* selection's value while this event
                # is being processed.  Same xStudio media UUID can be registered
                # under multiple owning timelines (e.g. once as a standalone
                # bin/individual-clip entry, again as a sequence clip after the
                # clip is folded into a sequence); sync_guid_for_xs_uuid's
                # unscoped fallback scan would otherwise return whichever one
                # was registered first — the stale bin guid — instead of the
                # sequence's own clip guid that peers actually share.
                clip_guid = self.plugin.media.sync_guid_for_xs_uuid(
                    media_uuid_str,
                    self.cached_viewed_timeline_guid(ttl=0.1),
                )
                if clip_guid:
                    media_obj = (
                        self.plugin.media._sync_guid_to_xs_media.get(clip_guid)
                        or self.plugin.media._flat_clip_to_media.get(clip_guid)
                    )
                    if media_obj:
                        _media_name_hint = media_obj.name

                if not clip_guid or not _media_name_hint:
                    # Legacy fallback scan — snapshot to avoid RuntimeError from concurrent mutation
                    for _pl, _ in list(self.plugin._sync_playlists.values()):
                        try:
                            for _m in _pl.media:
                                if str(_m.uuid) == media_uuid_str:
                                    _media_name_hint = _m.name
                                    clip_guid = self.plugin.media.clip_guid_for_media_name(_media_name_hint)
                                    break
                        except Exception:
                            pass
                        if _media_name_hint:
                            break

                _log(f"[SEL] show_atom media-change: name={_media_name_hint!r} uuid={media_uuid_str[:8]} container={_container_label} raw={_shape}")
                # Determine view_mode by checking whether this media UUID belongs
                # to a tracked sequence (Timeline) playlist.
                _seq_tl_guid: str | None = None
                if clip_guid and self.plugin.manager:
                    for _tg, _tl in list(self.plugin.manager.timelines.items()):
                        if _tl.metadata.get("xs_flat_playlist"):
                            continue
                        # Skip dynamic single-clip timelines (created lazily by
                        # get_or_create_clip_timeline for clip-level annotation
                        # bookkeeping).  They hold exactly one clip — a copy of
                        # whatever sequence/bin clip was last selected — and are
                        # not real sequences.  Without this check, once any clip
                        # had been individually selected this session, its
                        # leftover clip-timeline entry made every later
                        # selection of that same clip look like "media inside a
                        # sequence" (_is_seq_media=True), wrongly triggering the
                        # "playing through sequence" suppression and silently
                        # dropping the selection broadcast even with no sequence
                        # ever created.
                        if _tl.metadata.get("clip_timeline_for"):
                            continue
                        for _track in _tl.tracks:
                            if _track.kind == otio.schema.TrackKind.Video:
                                if any(
                                    getattr(_c, "metadata", {}).get("sync", {}).get("guid") == clip_guid
                                    for _c in _track
                                    if isinstance(_c, otio.schema.Clip)
                                ):
                                    _seq_tl_guid = _tg
                                    break
                        if _seq_tl_guid:
                            break

                if _seq_tl_guid is None:
                    # Fallback — snapshot to avoid RuntimeError from concurrent mutation
                    for _tg, (_seq_pl, _xs_tl, _kn) in list(self.plugin.structure._xs_sequence_playlists.items()):
                        try:
                            for _m in _seq_pl.media:
                                if str(_m.uuid) == media_uuid_str:
                                    _seq_tl_guid = _tg
                                    break
                        except Exception:
                            pass
                        if _seq_tl_guid:
                            break

                _is_seq_media = _seq_tl_guid is not None

                # Normalize to the SEQUENCE clip's guid before broadcasting.  The
                # same media is a separate OTIO object in the bin vs the sequence,
                # with different sync guids; only the sequence clip's guid is shared
                # across peers.  Selecting via the bin would otherwise broadcast the
                # bin guid, which the peer (which only has the sequence) can't match
                # — they end up "looking at something different".  Map bin↔sequence
                # by media basename so both sides exchange the shared identity.
                if _seq_tl_guid and _media_name_hint and self.plugin.manager:
                    _seq_tl = self.plugin.manager.timelines.get(_seq_tl_guid)
                    if _seq_tl is not None:
                        _base = os.path.splitext(os.path.basename(_media_name_hint))[0].lower()
                        _found = None
                        for _track in _seq_tl.tracks:
                            if _track.kind != otio.schema.TrackKind.Video:
                                continue
                            for _c in _track:
                                if not isinstance(_c, otio.schema.Clip):
                                    continue
                                _cn = os.path.splitext(os.path.basename(_c.name or ""))[0].lower()
                                if _cn == _base:
                                    _found = _c.metadata.get("sync", {}).get("guid")
                                    break
                                # Fallback: match by ExternalReference basename for
                                # xStudio-origin sequence clips whose name is empty.
                                _mr = getattr(_c, "media_reference", None)
                                if isinstance(_mr, otio.schema.ExternalReference):
                                    _ref_base = os.path.splitext(
                                        os.path.basename(_mr.target_url or "")
                                    )[0].lower()
                                    if _ref_base and _ref_base == _base:
                                        _found = _c.metadata.get("sync", {}).get("guid")
                                        break
                            if _found:
                                break
                        if _found and _found != clip_guid:
                            _log(f"[SEL] normalize bin→sequence clip guid {str(clip_guid)[:8]}→{_found[:8]}")
                            clip_guid = _found

                # Determine view_mode from what is actually being viewed, in
                # priority order:
                #   1. PSM=False  → an isolated single clip → "source".
                #   2. viewing a Timeline container → "sequence".
                #   3. viewing a Playlist container (the bin) → "source", even if
                #      the media also appears in a sequence — selecting it in the
                #      bin is a source/bin selection, not a sequence selection.
                #      (Keying off "_is_seq_media" here mislabelled bin clicks as
                #      mode=sequence and desynced the peers.)
                #   4. fallback when container type is unknown.
                _in_single_clip = (self._last_pinned_source_mode is False)
                if _in_single_clip:
                    view_mode = "source"
                elif is_timeline:
                    view_mode = "sequence"
                elif is_playlist:
                    view_mode = "source"
                else:
                    view_mode = "sequence" if _is_seq_media else "source"
                # Track unconditionally — PSM True→False handler reads this to
                # broadcast mode=source even when the show_atom was not suppressed.
                if _media_name_hint:
                    self.plugin._last_show_atom_media = _media_name_hint
                    self.plugin._last_show_atom_seq_tl_guid = _seq_tl_guid
                    self.plugin._last_show_atom_at = time.monotonic()
                # Echo guard: suppress the show_atom burst fired after we call
                # select_all() / set_on_screen_source in apply_selection.
                if time.monotonic() < self.plugin._selection_broadcast_suppress_until:
                    _log("[SEL] → suppressed (echo guard)")
                    return
                # Extended clip-specific guard: a POLL-SLOW can delay the show_atom
                # by 2+ s, past the short time window above.  If this show_atom is
                # for the exact clip we most recently applied remotely, suppress it
                # (it's the delayed echo, not a new local action).  A local switch
                # to a *different* clip is not suppressed (different guid).
                if (
                    clip_guid
                    and clip_guid == self._applied_clip_echo_guid
                    and time.monotonic() < self._applied_clip_echo_until
                ):
                    _log("[SEL] → suppressed (delayed clip echo)")
                    return
                # Suppress show_atoms fired while xStudio's sequence is playing
                # through clips — those aren't user selections, they're scan-through
                # events.  But allow the first one after play starts (race guard:
                # poll may have already set _last_polled_playing before this fires).
                # Never suppress when already in single-clip mode: those are always
                # deliberate user clip-switches, not playback scan-through events.
                _playing_just_started = (time.monotonic() - self.plugin._playing_started_at < 0.3)
                if (
                    _is_seq_media
                    and self.plugin._last_polled_playing
                    and not _playing_just_started
                    and not _in_single_clip
                ):
                    _log("[SEL] → suppressed (playing through sequence)")
                    return
                if (
                    clip_guid
                    and self.plugin.manager
                    and self.plugin.manager.status == STATE_SYNCED
                ):
                    self._last_viewed_clip_guid = clip_guid
                    if view_mode == "sequence" and _seq_tl_guid:
                        self.plugin.manager.active_timeline_guid = _seq_tl_guid
                    elif view_mode == "source":
                        clip_tl_guid = self.plugin.manager.get_or_create_clip_timeline(clip_guid)
                        if clip_tl_guid:
                            self.plugin.manager.active_timeline_guid = clip_tl_guid
                    self.broadcast_view_state(clip_guid, view_mode)
                    _log(f"[SEL] → broadcast view-state clip {clip_guid[:8]} mode={view_mode}")
                return

            if time.monotonic() < self.plugin._reload_suppress_until:
                return
            _log(f"[SEL] show_atom (annotation/bookmark): {_shape} — queuing annotation flush")
            if self.plugin.manager and self.plugin.manager.status == STATE_SYNCED:
                self.plugin._annotation_pending_time = time.monotonic()
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
        current_remote = getattr(self.plugin.active_playhead, "remote", None)
        if ph_remote != current_remote:
            try:
                new_ph = Playhead(self.plugin.connection, ph_remote)
                self._carry_over_playback_mode(new_ph)
                self.plugin.active_playhead = new_ph
                _log(f"[SEL] viewport_playhead_atom Form-2: active playhead updated viewport={event[2]!r}")
            except Exception:
                _log_exc("on_global_playhead_event: failed to update playhead")

        # Subscribe to viewed container's selection actor events, and its event
        # group (for add_media detection).  Both re-subscribe when the viewed
        # container changes — important for a peer that joined an empty session
        # and only later views/creates a container.
        try:
            container = self.get_viewed_container_safe()
            if container:
                self.subscribe_container_selection(container)
                self.plugin.structure.subscribe_viewed_container_events(container)
        except Exception:
            _log_exc("[SEL] Failed to subscribe to container events")

    # ── container selection subscription ──────────────────────────────

    @bounded(_PLAYHEAD_TIMEOUT_MS)
    def subscribe_container_selection(self, container) -> None:
        """Subscribe to the container's selection actor to receive selection events."""
        try:
            container_uuid = str(container.uuid)
            if self._current_selection_container_uuid == container_uuid:
                return

            if self._current_selection_sub_id is not None:
                try:
                    self.plugin.unsubscribe_from_event_group(self._current_selection_sub_id)
                except Exception:
                    pass
                self._current_selection_sub_id = None
                self._current_selection_container_uuid = None

            # Get selection actor
            selection_actor = self.plugin.connection.request_receive(
                container.remote, selection_actor_atom()
            )[0]

            from xstudio.api.auxiliary import ActorConnection
            selection_conn = ActorConnection(self.plugin.connection, selection_actor)

            # Subscribe
            self._current_selection_sub_id = self.plugin.subscribe_to_event_group(
                selection_conn, self.on_selection_event
            )
            self._current_selection_container_uuid = container_uuid
            _log(
                f"[SEL] Subscribed to selection actor events for"
                f" container={type(container).__name__} uuid={container_uuid[:8]}"
            )
            self.enqueue_selection_update()
        except Exception:
            _log_exc("[SEL] Failed to subscribe to container selection events")

    def on_selection_event(self, event) -> None:
        """Fires when selection actor changes selection."""
        if not (
            len(event) > 1
            and isinstance(event[0], event_atom)
            and (isinstance(event[1], source_atom) or isinstance(event[1], selection_changed_atom))
        ):
            return
        _log(f"[SEL] Selection event fired ({type(event[1]).__name__}) — queuing resolution")
        # Throttle the blocking current_playhead() query: on_global_playhead_event
        # already maintains active_playhead, so re-querying it on every selection
        # event is redundant.  During a selection storm those per-event blocking
        # reads stack into multi-second stalls (each bounded read costs up to its
        # timeout).  At most once per second is plenty to catch a real change.
        now = time.monotonic()
        if now - self._last_playhead_check >= 1.0:
            self._last_playhead_check = now
            self.check_and_update_active_playhead()
        self.enqueue_selection_update()

    def enqueue_selection_update(self) -> None:
        """Enqueue selection resolution to the poll thread command queue."""
        self.plugin._cmd_queue.put(("resolve_selection", None))

    # ── active playhead management ─────────────────────────────────────

    @bounded(_PLAYHEAD_TIMEOUT_MS)
    def check_and_update_active_playhead(self) -> None:
        """Query the active playhead from xStudio and cache its reference if changed."""
        try:
            ph = self.plugin.current_playhead()
        except Exception:
            return

        if ph:
            current_remote = getattr(self.plugin.active_playhead, "remote", None)
            if ph.remote != current_remote:
                self._carry_over_playback_mode(ph)
                self.plugin.active_playhead = ph
                _log(f"[position_atom] active playhead updated: {ph.remote}")

    def _reacquire_active_playhead(self) -> None:
        """Re-acquire a live playhead after the previous reference went stale.

        Called from the poll thread when an actor call to ``active_playhead``
        timed out.  ``current_playhead()`` queries the global-playhead-events
        actor (not the dead playhead), so it is safe; it is still bounded by a
        timeout in case that path is also slow.  On success the new playhead is
        stored.
        """
        try:
            with bounded_timeout(self.plugin.connection, _PLAYHEAD_TIMEOUT_MS):
                ph = self.plugin.current_playhead()
                if ph:
                    self._carry_over_playback_mode(ph)
                    self.plugin.active_playhead = ph
                    _log(f"[position_atom] re-acquired live playhead: {ph.remote}")
        except Exception:
            _log_exc("_reacquire_active_playhead: failed")

    def on_playhead_attribute_changed(self, attr, role) -> None:
        """Fires when playhead position, play state, or loop mode changes."""
        if attr.name == "Loop Mode":
            self._on_loop_mode_changed()
            return

        if attr.name not in ("Logical Frame", "playing"):
            return

        if not self.plugin.active_playhead:
            return

        try:
            playing = self.plugin.active_playhead.playing
            frame = self.plugin.active_playhead.position
            fps = self.plugin.active_playhead.frame_rate.fps() or 25.0
        except Exception:
            return

        if frame < 0:
            return

        # Echo guard: a remote playback apply just moved our playhead.  The exact
        # frame match handles the simple case; the rolling time window handles
        # rapid scrubbing, where async attribute_changed callbacks lag behind
        # _last_applied_frame and would otherwise echo stale frames back.
        if frame == self.plugin._last_applied_frame:
            return
        if time.monotonic() < self.plugin._playback_apply_suppress_until:
            return

        # On first run there's no known baseline to diff against. Treat it as
        # a change unconditionally rather than seeding the baseline from this
        # same reading — comparing a value against itself just-set from it
        # always reads as "unchanged", silently swallowing whatever the very
        # first observed state is (most visibly: hitting play as the first
        # action after connecting never broadcast, since nothing else fired
        # an event afterward to "catch" it — only scrubbing did, because it
        # fires many events in a row and later ones compare against a real
        # baseline).
        first_run = self.plugin._last_polled_playing is None

        # Check playing state change
        playing_changed = first_run or (playing != self.plugin._last_polled_playing)

        if not first_run:
            # Skip frame updates while playing if play state didn't change
            if playing and not playing_changed:
                return

            # If paused and frame hasn't changed, skip
            if not playing and not playing_changed:
                if frame == self.plugin._last_polled_frame:
                    return

        # Update cache to prevent redundant broadcasts
        self.plugin._last_polled_playing = playing
        self.plugin._last_polled_frame = frame

        # Construct the unified view-state payload (position + current view).
        # The view_mode/clip_guid describe what we are currently viewing so a
        # peer receiving this position update also keeps the correct mode/clip.
        state = {
            "playing": playing,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(frame),
                "rate": fps,
            },
            "playback_mode": self._get_playback_mode(),
            "view_mode": self._cur_view_mode,
            "clip_guid": self._cur_clip_guid,
        }

        # Mark local playback as active so an echoed selection from a following
        # peer doesn't seek our own playhead to a clip start mid-scrub.
        self.plugin._local_scrub_active_until = time.monotonic() + 0.4

        # Position-only updates while paused (scrubbing) are rate-limited:
        # xStudio fires one of these per rendered frame (~60 Hz) while dragging
        # the playhead, far more than a peer needs to stay in sync, and the
        # flood makes the receiver fall behind and apply a backlog of already-
        # stale positions.  Play/pause transitions always go out immediately;
        # the latest throttled position is held in _pending_scrub_state and
        # flushed by the poll loop so the final scrub position is never lost.
        now = time.monotonic()
        if not playing and not playing_changed:
            if now - self._last_scrub_broadcast_at < _SCRUB_BROADCAST_INTERVAL:
                self._pending_scrub_state = state
                self._pending_scrub_due = self._last_scrub_broadcast_at + _SCRUB_BROADCAST_INTERVAL
                return
        self._pending_scrub_state = None
        self._last_scrub_broadcast_at = now

        # Enqueue the broadcast command to be processed asynchronously
        _log(
            f"Event: queuing playback state broadcast frame={frame} playing={playing} "
            f"(source_attr={attr.name})"
        )
        self.plugin._cmd_queue.put(("broadcast_playback_state", state))

    def flush_pending_scrub_broadcast(self) -> None:
        """Send the most recently throttled scrub position once its deadline passes.

        Called from the poll loop.  Without this, a scrub that stops right after
        a throttled (unsent) position update would leave peers one position
        behind indefinitely, since no further attribute_changed event would
        arrive to trigger the send.
        """
        if self._pending_scrub_state is None:
            return
        if time.monotonic() < self._pending_scrub_due:
            return
        state = self._pending_scrub_state
        self._pending_scrub_state = None
        self._last_scrub_broadcast_at = time.monotonic()
        _log(
            f"Event: queuing playback state broadcast frame={state['current_time']['value']:.0f} "
            f"playing={state['playing']} (source_attr=scrub-flush)"
        )
        self.plugin._cmd_queue.put(("broadcast_playback_state", state))

    # ── deferred seek ──────────────────────────────────────────────────

    @bounded(_PLAYHEAD_TIMEOUT_MS)
    def apply_pending_seek(self) -> None:
        """Apply a deferred sequence-playhead seek once its deadline has passed.

        After a remote clip-selection triggers ``set_on_screen_source``, xStudio
        fires two ``viewport_playhead_atom`` Form-2 events roughly 200 ms apart.
        Each one updates ``active_playhead`` via ``on_global_playhead_event``.
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
        if not self.plugin.active_playhead:
            return
        try:
            self.plugin.active_playhead.position = frame
            _log(f"Deferred seek: applied frame {frame}")
        except Exception:
            _log_exc(f"Deferred seek: failed at frame {frame}")

    # ── selection resolution ───────────────────────────────────────────

    @bounded(_PLAYHEAD_TIMEOUT_MS)
    def resolve_and_broadcast_selection(self) -> None:
        """Resolve xStudio viewport container and selection state on change, and broadcast."""
        try:
            session_actor = self.plugin.connection.api.session.remote
            result = self.plugin.connection.request_receive_timeout(
                100, session_actor, viewport_active_media_container_atom()
            )[0]
            container_uuid = str(result.uuid)
            c = Container(self.plugin.connection, result.actor)
            try:
                c_type = c.type
            except RuntimeError as re:
                if "invalid_argument" in str(re):
                    return
                raise

            if c_type == "Timeline":
                container = Timeline(self.plugin.connection, result.actor, result.uuid)
            elif c_type == "Subset":
                container = Subset(self.plugin.connection, result.actor, result.uuid)
            elif c_type == "ContactSheet":
                container = ContactSheet(self.plugin.connection, result.actor, result.uuid)
            else:
                container = Playlist(self.plugin.connection, result.actor, result.uuid)

            is_timeline = isinstance(container, Timeline)
            is_playlist = isinstance(container, Playlist)
            self.plugin._viewport_container_is_playlist = is_playlist
            self.plugin._viewport_container_is_timeline = is_timeline

            clip_name = None
            clip_uuid_str = None
            if is_timeline:
                try:
                    selected_items = container.selection
                    sel_names = [
                        f"{getattr(i, 'name', '')} ({type(i).__name__})"
                        for i in selected_items
                    ]
                    if self._last_sel_names != sel_names:
                        _log(f"[SEL] Timeline.selection changed: {sel_names}")
                        self._last_sel_names = sel_names
                    for item in selected_items:
                        if type(item).__name__ == "Clip":
                            clip_name = getattr(item, "name", None)
                            if item.media:
                                clip_uuid_str = str(item.media.uuid)
                            break
                except Exception:
                    _log_exc("[SEL] Timeline.selection poll failed")
            elif is_playlist:
                try:
                    sel = container.playhead_selection
                    selected_sources = sel.selected_sources
                    src_names = [s.name for s in selected_sources]
                    if self._last_src_names != src_names:
                        _log(f"[SEL] Playlist.playhead_selection changed: {src_names}")
                        self._last_src_names = src_names
                    if selected_sources:
                        clip_name = selected_sources[0].name
                        clip_uuid_str = str(selected_sources[0].uuid)
                except Exception:
                    _log_exc("[SEL] Playlist.playhead_selection poll failed")

            if (
                container_uuid != self._last_logged_container_uuid
                or clip_name != self._last_logged_clip_name
            ):
                _log(f"[SEL] container={c_type} uuid={container_uuid[:8]} clip={clip_name!r}")
                self._last_logged_container_uuid = container_uuid
                self._last_logged_clip_name = clip_name

            # Update annotation fallback: flat-playlist path needs to know what clip
            # is currently viewed when resolve_clip_at_frame returns None.
            if clip_name and self.plugin.manager:
                cg = None
                if clip_uuid_str:
                    cg = self.plugin.media.sync_guid_for_xs_uuid(clip_uuid_str, container_uuid)
                if not cg:
                    cg = self.plugin.media.clip_guid_for_media_name(clip_name)
                if cg:
                    self._last_viewed_clip_guid = cg

            # Detect Pinned Source Mode transitions: False→True means the user
            # returned to sequence/timeline view without going through RV.
            if (
                self.plugin.active_playhead
                and not self.plugin._applying_pinned_mode
                and self.plugin.manager
                and self.plugin.manager.status == STATE_SYNCED
            ):
                try:
                    # Read PSM from a freshly-acquired LIVE playhead, not the
                    # cached active_playhead: after a source-view switch the
                    # cached reference can point at a destroyed actor and the read
                    # hangs the poll thread for ~100s (below the request_receive
                    # timeout layer, so @bounded on this method does not catch it,
                    # confirmed by the FREEZE-PROBE). current_playhead() queries
                    # the persistent global-playhead actor and is bounded.
                    with bounded_timeout(self.plugin.connection, _PLAYHEAD_TIMEOUT_MS):
                        _psm_ph = self.plugin.current_playhead()
                    psm_attr = _psm_ph.get_attribute("Pinned Source Mode") if _psm_ph else None
                    if psm_attr is not None:
                        with bounded_timeout(self.plugin.connection, _PLAYHEAD_TIMEOUT_MS):
                            psm = psm_attr.value()
                        if (
                            self._last_pinned_source_mode is not None
                            and psm != self._last_pinned_source_mode
                        ):
                            _log(
                                f"[SEL] Pinned Source Mode:"
                                f" {self._last_pinned_source_mode} → {psm}"
                            )
                            if psm is True:
                                # User re-pinned to the timeline — broadcast clear so
                                # peers exit single-clip mode too.
                                seq_tl_guid = self.plugin.manager.sequence_timeline_guid
                                if seq_tl_guid:
                                    self.plugin.manager.active_timeline_guid = seq_tl_guid
                                self.broadcast_view_state(None, "sequence")
                                _log("[SEL] → broadcast view-state: sequence (returned to sequence view)")
                            elif psm is False:
                                # User double-clicked a clip — xStudio enters single-clip
                                # mode.  The show_atom fired ~80 ms ago (suppressed or not);
                                # use _last_show_atom_media to broadcast mode=source so RV
                                # also switches to single-clip view for that clip.
                                _atom_age = time.monotonic() - self.plugin._last_show_atom_at
                                _media_h = self.plugin._last_show_atom_media if _atom_age < 2.0 else None
                                if not _media_h:
                                    _media_h = clip_name  # fallback: current poll value
                                if _media_h:
                                    _cg = None
                                    if clip_uuid_str and _media_h == clip_name:
                                        _cg = self.plugin.media.sync_guid_for_xs_uuid(
                                            clip_uuid_str, container_uuid
                                        )
                                    if not _cg:
                                        _cg = self.plugin.media.clip_guid_for_media_name(_media_h)
                                    if _cg:
                                        self._last_viewed_clip_guid = _cg
                                        _ctg = self.plugin.manager.get_or_create_clip_timeline(_cg)
                                        if _ctg:
                                            self.plugin.manager.active_timeline_guid = _ctg
                                        # xStudio auto-plays on double-click, so tell remote
                                        # peers to also start playing immediately.
                                        self.broadcast_view_state(_cg, "source", playing_override=True)
                                        _log(f"[SEL] PSM True→False: broadcast view-state {_cg[:8]} mode=source playing=True")
                                    else:
                                        _log(f"[SEL] PSM True→False: no clip_guid for {_media_h!r}")
                                else:
                                    _log("[SEL] PSM True→False: no media hint available")
                        self._last_pinned_source_mode = psm
                except Exception:
                    _log_exc("[SEL] Pinned Source Mode poll failed")

        except Exception as e:
            _log_exc(f"[SEL] poll failed: {e}")

    # ── playback state ─────────────────────────────────────────────────

    def _get_playback_mode(self) -> str:
        """Return the wire playback_mode ("play-once"/"loop"/"ping-pong") for the active playhead.

        Deliberately does NOT update ``_last_known_playback_mode`` — every new
        clip's playhead defaults to the engine's raw "Play Once" regardless of
        the user's real preference, so a passive read here would "poison" the
        cache with that default the moment it's used to build a broadcast.
        ``_last_known_playback_mode`` is only updated from genuine signals: a
        local "Loop Mode" attribute-changed event, or a value applied from a
        peer (see ``on_playhead_attribute_changed`` / ``apply_playback_state``).
        """
        ph = self.plugin.active_playhead
        if not ph:
            return "play-once"
        try:
            mode = str(ph.get_attribute("Loop Mode")).strip()
            return _LOOP_MODE_TO_WIRE.get(mode, "play-once")
        except Exception:
            return "play-once"

    def _carry_over_playback_mode(self, ph) -> None:
        """Apply the last-known playback mode onto a newly-acquired playhead.

        Every new clip/media in xStudio gets its own ``Playhead`` object whose
        native "Loop Mode" resets to the engine default (Play Once) instead of
        inheriting whatever mode the session was actually using — silently
        reverting a user's loop/ping-pong choice on every clip switch. Carry
        the last mode we actually observed forward so switching clips (or
        reacquiring a stale playhead) doesn't reset it.
        """
        if not ph or not self._last_known_playback_mode:
            return
        target = _WIRE_TO_LOOP_MODE.get(self._last_known_playback_mode)
        if target is None:
            return
        try:
            if str(ph.get_attribute("Loop Mode")).strip() != target:
                self._loop_mode_apply_suppress_until = time.monotonic() + 0.4
                ph.set_attribute("Loop Mode", target)
                _log(f"[SEL] carried over Loop Mode={target} onto newly-acquired playhead")
        except Exception:
            _log_exc("_carry_over_playback_mode: failed")

    def _on_loop_mode_changed(self) -> None:
        """Local "Loop Mode" attribute change on the active playhead.

        This is the definitive signal that the user actually chose a mode
        (as opposed to a passive read of a freshly-defaulted new playhead —
        see ``_get_playback_mode``'s docstring). Update the carry-over cache
        and broadcast immediately, mirroring OpenRV's play-mode-changed hook.
        """
        if time.monotonic() < self._loop_mode_apply_suppress_until:
            return
        ph = self.plugin.active_playhead
        if not ph:
            return
        wire_mode = self._get_playback_mode()
        self._last_known_playback_mode = wire_mode
        if not (self.plugin.manager and self.plugin.manager.status == STATE_SYNCED):
            return
        try:
            playing = ph.playing
            frame = ph.position
            fps = ph.frame_rate.fps() or 25.0
        except Exception:
            return
        state = {
            "playing": playing,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(frame),
                "rate": fps,
            },
            "playback_mode": wire_mode,
            "view_mode": self._cur_view_mode,
            "clip_guid": self._cur_clip_guid,
        }
        _log(f"Event: queuing playback_mode broadcast mode={wire_mode} (source_attr=Loop Mode)")
        self.plugin._cmd_queue.put(("broadcast_playback_state", state))

    @bounded(_PLAYHEAD_TIMEOUT_MS)
    def current_playback_state(self) -> dict | None:
        """Return the local playback state dict for inclusion in a state snapshot."""
        if not self.plugin.active_playhead:
            return None
        try:
            frame = self.plugin.active_playhead.position
            fps = self.plugin.active_playhead.frame_rate.fps() or 25.0
            playing = self.plugin.active_playhead.playing
            return {
                "playing": playing,
                "current_time": {
                    "OTIO_SCHEMA": "RationalTime.1",
                    "value": float(frame),
                    "rate": fps,
                },
                "playback_mode": self._get_playback_mode(),
            }
        except Exception:
            return None

    def broadcast_view_state(
        self,
        clip_guid: "str | None",
        view_mode: str,
        playing_override: "bool | None" = None,
    ) -> None:
        """Broadcast the unified view-state (selection + playback) message.

        The retired SELECTION_1.0 is folded into PLAYBACK_SETTINGS: this sends the
        current playhead position/play state together with ``view_mode`` and
        ``clip_guid`` so one message fully describes what this peer is viewing.
        Also records the current view locally so subsequent position-change
        broadcasts carry the same mode/clip.
        """
        if not (self.plugin.manager and self.plugin.manager.status == STATE_SYNCED):
            return
        self._cur_view_mode = view_mode
        self._cur_clip_guid = clip_guid or None
        # This is a LOCAL user action — guard against in-flight remote messages
        # (sent before the remote knew about this local change) overriding it.
        # Only set the guard when not inside a remote-apply echo suppression window
        # (i.e., this broadcast is genuinely local, not an echo of a remote apply).
        if time.monotonic() >= self.plugin._selection_broadcast_suppress_until:
            self._local_view_action_until = time.monotonic() + 1.0
        state = self.current_playback_state() or {
            "playing": False,
            "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 0.0, "rate": 24.0},
            "playback_mode": self._get_playback_mode(),
        }
        state["view_mode"] = view_mode
        state["clip_guid"] = clip_guid or None
        if playing_override is not None:
            state["playing"] = playing_override
        self.plugin._cmd_queue.put(("broadcast_playback_state", state))

    # ── viewport helpers ───────────────────────────────────────────────

    def get_viewed_container_safe(self):
        """Safely query viewed_container, handling expected RuntimeError if empty.

        :returns: The viewed container instance, or ``None``.
        """
        try:
            return self.plugin.connection.api.session.viewed_container
        except RuntimeError as e:
            if "invalid_argument" in str(e):
                return None
            raise
        except Exception:
            return None

    def get_local_viewed_timeline_guid(self) -> str | None:
        """Query the active container from the viewport and map it to its sync GUID.

        :returns: GUID string, or ``None`` if it cannot be resolved.
        :rtype: str or None
        """
        if not self.plugin.manager:
            return None
        try:
            session_actor = self.plugin.connection.api.session.remote
            result = self.plugin.connection.request_receive_timeout(
                100, session_actor, viewport_active_media_container_atom()
            )[0]
            container_uuid = str(result.uuid)
            c = Container(self.plugin.connection, result.actor)
            c_type = c.type
        except Exception:
            return None

        if c_type == "Timeline":
            # Check if this container UUID is one of our synced sequence timelines.
            for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
                if xs_tl and str(xs_tl.uuid) == container_uuid:
                    return tl_guid
            return container_uuid
        else:
            # Viewing a Playlist (or Subset/ContactSheet).
            # Check if it's a flat playlist.
            for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
                if xs_tl is None and str(pl.uuid) == container_uuid:
                    return tl_guid

            # Check if we are viewing a sequence's parent playlist (source view of a clip).
            matching_pl = None
            for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
                if str(pl.uuid) == container_uuid:
                    matching_pl = pl
                    break

            if matching_pl:
                try:
                    sel = matching_pl.playhead_selection
                    selected_sources = sel.selected_sources
                    if len(selected_sources) == 1:
                        clip_guid = self.plugin.media.sync_guid_for_xs_uuid(
                            str(selected_sources[0].uuid), container_uuid
                        )
                        if not clip_guid:
                            clip_guid = self.plugin.media.clip_guid_for_media_name(
                                selected_sources[0].name
                            )
                        if clip_guid:
                            return self.plugin.manager.get_or_create_clip_timeline(clip_guid)
                except Exception:
                    pass
                return self.plugin.manager.sequence_timeline_guid

            return container_uuid

    def _apply_sequence_view(self, tl_guid: "str | None") -> bool:
        """Switch the on-screen source to the sequence identified by *tl_guid*.

        Sequence mode is addressed by the **shared** sequence ``timeline_guid``,
        not by clip guid — clip guids differ per peer (bin/sequence/clip-timeline
        are distinct OTIO objects), so a clip-based search fails ("no playlist
        found") for a peer's sequence.  The sequence guid is the same on every
        peer, so we look it up directly in ``_sync_playlists`` and show it.

        :returns: True if the sequence was found and shown; False to let the
            caller fall back to the clip-based path.
        """
        if not (tl_guid and self.plugin.manager
                and tl_guid in self.plugin._sync_playlists):
            return False
        pl, tl = self.plugin._sync_playlists[tl_guid]
        target = tl if tl is not None else pl
        if target is None:
            return False
        self.plugin.manager.active_timeline_guid = tl_guid
        # Suppress the show_atom burst the switch fires from echoing back.
        self.plugin._selection_broadcast_suppress_until = time.monotonic() + 0.5
        try:
            self.plugin.connection.api.session.viewed_container = target
            self.plugin.connection.api.session.set_on_screen_source(target)
            if self.plugin.active_playhead:
                try:
                    self.plugin._applying_pinned_mode = True
                    self.plugin.active_playhead.set_attribute("Pinned Source Mode", True)
                    self._last_pinned_source_mode = True
                finally:
                    self.plugin._applying_pinned_mode = False
            _log(
                f"apply view-state: sequence → {getattr(target, 'name', '?')!r}"
                f" ({tl_guid[:8]})"
            )
            return True
        except Exception:
            _log_exc("apply view-state: sequence switch failed")
            return False

    # ── apply remote playback state ───────────────────────────────────

    def apply_playback_state(self, state: dict) -> None:
        """Apply an incoming unified view-state to the local xStudio session.

        Called from the poll thread via the ``on_playback_changed`` callback for
        every PLAYBACK_SETTINGS message — which is now the single view-state
        message (SELECTION_1.0 retired).  It applies, atomically:

        * the **view** — when ``view_mode``/``clip_guid`` change, switch the
          on-screen source / mode via the (tested) selection-apply logic; and
        * the **position** — the message ``current_time`` is authoritative, so
          the selection-apply's own clip-start seek is suppressed (see the
          playback-active guard) and the frame from the message wins.

        Updates ``_last_applied_frame``, ``_last_polled_frame``, and
        ``_last_polled_playing`` so the poll does not echo remote applies back.
        """
        if not self.plugin.active_playhead:
            return

        # 1. View switch (mode / active clip).  Switch the on-screen source only
        #    when it actually changes:
        #      * mode changed (sequence↔source), or
        #      * source mode and the isolated clip changed.
        #    Crucially, in SEQUENCE mode the active clip changes continuously as
        #    the playhead scrubs across cuts — but the source stays the sequence
        #    and the clip is derived from the frame, so a clip-only change must
        #    NOT re-switch or seek (that was the clip-start jump).  After a real
        #    switch we cancel any clip-start seek it queued, because the message
        #    frame below is the authoritative position in both modes.
        view_mode = state.get("view_mode")
        clip_guid = state.get("clip_guid")
        view_tl_guid = state.get("timeline_guid")
        if view_mode is not None:
            mode_changed = view_mode != self._last_applied_view_mode
            clip_changed = clip_guid != self._last_applied_clip_guid
            tl_changed = view_tl_guid != self._last_applied_tl_guid
            # Guard: if the local user just made a view selection, in-flight remote
            # messages from BEFORE the remote knew about it can arrive and hijack
            # the local selection.  Suppress remote view switches for ~1 s after
            # the local action.  (Only the view switch is suppressed — the position/
            # play path below still applies if the timeline matches.)
            if (
                time.monotonic() < self._local_view_action_until
                and (mode_changed or clip_changed or tl_changed)
                and (view_mode != self._cur_view_mode or clip_guid != self._cur_clip_guid)
            ):
                _log(
                    f"apply_playback_state: skipping view switch"
                    f" (local action guard active, remote={view_mode}/{(clip_guid or '')[:8]},"
                    f" local={self._cur_view_mode}/{(self._cur_clip_guid or '')[:8]})"
                )
                # Skip the view switch; fall through to position/play if timeline matches.
                view_mode = None  # neutralise the view block below
            # Switch the on-screen source when:
            #   * mode flips (sequence↔source),
            #   * source mode and the isolated clip changes, or
            #   * sequence mode and the *sequence* (timeline) changes — opening a
            #     different sequence.  A clip-only change inside one sequence
            #     (scrubbing across cuts) does NOT switch.
            if view_mode == "sequence":
                # Switch to the sequence by its shared timeline guid (robust to
                # per-peer clip-guid differences).  Only on a real transition:
                # mode flip or a different sequence — not on clip changes while
                # scrubbing one sequence.
                if mode_changed or tl_changed:
                    if not self._apply_sequence_view(view_tl_guid):
                        # Fall back to the clip-based path if the sequence guid
                        # isn't registered locally for some reason.
                        try:
                            self.apply_selection({"clip_guid": clip_guid or "", "view_mode": "sequence"})
                        except Exception:
                            _log_exc("apply_playback_state: sequence view switch failed")
                    self._pending_seek_frame = None
                elif clip_changed and clip_guid:
                    # Sequence-mode clip change: the peer selected / switched to a
                    # different clip.  The SENDER suppresses playhead scan-through
                    # while playing (see the "suppressed (playing through
                    # sequence)" guard), so a clip_guid change that reaches us is a
                    # deliberate user action, not a cut crossed during playback —
                    # we honour it whether or not the peer is playing (playing must
                    # never block a selection).  Highlight in place — no source
                    # switch, no seek — guarded against the actor-teardown crash
                    # (D2/D3).
                    self._highlight_timeline_item(clip_guid)
            elif view_mode == "source":
                if mode_changed or clip_changed:
                    try:
                        self.apply_selection({"clip_guid": clip_guid or "", "view_mode": "source"})
                    except Exception:
                        _log_exc("apply_playback_state: source view switch failed")
                    self._pending_seek_frame = None
            self._last_applied_view_mode = view_mode
            self._last_applied_clip_guid = clip_guid
            self._last_applied_tl_guid = view_tl_guid

        incoming_tl_guid = state.get("timeline_guid")
        _tl_mismatch = False
        if incoming_tl_guid and self.plugin.manager:
            # Check against target active_timeline_guid first (handles the selection change transition)
            if incoming_tl_guid != self.plugin.manager.active_timeline_guid:
                # Query actual viewed container GUID as a fallback in case
                # active_timeline_guid is transitioning.  Bounded: it reads the
                # viewed-container actor, which can also be mid-transition.  On
                # timeout/error, fall through and apply (better than dropping it).
                local_tl_guid = None
                try:
                    with bounded_timeout(self.plugin.connection, _PLAYHEAD_TIMEOUT_MS):
                        local_tl_guid = self.get_local_viewed_timeline_guid()
                except Exception:
                    local_tl_guid = None
                if local_tl_guid and local_tl_guid != incoming_tl_guid:
                    _tl_mismatch = True
                    _log(
                        f"RECV playback state: mismatched timeline_guid"
                        f" (local={local_tl_guid[:8]},"
                        f" target={self.plugin.manager.active_timeline_guid[:8]},"
                        f" incoming={incoming_tl_guid[:8]})"
                    )
        # If timelines mismatch, still apply a play command — a view switch may
        # have just landed and the local guid is still catching up.  Drop only the
        # seek (frame) when mismatched; do not silently drop playing=True.
        if _tl_mismatch and not state.get("playing", False):
            _log("RECV playback state: mismatched timeline_guid — ignoring (not playing)")
            return

        playing = state.get("playing", False)
        current_time = state.get("current_time", {})
        # Protocol value is 0-based (RV sends frame-1; xStudio frames are 0-based).
        frame = max(0, int(current_time.get("value", 0)))
        playback_mode = state.get("playback_mode")

        ph = self.plugin.active_playhead

        # The playhead property reads (.playing getter, .position setter) are
        # synchronous request_receive calls bounded only by the connection's
        # default_timeout_ms (100 s).  If active_playhead is stale — it points
        # to a playhead actor destroyed during a source-view switch — that read
        # blocks the poll thread for the full 100 s.  bounded_timeout lowers the
        # C++-level timeout so a dead actor raises promptly; we then re-acquire
        # the live playhead.  (A Python-thread timeout cannot help: the blocking
        # dequeue holds the GIL.)
        try:
            with bounded_timeout(self.plugin.connection, _PLAYHEAD_TIMEOUT_MS):
                # Sync xStudio's native loop mode so both peers' engines agree,
                # not just their synced state — mirrors OpenRV's setPlayMode call.
                target_loop_mode = _WIRE_TO_LOOP_MODE.get(playback_mode)
                if target_loop_mode is not None:
                    try:
                        if str(ph.get_attribute("Loop Mode")).strip() != target_loop_mode:
                            self._loop_mode_apply_suppress_until = time.monotonic() + 0.4
                            ph.set_attribute("Loop Mode", target_loop_mode)
                            _log(f"RECV playback: set Loop Mode={target_loop_mode} (playback_mode={playback_mode})")
                        self._last_known_playback_mode = playback_mode
                    except Exception:
                        _log_exc("RECV playback: set Loop Mode failed")
                playing_changed = (playing != ph.playing)
                if playing_changed:
                    # Update cache only when we actually change xStudio's play
                    # state so the poll does not mistake a no-op remote event
                    # for a local change.
                    self.plugin._last_polled_playing = playing
                    if playing:
                        self.plugin._playing_started_at = time.monotonic()
                    ph.playing = playing
                # Apply position if we are paused, or the play/pause state changed,
                # but NOT when the timeline guid mismatched (the view switch has not
                # finished landing — seeking on the wrong timeline would be wrong).
                if (not playing or playing_changed) and not _tl_mismatch:
                    self.plugin._last_applied_frame = frame
                    self.plugin._last_polled_frame = frame
                    # Suppress the echo: ph.position fires attribute_changed
                    # asynchronously; refresh a rolling window so those callbacks
                    # don't re-broadcast while a peer is driving playback.
                    self.plugin._playback_apply_suppress_until = time.monotonic() + 0.4
                    ph.position = frame
        except Exception:
            # xStudio's UI uses the new live playhead; re-acquire it via the
            # global-playhead-events actor (which does NOT touch the dead actor).
            # This apply is skipped; the next PLAYBACK_SETTINGS message lands on
            # the fresh playhead.
            _log("apply_playback_state: stale playhead — re-acquiring live playhead")
            self.plugin.active_playhead = None
            self._reacquire_active_playhead()

    # ── clip playhead lookup ──────────────────────────────────────────

    def playhead_for_clip(self, clip_guid: str):
        """Return the xStudio Playhead for the sequence playlist that contains *clip_guid*.

        Iterates ``_sync_playlists`` and checks each OTIO timeline's media track
        for the clip.

        Falls back to ``None`` so the caller can fall back to ``active_playhead``.
        """
        if not self.plugin.manager:
            return None
        try:
            for tl_guid, (playlist, _) in self.plugin._sync_playlists.items():
                otio_tl = self.plugin.manager.timelines.get(tl_guid)
                if otio_tl is None:
                    continue
                for track in otio_tl.tracks:
                    for child in track:
                        if child.metadata.get("sync", {}).get("guid") == clip_guid:
                            ph = playlist.playhead
                            _log(
                                f"playhead_for_clip: {clip_guid[:8]} found"
                                f" in tl={tl_guid[:8]} → playhead ok"
                            )
                            return ph
            _log(f"playhead_for_clip: {clip_guid[:8]} not found in any sequence timeline")
            return None
        except Exception:
            _log_exc("playhead_for_clip: exception")
            return None

    # ── apply remote selection ────────────────────────────────────────

    @bounded(_PLAYHEAD_TIMEOUT_MS)
    def _timeline_recently_rebuilt(self) -> bool:
        """True if a structural timeline rebuild happened within the settle window.

        Sending ``item_selection_atom`` into a sequence whose ClipActors are
        being torn down and recreated (which every ``load_otio(clear=True)``
        rebuild does) races that teardown and segfaults xStudio at the C++
        event-callback level — a crash no Python ``try/except`` can catch (see
        design D2).  Every structural-rebuild site already advances the
        plugin-global ``_structural_mutation_suppress_until`` to ``now + 1.5s``,
        so "still inside that window" is a ready-made "structure just changed"
        signal; we reuse it rather than thread a new per-timeline timestamp
        through every load_otio call site.  It is global (any rebuild suppresses
        any highlight) and deliberately conservative — a dropped highlight is
        harmless, a segfault is not.
        """
        return time.monotonic() < self.plugin._structural_mutation_suppress_until

    def _highlight_timeline_item(self, clip_guid: str) -> None:
        """Select/highlight *clip_guid* in its sequence timeline, in place.

        Does NOT switch the on-screen source or seek — it only sets the
        timeline's item selection, mirroring xStudio's own "select without
        isolating" behaviour.  Best-effort and defensively guarded:

        * skipped (kill-switch) when ``_ENABLE_TIMELINE_ITEM_HIGHLIGHT`` is off;
        * skipped (crash-race guard, design D2) when the target timeline was
          structurally rebuilt within the stability window;
        * skipped silently when the clip can't be resolved to a live item.

        The ``item_selection_atom`` send stays wrapped in ``try/except`` as
        defence-in-depth: the guard shrinks the crash window but a rebuild could
        still begin mid-send.
        """
        if not _ENABLE_TIMELINE_ITEM_HIGHLIGHT:
            return
        if not clip_guid or not self.plugin.manager:
            return
        if self._timeline_recently_rebuilt():
            _log(
                f"RECV highlight: skip clip {clip_guid[:8]} — timeline recently"
                f" rebuilt (stability guard, D2)"
            )
            return

        # Resolve the OTIO timeline + live xStudio timeline that contain this
        # clip guid.  _sync_playlists is keyed by xs parent-playlist guid, which
        # differs from the OTIO timeline guid for sequences, so cross-reference
        # via each timeline's xs_parent_playlist_guid metadata (same approach as
        # apply_selection's pass-2 lookup).
        otio_tl = None
        playlist_xs_tl = None
        _otio_key_to_xsp = {
            _otg: (_otl.metadata.get("xs_parent_playlist_guid") or _otg)
            for _otg, _otl in list(self.plugin.manager.timelines.items())
        }
        for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
            cand = self.plugin.manager.timelines.get(tl_guid)
            if cand is None:
                for _otg, _xsp in _otio_key_to_xsp.items():
                    if _xsp == tl_guid:
                        cand = self.plugin.manager.timelines.get(_otg)
                        break
            if cand is None:
                continue
            if any(
                child.metadata.get("sync", {}).get("guid") == clip_guid
                for track in cand.tracks for child in track
            ):
                otio_tl = cand
                playlist_xs_tl = xs_tl
                break
        if otio_tl is None or playlist_xs_tl is None:
            _log(f"RECV highlight: clip {clip_guid[:8]} not found in any synced timeline")
            return

        # Locate the clip's (track, child) index in the OTIO timeline.
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

        # The OTIO manager timeline may carry an extra Annotations track / a
        # different child count than the live xStudio timeline, so the indices
        # are NOT guaranteed positionally aligned — bounds-check before indexing
        # rather than throwing an IndexError logged as a noisy traceback.
        xs_tracks = playlist_xs_tl.stack.children
        xs_track = xs_tracks[target_track_idx] if 0 <= target_track_idx < len(xs_tracks) else None
        xs_children = xs_track.children if xs_track is not None else []
        if target_track_idx != -1 and 0 <= target_child_idx < len(xs_children):
            try:
                xs_child = xs_children[target_child_idx]
                from xstudio.core import UuidActor, UuidActorVec
                ua_vec = UuidActorVec()
                ua_vec.push_back(UuidActor(xs_child.uuid, xs_child.remote))
                # Suppress our own outbound re-broadcast of the selection this
                # send triggers, so applying a peer's highlight doesn't echo
                # back (mirrors RV's _rv_updating echo guard; task 3.2).
                self.plugin._selection_broadcast_suppress_until = time.monotonic() + 0.5
                self.plugin.connection.send(
                    playlist_xs_tl.remote, item_selection_atom(), ua_vec
                )
                _log(
                    f"RECV highlight: selected clip {clip_guid[:8]} at"
                    f" track={target_track_idx} child={target_child_idx}"
                )
            except Exception:
                _log_exc("RECV highlight: failed to set timeline item selection")
        elif target_track_idx != -1:
            _log(
                f"RECV highlight: skip clip {clip_guid[:8]} — OTIO index"
                f" (track={target_track_idx} child={target_child_idx}) out of"
                f" range for xStudio timeline"
            )

    def apply_selection(self, data: dict) -> None:
        """Apply a remotely broadcast clip selection.

        Switches the viewed container to the sequence's parent playlist and sets
        the playlist playhead selection to the targeted clip (mimicking RV source view).
        If selection is cleared, switches back to the sequence timeline and selects all.
        """
        if not self.plugin.active_playhead:
            return
        clip_guid = data.get("clip_guid", "")
        view_mode = data.get("view_mode", "source")

        # Echo guard for ALL apply paths: switching the on-screen source / setting
        # the selection below fires xStudio show_atom/selection events that our own
        # handler would otherwise re-broadcast — bouncing the selection back to the
        # sender.  When two peers start on different clips that produces an endless
        # swap (each applies the other's selection and re-broadcasts its own).
        # Suppress local selection broadcasts briefly while we apply this one.
        self.plugin._selection_broadcast_suppress_until = time.monotonic() + 0.5

        if not clip_guid:
            # Clear / container switch.
            _log(
                f"RECV selection: clear → "
                f"{'sequence' if view_mode == 'sequence' else 'source/playlist'} view"
                f" (mode={view_mode})"
            )
            if self.plugin.manager:
                seq_tl_guid = self.plugin.manager.sequence_timeline_guid
                if seq_tl_guid:
                    self.plugin.manager.active_timeline_guid = seq_tl_guid
                    if seq_tl_guid in self.plugin._sync_playlists:
                        pl, tl = self.plugin._sync_playlists[seq_tl_guid]
                        try:
                            # Switch viewed_container and on_screen_source based on view_mode.
                            viewed_c = tl if (view_mode == "sequence" and tl is not None) else pl
                            self.plugin.connection.api.session.viewed_container = viewed_c

                            # Update the viewport source
                            if view_mode == "sequence" and tl:
                                self.plugin.connection.api.session.set_on_screen_source(tl)
                                _log("RECV selection clear: set_on_screen_source to timeline (Sequence)")
                                # Clear the timeline item selection (empty vec).
                                # Same actor-teardown crash risk as the highlight
                                # send, so gate it on the same stability guard
                                # (not just the kill-switch flag).
                                if _ENABLE_TIMELINE_ITEM_HIGHLIGHT and not self._timeline_recently_rebuilt():
                                    try:
                                        from xstudio.core import UuidActorVec
                                        self.plugin.connection.send(tl.remote, item_selection_atom(), UuidActorVec())
                                    except Exception:
                                        pass
                                # Restore sequence view: pinnedSourceMode=True pins the playhead
                                # to the full timeline rather than any single selected media item.
                                if self.plugin.active_playhead:
                                    try:
                                        self.plugin._applying_pinned_mode = True
                                        self.plugin.active_playhead.set_attribute("Pinned Source Mode", True)
                                        self._last_pinned_source_mode = True
                                        _log("RECV selection clear: set Pinned Source Mode = True")
                                    except Exception:
                                        _log_exc("RECV selection: failed to set Pinned Source Mode")
                                    finally:
                                        self.plugin._applying_pinned_mode = False
                            else:
                                self.plugin.connection.api.session.set_on_screen_source(pl)
                                _log("RECV selection clear: set_on_screen_source to playlist (Source)")
                                if self.plugin.active_playhead:
                                    try:
                                        self.plugin._applying_pinned_mode = True
                                        self.plugin.active_playhead.set_attribute("Pinned Source Mode", False)
                                        self._last_pinned_source_mode = False
                                        _log("RECV selection clear: set Pinned Source Mode = False")
                                    except Exception:
                                        _log_exc("RECV selection: failed to set Pinned Source Mode")
                                    finally:
                                        self.plugin._applying_pinned_mode = False

                            pl.playhead_selection.select_all()
                            # select_all() fires show_atom for every media item in the
                            # playlist.  Suppress those for 0.5 s — in practice all
                            # echo show_atoms arrive within 150 ms.
                            self.plugin._selection_broadcast_suppress_until = time.monotonic() + 0.5
                        except Exception:
                            _log_exc("RECV selection clear: failed to switch container")

                        # active_playhead is refreshed by the Form-2 viewport_playhead_atom
                        # event that fires after set_on_screen_source completes.
            return

        # Skip if we already broadcast this same clip — this is an echo from RV
        if not self.plugin.manager:
            return
        clip = self.plugin.manager._object_map.get(clip_guid)
        if clip is None or not isinstance(clip, otio.schema.Clip):
            _log(f"RECV selection: guid={clip_guid} not found in object_map")
            return
        _log(f"RECV selection: clip '{clip.name}' guid={clip_guid[:8]} mode={view_mode}")

        # Switch active_timeline_guid to the clip's own single-clip timeline.
        clip_tl_guid = self.plugin.manager.get_or_create_clip_timeline(clip_guid)
        if clip_tl_guid:
            self.plugin.manager.active_timeline_guid = clip_tl_guid

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
            for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
                otio_tl = self.plugin.manager.timelines.get(tl_guid)
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
                if (
                    cname == clip_name
                    or os.path.splitext(os.path.basename(cname))[0] == clip_stem
                ):
                    playlist = pl
                    playlist_xs_tl = xs_tl
                    use_source = True
                    _log(
                        f"RECV selection: matched individual playlist"
                        f" {getattr(pl, 'name', '?')!r}"
                        f" for clip {clip_guid[:8]} ({clip_name!r})"
                    )
                    break

        matched_tl_guid = None  # set during pass-2 fallback
        if playlist is None:
            # Build a reverse map: OTIO guid → (xs_parent_guid, pl, xs_tl) so we can
            # cross-reference _sync_playlists (keyed by xs_parent_guid) against
            # manager.timelines (keyed by OTIO guid).  For sequences these two guids
            # differ, which is why the old direct timelines.get(tl_guid) lookup
            # silently returned None and skipped every sequence entry.
            _otio_key_to_sync = {}
            if self.plugin.manager:
                for _otg, _otl in list(self.plugin.manager.timelines.items()):
                    _xsp = _otl.metadata.get("xs_parent_playlist_guid") or _otg
                    _otio_key_to_sync[_otg] = _xsp

            for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
                # Try direct lookup first; fall back to scanning for a timeline
                # whose xs_parent_playlist_guid matches this _sync_playlists key.
                otio_tl = self.plugin.manager.timelines.get(tl_guid) if self.plugin.manager else None
                if otio_tl is None and self.plugin.manager:
                    for _otg, _xsp in _otio_key_to_sync.items():
                        if _xsp == tl_guid:
                            otio_tl = self.plugin.manager.timelines.get(_otg)
                            break
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

        # Pass-3: name-based fallback — if guid search still finds nothing (e.g.
        # the sequence OTIO wasn't rebuilt yet), search all individual playlists
        # for a single-clip entry whose clip name matches, then use set_selection.
        if playlist is None and clip_stem and self.plugin.manager:
            for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
                try:
                    pl_media = list(pl.media)
                except Exception:
                    continue
                if len(pl_media) != 1:
                    continue
                m_name = getattr(pl_media[0], "name", "") or ""
                if (
                    os.path.splitext(os.path.basename(m_name))[0].lower()
                    == clip_stem.lower()
                ):
                    playlist = pl
                    playlist_xs_tl = xs_tl
                    matched_tl_guid = tl_guid
                    use_source = True
                    _log(
                        f"RECV selection: Pass-3 name match → {getattr(pl, 'name', '?')!r}"
                        f" for {clip_stem!r}"
                    )
                    break

        if playlist is not None:
            # Decide which switching mechanism to use.
            # use_source=True  → pass-1 single-clip individual playlist found.
            # multi-clip seq   → set_on_screen_source + seek to clip start frame.
            # flat playlist    → viewed_container + set_selection (still works for those).
            is_multi_clip = False
            if (
                not use_source
                and matched_tl_guid is not None
                and playlist_xs_tl is not None
                and view_mode == "sequence"
            ):
                otio_tl = self.plugin.manager.timelines.get(matched_tl_guid)
                if otio_tl is not None:
                    n_video = sum(
                        1 for t in otio_tl.tracks
                        if t.kind == otio.schema.TrackKind.Video
                        for c in t if isinstance(c, otio.schema.Clip)
                    )
                    is_multi_clip = n_video > 1

            try:
                # Switch the viewed container in the sidebar.
                # If we are in sequence view and have a timeline, view the timeline.
                # Otherwise view the playlist.
                viewed_c = (
                    playlist_xs_tl
                    if (view_mode == "sequence" and playlist_xs_tl is not None)
                    else playlist
                )
                self.plugin.connection.api.session.viewed_container = viewed_c

                if use_source and playlist_xs_tl is not None:
                    # Single-clip individual playlist: just show it.
                    # Suppress the show_atom burst set_on_screen_source fires so it
                    # doesn't echo back to the peer that sent us this selection.
                    self.plugin._selection_broadcast_suppress_until = time.monotonic() + 0.5
                    self._applied_clip_echo_guid = clip_guid
                    self._applied_clip_echo_until = time.monotonic() + 3.0
                    self.plugin.connection.api.session.set_on_screen_source(playlist_xs_tl)
                    _log(
                        f"RECV selection: set_on_screen_source (individual) → "
                        f"{getattr(playlist_xs_tl, 'name', '?')!r}"
                    )
                elif is_multi_clip:
                    # Multi-clip sequence selection: switch the on-screen source to
                    # the sequence.  Called only on a genuine view transition (the
                    # unified apply skips clip-only changes during a sequence scrub),
                    # and the clip-start seek queued here is cancelled by
                    # apply_playback_state — the message frame is authoritative — so
                    # this no longer fights the scrub position.
                    start_frame = 0
                    try:
                        start_frame = int(clip.range_in_parent().start_time.value)
                    except Exception:
                        # Fallback: sum duration of all preceding items.
                        otio_tl = (
                            self.plugin.manager.timelines.get(matched_tl_guid)
                            if self.plugin.manager else None
                        )
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

                    self.plugin._selection_broadcast_suppress_until = time.monotonic() + 0.5
                    self._applied_clip_echo_guid = clip_guid
                    self._applied_clip_echo_until = time.monotonic() + 3.0
                    self.plugin.connection.api.session.set_on_screen_source(playlist_xs_tl)
                    _log(
                        f"RECV selection: set_on_screen_source (sequence) → "
                        f"{getattr(playlist_xs_tl, 'name', '?')!r}"
                    )
                    # Defer the seek until Form-2 events have settled (~200 ms).
                    self._pending_seek_frame = start_frame
                    self._pending_seek_deadline = time.monotonic() + 0.300

                    # Also select/highlight the clip inside the timeline track,
                    # in place (guarded against the actor-teardown crash — see
                    # _highlight_timeline_item / design D2).
                    self._highlight_timeline_item(clip_guid)
                else:
                    # Flat playlist: viewed_container + set_on_screen_source + set_selection.
                    # Suppress the show_atom that fires from set_selection so it doesn't
                    # echo back to the peer that just sent us this selection.
                    self.plugin._selection_broadcast_suppress_until = time.monotonic() + 0.5
                    self._applied_clip_echo_guid = clip_guid
                    self._applied_clip_echo_until = time.monotonic() + 3.0
                    self.plugin.connection.api.session.set_on_screen_source(playlist)
                    media, _ = self.plugin.media.media_for_sync_guid(clip_guid)
                    if media:
                        playlist.playhead_selection.set_selection([media.uuid])
                        _log(
                            f"RECV selection: set_selection"
                            f" → {getattr(media, 'name', '?')!r} ({str(media.uuid)[:8]})"
                        )
                    else:
                        _log(f"RECV selection: media not found for clip {clip_guid[:8]}")

                # Ensure the active playhead's Pinned Source Mode matches the view_mode.
                self.check_and_update_active_playhead()
                if self.plugin.active_playhead:
                    try:
                        self.plugin._applying_pinned_mode = True
                        psm = (view_mode == "sequence")
                        self.plugin.active_playhead.set_attribute("Pinned Source Mode", psm)
                        self._last_pinned_source_mode = psm
                        _log(f"RECV selection: set Pinned Source Mode = {psm}")
                    except Exception:
                        _log_exc("RECV selection: failed to set Pinned Source Mode")
                    finally:
                        self.plugin._applying_pinned_mode = False
            except Exception:
                _log_exc("RECV selection: container switch or selection failed")

            # active_playhead is refreshed by Form-2 viewport_playhead_atom events
            # that fire as the source switch completes (~200 ms).  apply_pending_seek
            # then applies the deferred seek once the deadline passes.
            _log("RECV selection: source switch dispatched")
        else:
            _log("RECV selection: no playlist found for clip")

    # ── clip frame resolution ──────────────────────────────────────────

    def resolve_clip_at_frame(
        self,
        timeline: otio.schema.Timeline,
        frame: int,
    ) -> tuple:
        """
        Return ``(clip_guid, clip_local_time)`` for the media clip at *frame*.

        *frame* is 0-based (xStudio convention).  Returns ``(None, None)``
        when the frame cannot be resolved to any clip in the first content track.
        """
        fps = 24.0
        if self.plugin.active_playhead:
            fps = self.plugin.active_playhead.frame_rate.fps() or fps

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
            _log_exc("resolve_clip_at_frame error")
        return None, None
