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

class PlaybackSyncController:
    """Owns playback and selection sync state and methods.

    :param plugin: Back-reference to the parent ORISyncPlugin instance.
    """

    def __init__(self, plugin):
        self.plugin = plugin

        # ── owned state ───────────────────────────────────────────────
        self._pending_seek_frame: int | None = None
        self._pending_seek_deadline: float = 0.0
        self._current_selection_sub_id: int | None = None
        self._current_selection_container_uuid: str | None = None
        self._last_logged_container_uuid: str | None = None
        self._last_logged_clip_name: str | None = None
        self._last_viewed_clip_guid: str | None = None
        self._last_pinned_source_mode: bool | None = None
        self._last_remote_stop_at: float = 0.0
        self._last_sel_names = None
        self._last_src_names = None

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
        self._last_pinned_source_mode = None
        self._last_remote_stop_at = 0.0
        self._last_sel_names = None
        self._last_src_names = None

    # ── global playhead event ──────────────────────────────────────────

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
                clip_guid = self.plugin.media.sync_guid_for_xs_uuid(
                    media_uuid_str,
                    self.plugin.manager.active_timeline_guid if self.plugin.manager else None
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
                # When xStudio is already in single-clip mode (PSM=False), any
                # show_atom is a deliberate user click — use source mode regardless
                # of whether the media belongs to a sequence playlist.
                _in_single_clip = (self._last_pinned_source_mode is False)
                view_mode = "source" if _in_single_clip else ("sequence" if _is_seq_media else "source")
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
                    self.plugin.manager.broadcast_selection(clip_guid, view_mode=view_mode)
                    _log(f"[SEL] → broadcast clip {clip_guid[:8]} mode={view_mode}")
                return

            if time.monotonic() < self.plugin._reload_suppress_until:
                return
            _log(f"[SEL] show_atom (annotation/bookmark): {_shape} — queuing annotation flush")
            if self.plugin.manager and self.plugin.manager.status == STATE_SYNCED:
                self.plugin._annotation_pending_time = time.monotonic()
                # [2C] Hot scan is now activated by _on_core_annotation_event
                # (PaintStart/PaintPoint events from AnnotationsCore).  Keep this
                # as a fallback for builds that don't have the [2C] broadcast.
                if not self.plugin._hot_scan_active:
                    try:
                        if self.plugin.active_playhead:
                            self.plugin._hot_scan_frame = self.plugin.active_playhead.position
                            self.plugin._hot_scan_active = True
                            self.plugin._hot_scan_last_change = time.monotonic()
                            _log(f"[fallback] Hot scan activated at frame {self.plugin._hot_scan_frame} via show_atom")
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
        current_remote = getattr(self.plugin.active_playhead, "remote", None)
        if ph_remote != current_remote:
            try:
                self.plugin.active_playhead = Playhead(self.plugin.connection, ph_remote)
                _log(f"[SEL] viewport_playhead_atom Form-2: active playhead updated viewport={event[2]!r}")
                self.plugin.subscribe_to_playhead_events(ph_remote, self.plugin._on_position_event, auto_cancel=True)
                _log("[position_atom] subscribed to playhead events")
            except Exception:
                _log_exc("on_global_playhead_event: failed to update playhead and subscribe")

        # Subscribe to viewed container's selection actor events.
        try:
            container = self.get_viewed_container_safe()
            if container:
                self.subscribe_container_selection(container)
        except Exception:
            _log_exc("[SEL] Failed to subscribe to container selection events")

    # ── container selection subscription ──────────────────────────────

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
        self.check_and_update_active_playhead()
        self.enqueue_selection_update()

    def enqueue_selection_update(self) -> None:
        """Enqueue selection resolution to the poll thread command queue."""
        self.plugin._cmd_queue.put(("resolve_selection", None))

    # ── active playhead management ─────────────────────────────────────

    def check_and_update_active_playhead(self) -> None:
        """Query the active playhead from xStudio and subscribe to its events if changed."""
        try:
            ph = self.plugin.current_playhead()
        except Exception:
            return

        if ph:
            current_remote = getattr(self.plugin.active_playhead, "remote", None)
            if ph.remote != current_remote:
                self.plugin.active_playhead = ph
                try:
                    self.plugin.subscribe_to_playhead_events(
                        ph.remote, self.plugin._on_position_event, auto_cancel=True
                    )
                    _log(f"[position_atom] active playhead updated/subscribed: {ph.remote}")
                except Exception:
                    _log_exc("[position_atom] failed to subscribe to active playhead events")

    def _reacquire_active_playhead(self) -> None:
        """Re-acquire a live playhead after the previous reference went stale.

        Called from the poll thread when an actor call to ``active_playhead``
        timed out.  ``current_playhead()`` queries the global-playhead-events
        actor (not the dead playhead), so it is safe; it is still bounded by a
        timeout in case that path is also slow.  On success the new playhead is
        stored and re-subscribed for position events.
        """
        try:
            with bounded_timeout(self.plugin.connection, _PLAYHEAD_TIMEOUT_MS):
                ph = self.plugin.current_playhead()
                if ph:
                    self.plugin.subscribe_to_playhead_events(
                        ph.remote, self.plugin._on_position_event, auto_cancel=True
                    )
                    self.plugin.active_playhead = ph
                    _log(f"[position_atom] re-acquired live playhead: {ph.remote}")
        except Exception:
            _log_exc("_reacquire_active_playhead: failed")

    def on_position_event(self, event) -> None:
        """Fires when playhead position or play state changes."""
        if not (
            len(event) > 2
            and isinstance(event[0], event_atom)
        ):
            return

        is_pos = isinstance(event[1], position_atom)
        is_play = isinstance(event[1], play_forward_atom) or isinstance(event[1], play_atom)
        if not (is_pos or is_play):
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

        # Echo guard: check if this is a remote-applied frame/state change
        if frame == self.plugin._last_applied_frame:
            return

        # Initialize play state on first run
        if self.plugin._last_polled_playing is None:
            self.plugin._last_polled_playing = playing
            self.plugin._last_polled_frame = frame

        # Check playing state change
        playing_changed = (playing != self.plugin._last_polled_playing)

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

        # Construct playback state payload
        state = {
            "playing": playing,
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(frame),
                "rate": fps,
            },
            "looping": False,
        }

        # Enqueue the broadcast command to be processed asynchronously
        _log(
            f"Event: queuing playback state broadcast frame={frame} playing={playing}"
            f" (source_event={type(event[1]).__name__})"
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
                                self.plugin.manager.broadcast_selection("")
                                _log("[SEL] → broadcast selection clear (returned to sequence view)")
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
                                        self.plugin.manager.broadcast_selection(_cg, view_mode="source")
                                        _log(f"[SEL] PSM True→False: broadcast {_cg[:8]} mode=source")
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
                "looping": False,
            }
        except Exception:
            return None

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

    # ── apply remote playback state ───────────────────────────────────

    def apply_playback_state(self, state: dict) -> None:
        """Apply an incoming playback state dict to the local xStudio playhead.

        Called from the poll thread via the ``on_playback_changed`` callback.
        xStudio's actor-based attribute writes are thread-safe.

        Updates ``_last_applied_frame``, ``_last_polled_frame``, and
        ``_last_polled_playing`` so that the poll does not echo remote applies
        back to the session.
        """
        if not self.plugin.active_playhead:
            return

        incoming_tl_guid = state.get("timeline_guid")
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
                    _log(
                        f"RECV playback state: mismatched timeline_guid"
                        f" (local={local_tl_guid[:8]},"
                        f" target={self.plugin.manager.active_timeline_guid[:8]},"
                        f" incoming={incoming_tl_guid[:8]}) — ignoring"
                    )
                    return

        playing = state.get("playing", False)
        current_time = state.get("current_time", {})
        # Protocol value is 0-based (RV sends frame-1; xStudio frames are 0-based).
        frame = max(0, int(current_time.get("value", 0)))

        if not playing:
            self._last_remote_stop_at = time.monotonic()
        else:
            # Guard against rapid stop→start loop restarts (e.g. RV looping a
            # single-clip source group sends playing=False then playing=True within
            # milliseconds).  A genuine user press-play always follows a stop by
            # more than 300 ms.
            _loop_gap = time.monotonic() - self._last_remote_stop_at
            if _loop_gap < 0.3:
                _log(
                    f"RECV playback: ignoring rapid play-after-stop"
                    f" ({_loop_gap*1000:.0f} ms) — loop restart"
                )
                return

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
                playing_changed = (playing != ph.playing)
                if playing_changed:
                    # Update cache only when we actually change xStudio's play
                    # state so the poll does not mistake a no-op remote event
                    # for a local change.
                    self.plugin._last_polled_playing = playing
                    if playing:
                        self.plugin._playing_started_at = time.monotonic()
                    ph.playing = playing
                # Apply position if we are paused, or the play/pause state changed.
                if not playing or playing_changed:
                    self.plugin._last_applied_frame = frame
                    self.plugin._last_polled_frame = frame
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
            for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
                otio_tl = self.plugin.manager.timelines.get(tl_guid)
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
                    self.plugin.connection.api.session.set_on_screen_source(playlist_xs_tl)
                    _log(
                        f"RECV selection: set_on_screen_source (individual) → "
                        f"{getattr(playlist_xs_tl, 'name', '?')!r}"
                    )
                elif is_multi_clip:
                    # Multi-clip sequence: seek the playhead after the source switch
                    # to avoid invalid_request errors.
                    start_frame = 0
                    try:
                        start_frame = int(clip.range_in_parent().start_time.value)
                    except Exception:
                        # Fallback: Sum duration of all preceding items in the track
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

                    self.plugin.connection.api.session.set_on_screen_source(playlist_xs_tl)
                    _log(
                        f"RECV selection: set_on_screen_source (sequence) → "
                        f"{getattr(playlist_xs_tl, 'name', '?')!r}"
                    )

                    # Defer the seek until Form-2 events have settled the playhead (~200 ms).
                    self._pending_seek_frame = start_frame
                    self._pending_seek_deadline = time.monotonic() + 0.300

                    # Programmatically select/highlight the clip in the timeline track.
                    otio_tl = self.plugin.manager.timelines.get(matched_tl_guid) if self.plugin.manager else None
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
                                self.plugin.connection.send(
                                    playlist_xs_tl.remote, item_selection_atom(), ua_vec
                                )
                                _log(
                                    f"RECV selection: set timeline selection"
                                    f" to track={target_track_idx} child={target_child_idx}"
                                )
                            except Exception:
                                _log_exc("RECV selection: failed to set timeline item selection")
                else:
                    # Flat playlist: viewed_container + set_on_screen_source + set_selection.
                    # Suppress the show_atom that fires from set_selection so it doesn't
                    # echo back to the peer that just sent us this selection.
                    self.plugin._selection_broadcast_suppress_until = time.monotonic() + 0.5
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
