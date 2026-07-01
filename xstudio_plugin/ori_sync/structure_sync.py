#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""StructureSyncController — owns structural/playlist sync state and methods."""

import copy
import functools
import json
import os
import threading
import time
from collections import Counter
import opentimelineio as otio
from xstudio.api.session.playlist.timeline import Timeline
from xstudio.core import (
    event_atom, item_atom, media_content_changed_atom,
)
try:
    from xstudio.core import add_media_atom as _add_media_atom
except ImportError:
    _add_media_atom = None
from otio_sync_core.manager import STATE_SYNCED
from .utils import _log, _log_exc, _uri_to_posix_path


class StructureSyncController:
    """Owns playlist/timeline structural sync state and methods.

    :param plugin: Back-reference to the parent ORISyncPlugin instance.
    """

    def __init__(self, plugin):
        self.plugin = plugin

        # ── owned state ───────────────────────────────────────────────
        self._xs_flat_playlists: dict[str, tuple] = {}
        self._xs_sequence_playlists: dict[str, tuple] = {}
        self._xs_sequence_media_names: dict[str, set] = {}
        self._xs_sequence_track_names: dict[str, list | None] = {}
        self._xs_media_order: dict[str, list] = {}
        self._timeline_item_sub_ids: dict = {}
        self._sequence_playlist_sub_ids: dict = {}
        # Maps a host playlist's sync guid → the local xStudio playlist we
        # created for it, so multiple sequences sharing one parent playlist
        # land in the same bin instead of spawning a duplicate per sequence.
        self._parent_playlist_map: dict = {}
        # Sequence guids with a pending coalesced rebuild.  Multiple INSERT_CHILDs
        # for one timeline arrive together; we reload the xStudio sequence once
        # (full load_otio + source-switch is ~slow in xStudio) instead of per clip.
        self._pending_sequence_rebuilds: set = set()
        self._timeline_item_dirty: set = set()
        self._timeline_item_lock = threading.Lock()
        self._test_container_sub_id = None
        # uuid of the container currently subscribed for add_media events, so the
        # subscription can be re-established when the viewed container changes
        # (e.g. a peer that joined an empty session and only later views media).
        self._test_container_uuid: str | None = None
        self._pending_snapshot_requesters: list[str] = []
        self._last_structure_scan: float = 0.0

    def reset(self) -> None:
        """Clear all owned state (called from plugin disconnect)."""
        self._xs_flat_playlists.clear()
        self._xs_sequence_playlists.clear()
        self._xs_sequence_media_names.clear()
        self._xs_sequence_track_names.clear()
        self._xs_media_order.clear()
        self._timeline_item_sub_ids.clear()
        self._sequence_playlist_sub_ids.clear()
        self._parent_playlist_map.clear()
        self._pending_sequence_rebuilds.clear()
        with self._timeline_item_lock:
            self._timeline_item_dirty.clear()
        self._test_container_sub_id = None
        self._test_container_uuid = None
        self._pending_snapshot_requesters.clear()
        self._last_structure_scan = 0.0

    # ── timeline item event subscription ──────────────────────────────

    def subscribe_timeline_item_events(self, tl_guid: str, xs_tl) -> None:
        """Subscribe to *xs_tl*'s event group to receive item_atom notifications.

        Called whenever a new sequence Timeline is registered.  Stores the
        subscription ID in ``_timeline_item_sub_ids`` so duplicates are skipped.

        :param tl_guid: Sync GUID identifying the timeline in the manager.
        :param xs_tl: The xStudio Timeline object whose event group to join.
        """
        if tl_guid in self._timeline_item_sub_ids:
            return
        try:
            cb = functools.partial(self.on_timeline_item_event, tl_guid)
            sub_id = self.plugin.subscribe_to_event_group(xs_tl, cb)
            self._timeline_item_sub_ids[tl_guid] = sub_id
            _log(f"[2F] subscribed to item_atom events for timeline {tl_guid[:8]}")
        except Exception:
            _log_exc(f"[2F] subscribe_to_event_group failed for timeline {tl_guid[:8]}")

    def subscribe_sequence_playlist_events(self, tl_guid: str, xs_playlist) -> None:
        """No-op placeholder — see poll_sequence_new_media in the periodic poll.

        Subscribing to the playlist event group for ``add_media_atom`` caused a
        SIGSEGV in xStudio's pybind layer: when ``unsubscribe_from_event_group``
        was called during flat→sequence cleanup, xStudio had a pending callback
        delivery in flight for the same event group, producing a use-after-free.
        Polling is safer — ``poll_sequence_new_media`` runs every 1 s instead.
        """

    # ── viewed-container event ─────────────────────────────────────────

    def subscribe_viewed_container_events(self, container) -> None:
        """(Re)subscribe to *container*'s event group for add_media detection.

        The viewed container can change after connect — most importantly, a peer
        that joins an empty session has no container at connect time, then later
        views/creates one.  Without re-subscribing, that peer never detects media
        dragged into its own sequence.  Mirrors ``subscribe_container_selection``:
        guarded by uuid, unsubscribes the previous one first.  Safe to tear down
        now that the py_context broadcast_down_atom handler purges dead callbacks.
        """
        try:
            container_uuid = str(container.uuid)
        except Exception:
            return
        if container_uuid == self._test_container_uuid:
            return
        if self._test_container_sub_id is not None:
            try:
                self.plugin.unsubscribe_from_event_group(self._test_container_sub_id)
            except Exception:
                pass
            self._test_container_sub_id = None
        try:
            self._test_container_sub_id = self.plugin.subscribe_to_event_group(
                container, self.plugin._on_test_container_event
            )
            self._test_container_uuid = container_uuid
            _log(
                f"[2F] (re)subscribed to viewed-container events"
                f" (type={type(container).__name__} uuid={container_uuid[:8]})"
            )
        except Exception:
            _log_exc("[2F] subscribe_viewed_container_events failed")

    def on_test_container_event(self, event) -> None:
        """Handle events from the viewed container's event group.

        This single subscription is created once at connect time and is never
        torn down during a flat→sequence transition, which makes it the safe
        place to detect ``add_media_atom`` — the signal xStudio fires on the
        playlist when the user drags media into a sequence track.  (A dedicated
        per-sequence playlist subscription crashed xStudio: unsubscribing it
        during the flat→sequence actor swap delivered a broadcast_down_atom to
        a torn-down callback.)  We enqueue a one-shot sequence scan in response.
        """
        if time.monotonic() < self.plugin._structural_mutation_suppress_until:
            return
        if not (len(event) > 1 and isinstance(event[0], event_atom)):
            return
        # DIAGNOSTIC: log the atom type of every viewed-container event so we can
        # see what the (new) xStudio build fires when media is dragged into a
        # sequence — the add_media detection below may be keyed on the wrong atom.
        _t1 = type(event[1]).__name__ if len(event) > 1 else "n/a"
        _log(f"[2F-DIAG] viewed-container event: t1={_t1} len={len(event)}")
        is_add_media = _add_media_atom is not None and isinstance(event[1], _add_media_atom)
        is_media_change = isinstance(event[1], media_content_changed_atom)
        if not (is_add_media or is_media_change):
            return
        _log("[2F] viewed-container add_media/media_changed — queuing sequence scan")
        self.plugin._cmd_queue.put(("sync_sequences", None))

    # ── timeline item event ────────────────────────────────────────────

    def on_timeline_item_event(self, tl_guid: str, event) -> None:
        """Handle item_atom or media_content_changed_atom events from a tracked Timeline or Playlist event group.

        Parses the event to detect mutations and enqueues a structural synchronization command.

        :param tl_guid: Sync GUID of the timeline/container that fired the event.
        :param event: Event tuple from xStudio's CAF message bus.
        """
        if time.monotonic() < self.plugin._structural_mutation_suppress_until:
            return

        # DIAGNOSTIC: log every timeline event's shape so we can see what the
        # (new) xStudio build fires when media is dragged into a sequence track —
        # the item_atom/action filtering below may be missing the new signal.
        _t1 = type(event[1]).__name__ if len(event) > 1 else "n/a"
        _log(f"[2F-DIAG] timeline event tl={tl_guid[:8]} t1={_t1} len={len(event)}")

        if not (len(event) > 2 and isinstance(event[0], event_atom)):
            return

        is_item = isinstance(event[1], item_atom)
        is_media_change = isinstance(event[1], media_content_changed_atom)
        if not (is_item or is_media_change):
            return

        if is_media_change:
            _log(
                f"[2F] media_content_changed_atom fired for playlist"
                f" {tl_guid[:8]} — queuing sync_container"
            )
            self.plugin._cmd_queue.put(("sync_container", {"tl_guid": tl_guid}))
            return

        hidden = event[3] if len(event) > 3 else False
        if hidden:
            return

        changes = event[2]
        if hasattr(changes, "dump"):
            try:
                changes = json.loads(changes.dump())
            except Exception:
                changes = {}

        actions = []
        if isinstance(changes, dict):
            actions.append(changes.get("action"))
        elif isinstance(changes, list):
            for c in changes:
                if isinstance(c, dict):
                    actions.append(c.get("action"))

        # IA_INSERT=6, IA_REMOVE=7, IA_SPLICE=8, IA_NAME=9, IA_DIRTY=20
        _log(f"[2F-DIAG] timeline item_atom tl={tl_guid[:8]} actions={actions}")
        matching_actions = [a for a in actions if a in (6, 7, 8, 9, 20)]
        # Trim/source_range edits fire item_atom with action=None (unrecognised code).
        # For sequence timelines any non-empty item event is potentially structural.
        is_sequence = tl_guid in self._xs_sequence_playlists
        if not matching_actions:
            if not (is_sequence and actions):
                return
        action = matching_actions[0] if matching_actions else None
        _log(
            f"[2F] item_atom fired for timeline {tl_guid[:8]} with action {action}"
            f" — queuing sync_container"
        )
        self.plugin._cmd_queue.put(("sync_container", {"tl_guid": tl_guid}))

    # ── execute sync container ─────────────────────────────────────────

    def execute_sync_container(self, tl_guid: str) -> None:
        """Process event-driven structural updates for a given timeline/container.

        :param tl_guid: Sync GUID of the timeline/container to sync.
        """
        if not tl_guid:
            return
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return

        if tl_guid in self._xs_sequence_playlists:
            _log(f"[2F] Executing sequence sync_container for {tl_guid[:8]}")
            self.poll_sequence_new_media(only_guid=tl_guid)
            self.poll_sequence_track_deletions(only_guid=tl_guid)
            self.poll_sequence_reorders(only_guid=tl_guid)
            self.poll_sequence_source_ranges(only_guid=tl_guid)
        elif tl_guid in self._xs_flat_playlists:
            _log(f"[2F] Executing flat playlist sync_container for {tl_guid[:8]}")
            self.poll_flat_playlist_new_media(only_guid=tl_guid)
            self.poll_flat_playlist_reorders(only_guid=tl_guid)
            self.poll_new_playlists()
            self.poll_playlist_renames()

    # ── media order ────────────────────────────────────────────────────

    def update_xs_media_order(self, tl_guid: str, otio_tl: "otio.schema.Timeline") -> None:
        """Update _xs_media_order for a sequence timeline from its OTIO representation."""
        media_track = next(
            (t for t in otio_tl.tracks
             if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
            next(
                (t for t in otio_tl.tracks
                 if t.kind == otio.schema.TrackKind.Video),
                None,
            ),
        )
        if media_track is not None:
            self._xs_media_order[tl_guid] = [
                c.metadata.get("sync", {}).get("guid")
                for c in media_track
                if isinstance(c, otio.schema.Clip)
            ]

    # ── flat playlist polls ────────────────────────────────────────────

    def poll_flat_playlist_reorders(self, only_guid: str | None = None) -> None:
        """Detect and broadcast clip reorders in flat (media-bin) Playlists.

        Only runs on the master.  For each flat Playlist registered in
        ``_xs_flat_playlists``, reads the current ``playlist.media`` order from
        xStudio and compares it to the stored name list.  When a difference is
        found the clip at the first mismatched position is moved via
        ``broadcast_move_child``.

        :param only_guid: When given, only checks the timeline with this sync GUID.
        """
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return

        for tl_guid, (xs_playlist, stored_order) in list(self._xs_flat_playlists.items()):
            if only_guid is not None and tl_guid != only_guid:
                continue
            try:
                current_media = xs_playlist.media
                current_order = [
                    self.plugin.media.sync_guid_for_xs_uuid(str(m.uuid), tl_guid) or str(m.uuid)
                    for m in current_media
                ]
            except Exception:
                continue

            if current_order == stored_order:
                continue

            otio_tl = self.plugin.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue

            video_track = next(
                (t for t in otio_tl.tracks if t.kind == otio.schema.TrackKind.Video),
                None,
            )
            if video_track is None:
                continue

            track_guid = video_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                continue

            track_clip_guids = {
                clip.metadata["sync"]["guid"]
                for clip in video_track
                if isinstance(clip, otio.schema.Clip)
                and "sync" in clip.metadata
                and "guid" in clip.metadata["sync"]
            }

            # Simulate the moves to transform stored_order into current_order,
            # broadcasting each MOVE_CHILD so the remote peer receives the full sequence.
            temp_order = list(stored_order)
            while temp_order != current_order:
                found = False
                for new_idx, guid in enumerate(current_order):
                    if new_idx >= len(temp_order) or temp_order[new_idx] != guid:
                        if guid in track_clip_guids:
                            self.plugin.manager.broadcast_move_child(track_guid, guid, new_idx)
                            _log(f"Flat playlist reorder: guid {guid[:8]} → index {new_idx}")
                        if guid in temp_order:
                            temp_order.remove(guid)
                            temp_order.insert(new_idx, guid)
                        found = True
                        break
                if not found:
                    break
            self._xs_flat_playlists[tl_guid] = (xs_playlist, list(current_order))

    def poll_sequence_reorders(self, only_guid: str | None = None) -> None:
        """Detect and broadcast clip reorders in sequence Timelines.

        Only runs on the master.  For each sequence Timeline registered in
        ``_xs_sequence_playlists``, reads the current timeline order from
        xStudio by re-serialising it to OTIO, maps each clip to its stable
        sync GUID, and compares it to ``self._xs_media_order``.

        :param only_guid: When given, only checks the timeline with this sync GUID.
        """
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return

        items = list(self._xs_sequence_playlists.items())
        if only_guid is not None:
            items = [(g, v) for g, v in items if g == only_guid]

        for tl_guid, tl_entry in items:
            xs_tl = tl_entry[1]
            if xs_tl is None:
                continue
            otio_tl = self.plugin.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue
            video_track = next(
                (t for t in otio_tl.tracks
                 if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                next(
                    (t for t in otio_tl.tracks
                     if t.kind == otio.schema.TrackKind.Video),
                    None,
                ),
            )
            if video_track is None:
                continue
            track_guid = video_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                continue

            try:
                xs_otio_str = xs_tl.to_otio_string()
                xs_tl_parsed = otio.adapters.read_from_string(xs_otio_str, "otio_json")
                xs_video_track = next(
                    (t for t in xs_tl_parsed.tracks
                     if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                    next(
                        (t for t in xs_tl_parsed.tracks
                         if t.kind == otio.schema.TrackKind.Video),
                        None,
                    ),
                )
                xs_clips = (
                    [c for c in xs_video_track if isinstance(c, otio.schema.Clip)]
                    if xs_video_track is not None else []
                )
            except Exception:
                continue

            # Greedy match current clips to stable manager clip GUIDs
            manager_clips = [c for c in video_track if isinstance(c, otio.schema.Clip)]
            pool = list(manager_clips)
            current_order = []

            for clip in xs_clips:
                clip_guid = None
                clip_url = ""
                if isinstance(clip.media_reference, otio.schema.ExternalReference):
                    clip_url = clip.media_reference.target_url or ""
                clip_path = _uri_to_posix_path(clip_url)
                norm_clip_path = os.path.normpath(clip_path) if clip_path else ""
                clip_stem = (
                    os.path.splitext(os.path.basename(clip_path))[0].lower()
                    if clip_path else ""
                )

                matched_mc = None
                for mc in pool:
                    mc_url = ""
                    if isinstance(mc.media_reference, otio.schema.ExternalReference):
                        mc_url = mc.media_reference.target_url or ""
                    mc_path = _uri_to_posix_path(mc_url)
                    norm_mc_path = os.path.normpath(mc_path) if mc_path else ""
                    if norm_clip_path and norm_clip_path == norm_mc_path:
                        matched_mc = mc
                        break
                    mc_stem = (
                        os.path.splitext(os.path.basename(mc_path))[0].lower()
                        if mc_path else ""
                    )
                    if clip_stem and clip_stem == mc_stem:
                        matched_mc = mc
                        break

                if matched_mc is not None:
                    clip_guid = matched_mc.metadata.get("sync", {}).get("guid")
                    pool.remove(matched_mc)

                if clip_guid:
                    current_order.append(clip_guid)

            stored_order = self._xs_media_order.get(tl_guid)
            if stored_order is None:
                self._xs_media_order[tl_guid] = list(current_order)
                continue

            if current_order == stored_order:
                continue

            if len(current_order) != len(stored_order):
                self._xs_media_order[tl_guid] = list(current_order)
                continue

            if set(current_order) != set(stored_order):
                # Different clip GUIDs (not just different order) — the manager
                # OTIO was replaced since the baseline was set.  Reset the
                # baseline instead of broadcasting a bogus reorder.
                self._xs_media_order[tl_guid] = list(current_order)
                continue

            # Simulate the moves to transform stored_order into current_order,
            # broadcasting each MOVE_CHILD so the remote peer receives the full sequence.
            temp_order = list(stored_order)
            while temp_order != current_order:
                found = False
                for new_idx, guid in enumerate(current_order):
                    if new_idx >= len(temp_order) or temp_order[new_idx] != guid:
                        self.plugin.manager.broadcast_move_child(track_guid, guid, new_idx)
                        _log(f"Sequence timeline reorder: guid {guid[:8]} → index {new_idx}")
                        if guid in temp_order:
                            temp_order.remove(guid)
                            temp_order.insert(new_idx, guid)
                        found = True
                        break
                if not found:
                    break
            self._xs_media_order[tl_guid] = list(current_order)

    def poll_flat_playlist_new_media(self, only_guid: str | None = None) -> None:
        """Detect and broadcast media additions and deletions in flat Playlists.

        Runs on both master and client.  Compares the current media list
        against the stored order; broadcasts INSERT_CHILD for additions and
        REMOVE_CHILD for deletions so all peers stay in sync.

        :param only_guid: When given, only checks the timeline with this sync GUID.
        """
        if not self.plugin.manager:
            return

        for tl_guid, (xs_playlist, stored_order) in list(self._xs_flat_playlists.items()):
            if only_guid is not None and tl_guid != only_guid:
                continue
            try:
                current_media = xs_playlist.media
            except Exception:
                continue

            current_order = [
                self.plugin.media.sync_guid_for_xs_uuid(str(m.uuid), tl_guid) or str(m.uuid)
                for m in current_media
            ]
            if current_order == stored_order:
                continue

            if set(current_order) == set(stored_order):
                # Pure reorder: do not update the cached stored_order so that
                # poll_flat_playlist_reorders has a chance to detect and broadcast it.
                continue

            otio_tl = self.plugin.manager.timelines.get(tl_guid)
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
            current_names = set(current_order)

            # Broadcast removals first so the OTIO track stays consistent when
            # inserts arrive immediately after (e.g. replace = delete + add).
            removed_names = stored_names - current_names
            if removed_names:
                for clip in list(video_track):
                    if isinstance(clip, otio.schema.Clip):
                        cg = clip.metadata.get("sync", {}).get("guid")
                        if cg in removed_names:
                            self.plugin.media.evict(cg, tl_guid)
                            self.plugin.media._flat_clip_to_media.pop(cg, None)
                            self.plugin.manager.broadcast_remove_child(track_guid, cg)
                            _log(f"flat playlist deleted media: {clip.name!r} removed")

            # Broadcast additions.
            for new_idx, media in enumerate(current_media):
                guid = (
                    self.plugin.media.sync_guid_for_xs_uuid(str(media.uuid), tl_guid)
                    or str(media.uuid)
                )
                if guid in stored_names:
                    continue
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
                    self.plugin.manager._ensure_guid_and_map(clip)
                    clip_guid = clip.metadata.get("sync", {}).get("guid")
                    if clip_guid:
                        self.plugin.media._flat_clip_to_media[clip_guid] = media
                        self.plugin.media.register(media, clip_guid, tl_guid)
                    self.plugin.manager.insert_child(track_guid, clip, new_idx)
                    _log(f"flat playlist new media: {media.name!r} inserted at {new_idx}")
                except Exception:
                    _log_exc(f"flat playlist new media: failed for {media.name!r}")

            # Re-evaluate current_order using updated mapping to ensure sync GUIDs are cached
            current_order = [
                self.plugin.media.sync_guid_for_xs_uuid(str(m.uuid), tl_guid) or str(m.uuid)
                for m in current_media
            ]
            self._xs_flat_playlists[tl_guid] = (xs_playlist, current_order)

    # ── new playlists and renames ──────────────────────────────────────

    def poll_new_playlists(self) -> None:
        """Detect newly created playlists or timelines and broadcast them.

        Runs on any synced peer (not just the master).  Scans
        ``session.playlists`` for containers not yet in ``_sync_playlists``
        and broadcasts each new one via ``broadcast_add_timeline``.
        Sequence (Timeline-backed) and flat (media-bin) playlists are both
        handled.
        """
        if not self.plugin.manager:
            return
        if self.plugin.manager.status != STATE_SYNCED:
            return
        try:
            playlists = self.plugin.connection.api.session.playlists
        except Exception:
            return

        known_pl_uuids: set[str] = set()
        for pl, _ in self.plugin._sync_playlists.values():
            try:
                known_pl_uuids.add(str(pl.uuid))
            except Exception:
                pass

        for playlist in playlists:
            try:
                pl_uuid = str(playlist.uuid)
            except Exception:
                continue

            try:
                containers = playlist.containers
            except Exception:
                _log_exc(
                    f"poll_new_playlists: cannot get containers for"
                    f" {getattr(playlist, 'name', '?')!r}"
                )
                continue

            timelines = [c for c in containers if isinstance(c, Timeline)]
            if timelines:
                # If this playlist was previously registered as flat, clean up the flat entry
                if pl_uuid in self._xs_flat_playlists:
                    _log(
                        f"Playlist {playlist.name!r} ({pl_uuid[:8]}) transitioned"
                        f" from flat to sequence. Cleaning up flat entry."
                    )
                    self._xs_flat_playlists.pop(pl_uuid, None)
                    self.plugin._sync_playlists.pop(pl_uuid, None)
                    sub_id = self._timeline_item_sub_ids.pop(pl_uuid, None)
                    if sub_id:
                        try:
                            self.plugin.unsubscribe_from_event_group(sub_id)
                        except Exception:
                            pass
                    try:
                        self.plugin.manager.broadcast_remove_timeline(pl_uuid)
                    except Exception:
                        pass

                # xStudio timeline actors already synced under a sync guid —
                # i.e. created from a remote snapshot/ADD_TIMELINE, which stores
                # them as the *value* in _sync_playlists keyed by the sync guid
                # (e.g. 19458ef9), not by their native xStudio uuid (e.g.
                # 8391ed30). A key-only check below misses them and would
                # re-broadcast a phantom duplicate timeline under the native uuid.
                synced_xs_uuids = {
                    str(_t.uuid)
                    for _pl, _t in self.plugin._sync_playlists.values()
                    if _t is not None
                }
                for xs_tl in timelines:
                    tl_guid = str(xs_tl.uuid)
                    if tl_guid in self.plugin._sync_playlists or tl_guid in synced_xs_uuids:
                        continue
                    tl = self.plugin.builder.build_single_sequence_otio(playlist, xs_tl)
                    if tl is None:
                        continue

                    # Stamp the playlist bin URIs so peers can populate their
                    # bin with the same media when they receive ADD_TIMELINE.
                    try:
                        _bin_uris = []
                        for _m in playlist.media:
                            try:
                                _uri = str(_m.media_source().media_reference.uri())
                                if _uri:
                                    _bin_uris.append(_uri)
                            except Exception:
                                pass
                        if _bin_uris:
                            tl.metadata["xs_bin_media"] = _bin_uris
                    except Exception:
                        pass

                    self.plugin.media.bootstrap_mapping(playlist, tl, xs_tl)
                    self.plugin.manager.register_timeline(tl)
                    self._xs_sequence_track_names[tl_guid] = None

                    _media_tr_np = next(
                        (t for t in tl.tracks
                         if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                        next(
                            (t for t in tl.tracks
                             if t.kind == otio.schema.TrackKind.Video),
                            None,
                        ),
                    )
                    _known_np = {
                        c.name for c in (_media_tr_np or [])
                        if isinstance(c, otio.schema.Clip)
                    }
                    try:
                        _known_np |= {m.name for m in playlist.media}
                    except Exception:
                        pass
                    self._xs_sequence_playlists[tl_guid] = (playlist, xs_tl, _known_np)
                    self.plugin._sync_playlists[tl_guid] = (playlist, xs_tl)
                    try:
                        self._xs_sequence_media_names[tl_guid] = {m.name for m in playlist.media}
                    except Exception:
                        self._xs_sequence_media_names[tl_guid] = set()
                    self.subscribe_timeline_item_events(tl_guid, xs_tl)
                    self.subscribe_sequence_playlist_events(tl_guid, playlist)
                    self.plugin.manager.broadcast_add_timeline(tl_guid)
                    _log(
                        f"New sequence timeline {xs_tl.name!r}"
                        f" (playlist={playlist.name!r}) → broadcast"
                    )
            else:
                if pl_uuid in known_pl_uuids:
                    continue
                tl = self.plugin.builder.build_otio_from_playlist_media(playlist)
                if tl is None:
                    continue
                tl_guid = tl.metadata.get("sync", {}).get("guid", "")
                if not tl_guid:
                    continue
                self.plugin.manager.register_timeline(tl)
                self.plugin.manager.broadcast_add_timeline(tl_guid)
                _log(f"New flat playlist {playlist.name!r} → broadcast")

    def poll_playlist_renames(self) -> None:
        """Detect and broadcast playlist or timeline name changes.

        Runs on any synced peer (not just the master).  Compares the current
        xStudio name against the OTIO timeline name stored in the manager for
        each tracked playlist.
        """
        if not self.plugin.manager:
            return
        if self.plugin.manager.status != STATE_SYNCED:
            return
        for tl_guid, (pl, xs_tl) in list(self.plugin._sync_playlists.items()):
            otio_tl = self.plugin.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue
            try:
                current_name = xs_tl.name if xs_tl is not None else pl.name
            except Exception:
                continue
            if current_name and current_name != (otio_tl.name or ""):
                _log(
                    f"Timeline rename: {otio_tl.name!r} → {current_name!r}"
                    f" ({tl_guid[:8]})"
                )
                self.plugin.manager.broadcast_timeline_rename(tl_guid, current_name)

    def _purge_local_playlist_entry(self, tl_guid: str) -> None:
        """Drop all local tracking state for *tl_guid* and unsubscribe its events.

        Shared by :meth:`delete_local_container` (inbound removal) and
        :meth:`poll_deleted_playlists` (local deletion). Touches only tracking
        dicts and subscriptions — never the xStudio container itself.
        """
        self.plugin._sync_playlists.pop(tl_guid, None)
        self._xs_sequence_playlists.pop(tl_guid, None)
        self._xs_flat_playlists.pop(tl_guid, None)
        self._xs_sequence_media_names.pop(tl_guid, None)
        self._xs_sequence_track_names.pop(tl_guid, None)
        self._xs_media_order.pop(tl_guid, None)
        sub_id = self._timeline_item_sub_ids.pop(tl_guid, None)
        if sub_id:
            try:
                self.plugin.unsubscribe_from_event_group(sub_id)
            except Exception:
                pass
        pl_sub_id = self._sequence_playlist_sub_ids.pop(tl_guid, None)
        if pl_sub_id:
            try:
                self.plugin.unsubscribe_from_event_group(pl_sub_id)
            except Exception:
                pass

    def delete_local_container(self, tl_guid: str) -> None:
        """Remove the xStudio container for a remotely-removed timeline.

        Symmetric to the ``add_timeline`` create path. No-op when no local
        container maps to *tl_guid*. Runs on the poll thread (enqueued as the
        ``remove_timeline`` command) so the xStudio session mutation is
        thread-safe.
        """
        entry = self.plugin._sync_playlists.get(tl_guid)
        if entry is None:
            _log(f"RECV remove_timeline: no container for {tl_guid[:8]} (no-op)")
            return
        playlist, _xs_tl = entry
        # Suppress our own structural-event echo: remove_container fires xStudio
        # item/content events that on_timeline_item_event would otherwise turn
        # into a sync_container scan, re-detecting the sequence and re-broadcasting
        # it as a brand-new timeline — resurrecting the delete. Every other
        # remote-apply path sets this guard for the same reason.
        self.plugin._structural_mutation_suppress_until = time.monotonic() + 1.5
        # remove_container keys on the *container* uuid (create_playlist's first
        # return value), NOT the Playlist actor's uuid. We only stored the
        # Playlist object, so passing it removes nothing (wrong uuid) and the
        # periodic poll then re-detects the still-present playlist. Resolve the
        # container uuid from the session tree before removing.
        container_uuid = self._container_uuid_for_playlist(playlist)
        try:
            target = container_uuid if container_uuid is not None else playlist
            self.plugin.connection.api.session.remove_container(target)
            _log(
                f"RECV remove_timeline: removed container for {tl_guid[:8]}"
                + ("" if container_uuid is not None else " (actor-uuid fallback)")
            )
        except Exception:
            _log_exc(f"delete_local_container: remove_container failed for {tl_guid[:8]}")
        self._purge_local_playlist_entry(tl_guid)

    def _container_uuid_for_playlist(self, playlist):
        """Resolve the session-tree container uuid for a Playlist actor.

        ``remove_container`` removes by the container uuid that
        ``create_playlist`` returns first; ``Playlist.uuid`` is the actor uuid,
        which is different. Match the playlist's actor uuid against each
        container node's ``value_uuid`` in ``session.playlist_tree``.

        :param playlist: The xStudio ``Playlist`` whose container to find.
        :returns: The container ``Uuid``, or ``None`` if it cannot be resolved.
        """
        try:
            actor_uuid = str(playlist.uuid)
        except Exception:
            return None
        # Top-level container nodes expose ``value_uuid`` (the actor uuid) and
        # ``uuid`` (the container uuid); they are ``PlaylistItemTree`` and have
        # no ``children`` (only the root ``PlaylistTree`` does), so this is a
        # single-level scan — sync-created playlists are top-level containers.
        try:
            for node in self.plugin.connection.api.session.playlist_tree.children:
                try:
                    if str(node.value_uuid) == actor_uuid:
                        return node.uuid
                except Exception:
                    continue
        except Exception:
            _log_exc("_container_uuid_for_playlist: failed to walk playlist_tree")
        return None

    def poll_deleted_playlists(self) -> None:
        """Detect synced playlists/timelines deleted in xStudio and broadcast removal.

        Counterpart to :meth:`poll_new_playlists`. Enumerates the live
        ``session.playlists`` to learn which container identities still exist,
        then broadcasts ``REMOVE_TIMELINE`` for any tracked entry whose native
        identity is gone. Liveness is judged from the live enumeration (not by
        poking the stored, possibly-dead actor) so a deleted playlist's actor
        read cannot freeze the poll thread.
        """
        if not self.plugin.manager:
            return
        if self.plugin.manager.status != STATE_SYNCED:
            return
        try:
            playlists = self.plugin.connection.api.session.playlists
        except Exception:
            return

        live_uuids: set[str] = set()
        enumerated = False
        for playlist in playlists:
            try:
                live_uuids.add(str(playlist.uuid))
                for c in playlist.containers:
                    if isinstance(c, Timeline):
                        live_uuids.add(str(c.uuid))
                enumerated = True
            except Exception:
                continue
        # Guard against a transient empty/failed scan wiping every timeline.
        if not enumerated:
            return

        for tl_guid, (pl, xs_tl) in list(self.plugin._sync_playlists.items()):
            try:
                native = str(xs_tl.uuid) if xs_tl is not None else str(pl.uuid)
            except Exception:
                native = None
            if native is not None and native in live_uuids:
                continue  # still present
            self._purge_local_playlist_entry(tl_guid)
            try:
                self.plugin.manager.broadcast_remove_timeline(tl_guid)
            except Exception:
                _log_exc(f"poll_deleted_playlists: broadcast failed for {tl_guid[:8]}")
            _log(
                f"Playlist/timeline {tl_guid[:8]} deleted in xStudio "
                f"→ removal broadcast"
            )

    # ── sequence media polls ───────────────────────────────────────────

    def poll_sequence_new_media(self, only_guid: str | None = None) -> None:
        """Detect and broadcast clips added to sequence Timelines.

        Iterates ``playlist.media`` (same approach as flat playlists) instead
        of calling ``to_otio_string()``, which returns MissingReference for
        client-side timelines loaded via ``load_otio()``.

        :param only_guid: When given, only checks the timeline with this sync
            GUID (used by the event-driven path to avoid re-scanning all
            timelines on every item_atom event).
        """
        if not self.plugin.manager:
            return

        items = list(self._xs_sequence_playlists.items())
        if only_guid is not None:
            items = [(g, v) for g, v in items if g == only_guid]

        for tl_guid, (xs_playlist, xs_tl, known_names) in items:
            _log(f"[2F] poll_sequence_new_media: tl={tl_guid[:8]} known={len(known_names)}")
            otio_tl = self.plugin.manager.timelines.get(tl_guid)
            if otio_tl is None:
                _log(f"[2F] poll_sequence_new_media: tl={tl_guid[:8]} not in manager — skip")
                continue
            video_track = next(
                (t for t in otio_tl.tracks
                 if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                next(
                    (t for t in otio_tl.tracks
                     if t.kind == otio.schema.TrackKind.Video),
                    None,
                ),
            )
            if video_track is None:
                continue
            track_guid = video_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                continue

            try:
                current_media = xs_playlist.media
            except Exception:
                continue

            current_media_name_set = {m.name for m in current_media}

            # --- Deletions: broadcast REMOVE_CHILD for media removed from the bin ---
            prev_media_names = self._xs_sequence_media_names.get(tl_guid, set())
            removed_media_names = prev_media_names - current_media_name_set
            if removed_media_names:
                removed_basenames = {os.path.basename(n) for n in removed_media_names}
                for clip in list(video_track):
                    if not isinstance(clip, otio.schema.Clip):
                        continue
                    if clip.name in removed_media_names or clip.name in removed_basenames:
                        child_guid = clip.metadata.get("sync", {}).get("guid")
                        if child_guid:
                            self.plugin.manager.broadcast_remove_child(track_guid, child_guid)
                            _log(f"sequence deleted media: {clip.name!r} removed")
                            known_names = known_names - {clip.name}
            self._xs_sequence_media_names[tl_guid] = current_media_name_set

            # --- Additions (from media bin) ---
            for media in current_media:
                if media.name in known_names:
                    continue
                # Also check basename so full-path entries for known clips are skipped.
                _bn = os.path.basename(media.name)
                if _bn in known_names:
                    known_names = known_names | {media.name}
                    continue
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
                            name=_bn,
                            media_reference=otio.schema.ExternalReference(
                                target_url=uri, available_range=sr
                            ),
                            source_range=sr,
                        )
                    else:
                        clip = otio.schema.Clip(
                            name=_bn,
                            media_reference=otio.schema.ExternalReference(target_url=uri),
                        )
                    new_index = len([c for c in video_track if isinstance(c, otio.schema.Clip)])
                    self.plugin.manager._ensure_guid_and_map(clip)
                    clip_guid = clip.metadata.get("sync", {}).get("guid")
                    if clip_guid:
                        self.plugin.media.register(media, clip_guid, tl_guid)
                    self.plugin.manager.insert_child(track_guid, clip, new_index)
                    _log(f"sequence new media: {_bn!r} at index {new_index}")
                    known_names = known_names | {media.name, _bn}
                except Exception:
                    _log_exc(f"sequence new media: failed for {media.name!r}")

            # --- Additions (direct track dragging) ---
            _log(
                f"[2F] track path entry: tl={tl_guid[:8]}"
                f" manager_clips={len([c for c in video_track if isinstance(c, otio.schema.Clip)])}"
                f" bin_media={len(list(current_media))}"
            )
            try:
                xs_otio_str = xs_tl.to_otio_string()
                xs_tl_parsed = otio.adapters.read_from_string(xs_otio_str, "otio_json")
                _log(
                    f"[2F] track path: tl={tl_guid[:8]} tracks={len(list(xs_tl_parsed.tracks))}"
                    f" track_names={[t.name for t in xs_tl_parsed.tracks]}"
                )
                # Collect clips from ALL video-kind tracks except Annotations.
                # xStudio places drag-dropped media in a new 'Dropped' track
                # rather than the existing 'Video Track', so we must scan all.
                xs_clips_ordered: list = []
                for _trk in xs_tl_parsed.tracks:
                    if _trk.kind != otio.schema.TrackKind.Video:
                        continue
                    if _trk.name == "Annotations":
                        continue
                    xs_clips_ordered.extend(
                        c for c in _trk if isinstance(c, otio.schema.Clip)
                    )
                xs_clips = xs_clips_ordered
                _log(
                    f"[2F] track path: xs_clips={len(xs_clips)}"
                )
            except Exception:
                xs_clips = []
                _log_exc(f"Failed to read track clips for {tl_guid[:8]}")

            manager_clips = [c for c in video_track if isinstance(c, otio.schema.Clip)]
            pool = list(manager_clips)

            for new_idx, clip in enumerate(xs_clips):
                # Try to find a match in the manager clips pool
                clip_url = ""
                if isinstance(clip.media_reference, otio.schema.ExternalReference):
                    clip_url = clip.media_reference.target_url or ""
                clip_path = _uri_to_posix_path(clip_url)
                norm_clip_path = os.path.normpath(clip_path) if clip_path else ""
                clip_stem = (
                    os.path.splitext(os.path.basename(clip_path))[0].lower()
                    if clip_path else ""
                )

                matched_mc = None
                for mc in pool:
                    mc_url = ""
                    if isinstance(mc.media_reference, otio.schema.ExternalReference):
                        mc_url = mc.media_reference.target_url or ""
                    mc_path = _uri_to_posix_path(mc_url)
                    norm_mc_path = os.path.normpath(mc_path) if mc_path else ""
                    if norm_clip_path and norm_clip_path == norm_mc_path:
                        matched_mc = mc
                        break
                    mc_stem = (
                        os.path.splitext(os.path.basename(mc_path))[0].lower()
                        if mc_path else ""
                    )
                    if clip_stem and clip_stem == mc_stem:
                        matched_mc = mc
                        break

                if matched_mc is not None:
                    pool.remove(matched_mc)
                else:
                    # No match found in current manager clips -> this is a new track clip addition!
                    try:
                        # The clip came from xs_tl_parsed (via a 'Dropped' or 'Video Track'
                        # inside that parsed tree) and still has that track as its parent.
                        # OTIO refuses to reparent a child that already has one, so we
                        # deepcopy to get a detached clone before stamping and inserting.
                        clip = copy.deepcopy(clip)
                        # Clips dragged into a track come back from to_otio_string()
                        # with an empty name — the name lives on the media reference,
                        # not the clip.  Derive it from the media path so peers show
                        # a proper clip name instead of a blank.
                        if not clip.name and clip_path:
                            clip.name = os.path.splitext(os.path.basename(clip_path))[0]
                        self.plugin.manager._ensure_guid_and_map(clip)
                        clip_guid = clip.metadata.get("sync", {}).get("guid")

                        # Register in media mapping if we can find a matching Media object in current_media
                        matched_media = None
                        for media in current_media:
                            if media.name == clip.name or os.path.basename(media.name) == clip.name:
                                matched_media = media
                                break
                            try:
                                m_uri = str(media.media_source().media_reference.uri())
                                m_path = _uri_to_posix_path(m_uri)
                                if m_path and norm_clip_path == os.path.normpath(m_path):
                                    matched_media = media
                                    break
                            except Exception:
                                pass

                        if matched_media and clip_guid:
                            self.plugin.media.register(matched_media, clip_guid, tl_guid)

                        self.plugin.manager.insert_child(track_guid, clip, new_idx)
                        _log(
                            f"sequence track new media: {clip.name!r} inserted at index {new_idx}"
                        )
                        known_names = known_names | {clip.name}
                        if matched_media:
                            known_names = known_names | {matched_media.name}
                    except Exception:
                        _log_exc(
                            f"sequence track new media: failed to insert {clip.name!r}"
                        )

            self._xs_sequence_playlists[tl_guid] = (xs_playlist, xs_tl, known_names)

    def poll_sequence_track_deletions(self, only_guid: str | None = None) -> None:
        """Detect clips removed from xStudio sequence Timeline tracks and broadcast REMOVE_CHILD.

        Removing a clip from an xStudio Timeline track does NOT remove the media
        from the playlist bin, so poll_sequence_new_media (which watches the bin)
        misses these deletions.  This method compares the live xStudio track clip
        names against the OTIO manager track and broadcasts REMOVE_CHILD for any
        clip that has disappeared from xStudio but still exists in the OTIO state.

        :param only_guid: When set, only checks the named timeline (used by the
            event-driven path after an item_atom fires).
        """
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return

        items = list(self._xs_sequence_playlists.items())
        if only_guid is not None:
            items = [(g, v) for g, v in items if g == only_guid]

        for tl_guid, tl_entry in items:
            xs_tl = tl_entry[1]
            if xs_tl is None:
                continue
            otio_tl = self.plugin.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue
            video_track = next(
                (t for t in otio_tl.tracks
                 if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                next(
                    (t for t in otio_tl.tracks
                     if t.kind == otio.schema.TrackKind.Video),
                    None,
                ),
            )
            if video_track is None:
                continue
            track_guid = video_track.metadata.get("sync", {}).get("guid")
            if not track_guid:
                continue

            # Read live clip names by re-serialising the xStudio timeline to OTIO.
            # to_otio_string() returns MissingReference for media but preserves clip
            # names, which is all we need for deletion detection.
            try:
                xs_otio_str = xs_tl.to_otio_string()
                xs_tl_parsed = otio.adapters.read_from_string(xs_otio_str, "otio_json")
                xs_video_track = next(
                    (t for t in xs_tl_parsed.tracks
                     if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                    next(
                        (t for t in xs_tl_parsed.tracks
                         if t.kind == otio.schema.TrackKind.Video),
                        None,
                    ),
                )
                xs_clip_names = (
                    [c.name for c in xs_video_track if isinstance(c, otio.schema.Clip)]
                    if xs_video_track is not None else []
                )
            except Exception:
                _log_exc(
                    f"poll_sequence_track_deletions: to_otio_string failed"
                    f" for {tl_guid[:8]}"
                )
                continue

            stored = self._xs_sequence_track_names.get(tl_guid)
            if stored is None:
                # First observation — record baseline and skip comparison.
                self._xs_sequence_track_names[tl_guid] = xs_clip_names
                continue

            if xs_clip_names == stored:
                continue

            # Diff: names in stored but gone from current → deleted.
            stored_counts = Counter(stored)
            current_counts = Counter(xs_clip_names)
            for clip_name, count in stored_counts.items():
                removed = count - current_counts.get(clip_name, 0)
                for _ in range(removed):
                    # Find the OTIO clip with this name to get its GUID.
                    for otio_clip in list(video_track):
                        if (
                            isinstance(otio_clip, otio.schema.Clip)
                            and otio_clip.name == clip_name
                        ):
                            child_guid = otio_clip.metadata.get("sync", {}).get("guid")
                            if child_guid:
                                self.plugin.media.evict(child_guid, tl_guid)
                                self.plugin.manager.broadcast_remove_child(
                                    track_guid, child_guid
                                )
                                _log(
                                    f"sequence track: deleted {clip_name!r}"
                                    f" from xStudio timeline"
                                )
                            break

            self._xs_sequence_track_names[tl_guid] = xs_clip_names

    def poll_sequence_source_ranges(self, only_guid: str | None = None) -> None:
        """Detect clip source_range changes (trims) in sequences and broadcast REPLACE_TIMELINE.

        :param only_guid: When set, only checks the named timeline.
        """
        if not self.plugin.manager or self.plugin.manager.status != STATE_SYNCED:
            return

        items = list(self._xs_sequence_playlists.items())
        if only_guid is not None:
            items = [(g, v) for g, v in items if g == only_guid]

        for tl_guid, tl_entry in items:
            playlist, xs_tl = tl_entry[0], tl_entry[1]
            if xs_tl is None:
                continue
            stored = self.plugin.manager.timelines.get(tl_guid)
            if stored is None:
                continue

            try:
                xs_otio_str = xs_tl.to_otio_string()
                xs_tl_parsed = otio.adapters.read_from_string(xs_otio_str, "otio_json")
                xs_video_track = next(
                    (t for t in xs_tl_parsed.tracks
                     if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                    next(
                        (t for t in xs_tl_parsed.tracks
                         if t.kind == otio.schema.TrackKind.Video),
                        None,
                    ),
                )
            except Exception:
                _log_exc(f"poll_sequence_source_ranges: to_otio_string failed for {tl_guid[:8]}")
                continue

            def _sr_fingerprint(video_track):
                # Include both Clip source_ranges AND Gap durations so that
                # repositioning a clip (which removes/adds/resizes Gaps) triggers
                # a REPLACE_TIMELINE just like a source_range trim does.  Sort
                # the result to make the comparison order-independent so pure
                # clip reorders don't produce a spurious REPLACE_TIMELINE.
                result = []
                if video_track is None:
                    return result
                for c in video_track:
                    if isinstance(c, otio.schema.Clip):
                        sr = c.source_range
                        result.append(('c', () if sr is None else (
                            sr.start_time.value, sr.start_time.rate,
                            sr.duration.value, sr.duration.rate)))
                    elif isinstance(c, otio.schema.Gap):
                        sr = c.source_range
                        result.append(('g', () if sr is None else (
                            sr.duration.value, sr.duration.rate)))
                result.sort()
                return result

            stored_video_track = next(
                (t for t in stored.tracks
                 if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                next(
                    (t for t in stored.tracks if t.kind == otio.schema.TrackKind.Video),
                    None,
                ),
            )
            if _sr_fingerprint(xs_video_track) == _sr_fingerprint(stored_video_track):
                continue

            _log(f"[2F] sequence {tl_guid[:8]} source_ranges changed — broadcasting REPLACE_TIMELINE")
            try:
                new_otio = self.plugin.builder.build_single_sequence_otio(playlist, xs_tl)
                if new_otio is None:
                    continue

                # When xs_tl.to_otio_string() returns MissingReference clips (a
                # client-loaded sequence where xStudio can't recover the original
                # URLs), build_single_sequence_otio produces an unusable OTIO.
                # Fall back: deep-copy the stored OTIO (which has valid
                # ExternalReference URLs) and patch in the updated source_ranges
                # from the xs-parsed OTIO by position.  This is safe here because
                # the sorted-fingerprint guard above already suppresses this path
                # for pure reorders — only SR changes reach this point, so the
                # positional ordering of xs clips matches the stored OTIO.
                new_video_track = next(
                    (t for t in new_otio.tracks
                     if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                    next(
                        (t for t in new_otio.tracks if t.kind == otio.schema.TrackKind.Video),
                        None,
                    ),
                )
                if new_video_track is not None:
                    new_clips = [c for c in new_video_track if isinstance(c, otio.schema.Clip)]
                    first_ref = getattr(new_clips[0], 'media_reference', None) if new_clips else None
                    if new_clips and not isinstance(first_ref, otio.schema.ExternalReference):
                        stored_clips = [
                            c for c in (stored_video_track or [])
                            if isinstance(c, otio.schema.Clip)
                        ]
                        if len(new_clips) == len(stored_clips):
                            patched = copy.deepcopy(stored)
                            patched_vt = next(
                                (t for t in patched.tracks
                                 if t.kind == otio.schema.TrackKind.Video
                                 and t.name != "Annotations"),
                                next(
                                    (t for t in patched.tracks
                                     if t.kind == otio.schema.TrackKind.Video),
                                    None,
                                ),
                            )
                            if patched_vt is not None:
                                patched_clips = [
                                    c for c in patched_vt if isinstance(c, otio.schema.Clip)
                                ]
                                for xs_c, p_c in zip(new_clips, patched_clips):
                                    p_c.source_range = xs_c.source_range
                                new_otio = patched
                        else:
                            _log(
                                f"poll_sequence_source_ranges: clip count mismatch "
                                f"({len(new_clips)} vs {len(stored_clips)}) for "
                                f"{tl_guid[:8]}, skipping"
                            )
                            continue

                if "xs_bin_media" in stored.metadata:
                    new_otio.metadata["xs_bin_media"] = stored.metadata["xs_bin_media"]
                self.plugin.manager.register_timeline(new_otio)
                self.update_xs_media_order(tl_guid, new_otio)
                self.plugin.manager.broadcast_replace_timeline(tl_guid)
            except Exception:
                _log_exc(f"poll_sequence_source_ranges: rebuild failed for {tl_guid[:8]}")

    # ── remote clip insert routing ─────────────────────────────────────

    def apply_remote_clip_insert(self, clip_obj: "otio.schema.Clip") -> None:
        """Route a received non-annotation INSERT_CHILD clip to the right handler.

        Searches ``_sync_playlists`` for the playlist whose OTIO track now
        contains *clip_obj* (the manager has already inserted it).  Dispatches
        to ``apply_flat_playlist_insert`` or ``apply_sequence_insert``
        depending on the timeline type.

        :param clip_obj: The newly-inserted OTIO Clip.
        """
        clip_guid = clip_obj.metadata.get("sync", {}).get("guid", "")
        if not clip_guid:
            return
        _log(f"apply_remote_clip_insert: clip={clip_guid[:8]} name={clip_obj.name!r}")
        for tl_guid, (pl, xs_tl) in self.plugin._sync_playlists.items():
            otio_tl = self.plugin.manager.timelines.get(tl_guid)
            if otio_tl is None:
                continue
            for track in otio_tl.tracks:
                if track.kind != otio.schema.TrackKind.Video:
                    continue
                for child in track:
                    if child.metadata.get("sync", {}).get("guid") == clip_guid:
                        if otio_tl.metadata.get("xs_flat_playlist"):
                            self.apply_flat_playlist_insert(clip_obj, pl, xs_tl, tl_guid)
                            # Reorder/reconcile the playlist order with the OTIO timeline
                            try:
                                self.apply_flat_playlist_move(tl_guid, pl, otio_tl, 0)
                            except Exception:
                                _log_exc(
                                    "Failed to reconcile playlist order after remote insert"
                                )
                        else:
                            # Debounce: enqueue ONE rebuild for this timeline no
                            # matter how many clips arrive in the batch.  Each
                            # apply_sequence_insert does a full load_otio +
                            # source-switch (~slow in xStudio); doing it per clip
                            # serialised ~18s rebuilds back-to-back.
                            if tl_guid not in self._pending_sequence_rebuilds:
                                self._pending_sequence_rebuilds.add(tl_guid)
                                _log(f"apply_remote_clip_insert: queuing rebuild tl={tl_guid[:8]}")
                                self.plugin._cmd_queue.put(("rebuild_sequence", {"tl_guid": tl_guid}))
                            # Keep known_names in sync so next poll doesn't
                            # re-broadcast this remote-received clip.
                            if tl_guid in self._xs_sequence_playlists:
                                try:
                                    _sq_pl, _sq_tl, _sq_known = self._xs_sequence_playlists[tl_guid]
                                    if clip_obj.name not in _sq_known:
                                        self._xs_sequence_playlists[tl_guid] = (
                                            _sq_pl, _sq_tl, _sq_known | {clip_obj.name}
                                        )
                                except Exception:
                                    pass
                        return

    def apply_flat_playlist_move(
        self,
        tl_guid: str,
        xs_playlist,
        otio_tl: "otio.schema.Timeline",
        to_index: int,
    ) -> None:
        """Reorder a media item in a flat xStudio Playlist to match a MOVE_CHILD event.

        Reconciles the entire xStudio playlist order to match the updated OTIO track
        by executing a right-to-left movement pass.

        :param tl_guid: GUID of the flat-playlist OTIO timeline.
        :param xs_playlist: xStudio Playlist object.
        :param otio_tl: Updated OTIO Timeline (MOVE_CHILD already applied).
        :param to_index: Target index from the MOVE_CHILD payload (not directly used).
        """
        video_track = next(
            (t for t in otio_tl.tracks if t.kind == otio.schema.TrackKind.Video),
            None,
        )
        if video_track is None:
            return

        ordered_clips = [c for c in video_track if isinstance(c, otio.schema.Clip)]

        # Resolve target media UUIDs to verify and align
        target_uuids = []
        for clip in ordered_clips:
            cg = clip.metadata.get("sync", {}).get("guid", "")
            media = self.plugin.media.media_for_sync_guid(cg)[0]
            if media:
                target_uuids.append(media.uuid)

        # Get current order in xStudio
        try:
            current_media = xs_playlist.media
            current_uuids = [m.uuid for m in current_media]
        except Exception:
            return

        if current_uuids == target_uuids:
            return

        # Reconcile from right to left
        for i in range(len(target_uuids) - 1, -1, -1):
            uuid_val = target_uuids[i]
            try:
                current_media = xs_playlist.media
                current_uuids = [m.uuid for m in current_media]
                curr_idx = current_uuids.index(uuid_val)
            except Exception:
                continue

            if curr_idx != i:
                before_media = None
                if i + 1 < len(target_uuids):
                    before_uuid = target_uuids[i + 1]
                    before_media = next(
                        (m for m in current_media if m.uuid == before_uuid), None
                    )

                moved_media = next(
                    (m for m in current_media if m.uuid == uuid_val), None
                )
                if moved_media:
                    try:
                        if before_media:
                            xs_playlist.move_media(moved_media, before=before_media)
                        else:
                            xs_playlist.move_media(moved_media)
                        _log(
                            f"flat playlist move (reconcile):"
                            f" moved {getattr(moved_media, 'name', '?')!r} to index {i}"
                        )
                    except Exception:
                        _log_exc(
                            f"flat playlist move (reconcile): failed for"
                            f" {getattr(moved_media, 'name', '?')!r}"
                        )

        # Update stored order in flat playlists map to prevent polling feedback loop
        if tl_guid in self._xs_flat_playlists:
            try:
                final_media = xs_playlist.media
                final_order = [
                    self.plugin.media.sync_guid_for_xs_uuid(str(m.uuid), tl_guid) or str(m.uuid)
                    for m in final_media
                ]
                self._xs_flat_playlists[tl_guid] = (xs_playlist, final_order)
            except Exception:
                pass

    def apply_flat_playlist_insert(
        self, clip_obj: "otio.schema.Clip", xs_playlist, xs_timeline, tl_guid: str = None
    ) -> None:
        self.plugin._structural_mutation_suppress_until = time.monotonic() + 1.5
        """Add a newly-broadcast clip to a flat xStudio Playlist.

        Called when an INSERT_CHILD event arrives for a clip that belongs to a
        flat-playlist track.  Adds the media via ``add_media(path)``, records
        the GUID→Media mapping, then adds the media to the Timeline child so
        it appears in the sequence panel.

        :param clip_obj: The inserted OTIO Clip (manager has already inserted it
            into the OTIO track).
        :param xs_playlist: xStudio Playlist to add the media to.
        :param xs_timeline: xStudio Timeline child to add the media to.
        :param tl_guid: Sync GUID of the target timeline.
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
                self.plugin.media._flat_clip_to_media[clip_guid] = media_obj
                self.plugin.media.register(media_obj, clip_guid, tl_guid)
            if xs_timeline is not None:
                try:
                    xs_timeline.add_media(media_obj)
                except Exception:
                    _log_exc(f"flat insert: could not add {clip_obj.name!r} to timeline")
            _log(f"flat playlist insert: {clip_obj.name!r} ← {path!r}")
        except Exception:
            _log_exc(f"flat playlist insert: add_media failed for {path!r}")

    def execute_sequence_rebuild(self, tl_guid: str) -> None:
        """Run a coalesced sequence rebuild queued by ``apply_remote_clip_insert``.

        Clears the pending flag first so any insert arriving during the (slow)
        reload re-queues a fresh rebuild, then reloads once from the manager's
        current OTIO — reflecting every clip applied since the rebuild was queued.
        """
        self._pending_sequence_rebuilds.discard(tl_guid)
        if not self.plugin.manager:
            return
        otio_tl = self.plugin.manager.timelines.get(tl_guid)
        if otio_tl is None:
            return
        entry = self.plugin._sync_playlists.get(tl_guid)
        xs_tl = entry[1] if entry else None
        if xs_tl is None:
            return
        self.apply_sequence_insert(tl_guid, otio_tl, xs_tl)

    def apply_sequence_insert(
        self, tl_guid: str, otio_tl: "otio.schema.Timeline", xs_timeline
    ) -> None:
        """Reload an xStudio sequence Timeline after a remote clip insertion.

        The manager has already inserted the new OTIO Clip into the track.
        We re-serialise the OTIO and call ``load_otio(clear=True)``.

        :param tl_guid: GUID of the affected OTIO timeline.
        :param otio_tl: Updated OTIO Timeline.
        :param xs_timeline: xStudio Timeline to reload.
        """
        # Each INSERT_CHILD reloads the whole sequence via load_otio(clear=True).
        # When inserts arrive spread out (not batched), an earlier suppression
        # cooldown here dropped later clips, so we always rebuild.  The repeated
        # rebuild is safe because we strip stale xstudio actor UUIDs below before
        # load_otio, so fresh ClipActors are created each time (the ClipActor CAF
        # errors came from reusing those stale UUIDs, not from rebuilding itself).
        self.plugin._structural_mutation_suppress_until = time.monotonic() + 1.5
        n_clips = sum(
            1 for t in otio_tl.tracks
            for c in t if isinstance(c, otio.schema.Clip)
        )
        _log(f"apply_sequence_insert: tl={tl_guid[:8]} clips={n_clips} xs_tl={xs_timeline is not None}")
        try:
            _t0 = time.monotonic()
            prepared_otio = self.plugin.media.prepare_otio_for_load(otio_tl)
            # Strip stale xstudio actor UUIDs from clips so load_otio creates
            # fresh actors instead of trying to reuse ones from a previous load.
            for _track in prepared_otio.tracks:
                for _item in _track:
                    if isinstance(_item, otio.schema.Clip):
                        _item.metadata.pop("xstudio", None)
            self.plugin.builder.fill_source_ranges(prepared_otio)
            otio_str = otio.adapters.write_to_string(prepared_otio, "otio_json")
            _t_prep = time.monotonic()
            self.plugin._reload_suppress_until = time.monotonic() + 2.0
            self._xs_sequence_track_names[tl_guid] = None
            _log(f"apply_sequence_insert: calling load_otio tl={tl_guid[:8]}")
            xs_timeline.load_otio(otio_str, clear=True)
            _t_load = time.monotonic()
            if tl_guid in self.plugin._sync_playlists:
                playlist = self.plugin._sync_playlists[tl_guid][0]
                self.plugin.media.bootstrap_mapping(playlist, otio_tl, xs_timeline)
            _t_boot = time.monotonic()
            try:
                self.plugin.connection.api.session.set_on_screen_source(xs_timeline)
            except Exception:
                pass
            _t_sos = time.monotonic()
            _log(
                f"sequence insert: reloaded timeline {tl_guid[:8]} — "
                f"prep={_t_prep-_t0:.2f}s load_otio={_t_load-_t_prep:.2f}s "
                f"bootstrap={_t_boot-_t_load:.2f}s set_on_screen={_t_sos-_t_boot:.2f}s"
            )
        except Exception:
            self.plugin._reload_suppress_until = 0.0
            _log_exc(f"sequence insert: failed to reload timeline {tl_guid[:8]}")

    # ── remote remove/move child ───────────────────────────────────────

    def apply_remote_remove_child(self, data: dict) -> None:
        self.plugin._structural_mutation_suppress_until = time.monotonic() + 1.5
        """Apply a REMOVE_CHILD event from a remote peer to the local xStudio session.

        The manager has already removed the clip from the OTIO track before this
        is called.

        * **Flat playlists**: removes the media from the playlist bin using the
          ``_flat_clip_to_media`` mapping, then refreshes the stored name list so
          the next poll tick does not re-broadcast the removal.
        * **Sequence timelines**: reloads the updated OTIO via ``load_otio(clear=True)``.

        :param data: Payload dict with keys ``parent_uuid`` and ``child_uuid``.
        """
        parent_uuid = data.get("parent_uuid")
        child_uuid = data.get("child_uuid")
        if not parent_uuid or not child_uuid:
            return

        # Identify the owning timeline by its track GUID.
        tl_guid = None
        for guid, tl in self.plugin.manager.timelines.items():
            for track in tl.tracks:
                if track.metadata.get("sync", {}).get("guid") == parent_uuid:
                    tl_guid = guid
                    break
            if tl_guid:
                break

        self.plugin.media.evict(child_uuid, tl_guid)

        if tl_guid is None:
            _log(f"remote remove_child: no timeline for track {parent_uuid[:8]}")
            return

        playlist_tuple = self.plugin._sync_playlists.get(tl_guid)
        if playlist_tuple is None:
            _log(
                f"remote remove_child: no xStudio playlist for timeline {tl_guid[:8]}"
            )
            return
        xs_playlist, xs_timeline = playlist_tuple

        otio_tl = self.plugin.manager.timelines.get(tl_guid)

        # --- Flat playlist: remove the media object from the bin ---
        if xs_timeline is None or (otio_tl and otio_tl.metadata.get("xs_flat_playlist")):
            media_obj = self.plugin.media._flat_clip_to_media.pop(child_uuid, None)
            if media_obj is not None:
                try:
                    xs_playlist.remove_media(media_obj)
                    _log(
                        f"remote remove_child: removed media {child_uuid[:8]}"
                        f" from flat playlist"
                    )
                except Exception:
                    _log_exc(
                        f"remote remove_child: remove_media failed for {child_uuid[:8]}"
                    )
            else:
                _log(
                    f"remote remove_child: media not found for child_guid={child_uuid[:8]}"
                )
            # Refresh stored order so the next poll does not re-fire.
            if tl_guid in self._xs_flat_playlists:
                try:
                    cur_pl, _ = self._xs_flat_playlists[tl_guid]
                    self._xs_flat_playlists[tl_guid] = (
                        cur_pl,
                        [
                            self.plugin.media.sync_guid_for_xs_uuid(str(m.uuid), tl_guid)
                            or str(m.uuid)
                            for m in xs_playlist.media
                        ],
                    )
                except Exception:
                    pass
            return

        # --- Sequence timeline: reload OTIO to reflect the removal ---
        if otio_tl is None:
            return
        try:
            prepared_otio = self.plugin.media.prepare_otio_for_load(otio_tl)
            self.plugin.builder.fill_source_ranges(prepared_otio)
            otio_str = otio.adapters.write_to_string(prepared_otio, "otio_json")
            self.plugin._reload_suppress_until = time.monotonic() + 2.0
            self._xs_sequence_track_names[tl_guid] = None
            xs_timeline.load_otio(otio_str, clear=True)
            if tl_guid in self.plugin._sync_playlists:
                playlist = self.plugin._sync_playlists[tl_guid][0]
                self.plugin.media.bootstrap_mapping(playlist, otio_tl, xs_timeline)
            self.update_xs_media_order(tl_guid, otio_tl)
            _log(f"remote remove_child: reloaded sequence timeline {tl_guid[:8]}")
            try:
                self.plugin.connection.api.session.set_on_screen_source(xs_timeline)
            except Exception:
                pass
        except Exception:
            self.plugin._reload_suppress_until = 0.0
            _log_exc(
                f"remote remove_child: reload failed for timeline {tl_guid[:8]}"
            )
            return

        # Sync known_names and media-name tracking so the next poll does not
        # re-detect the now-absent media as a deletion and re-broadcast it.
        if tl_guid in self._xs_sequence_playlists:
            try:
                _sq_pl, _sq_tl, _ = self._xs_sequence_playlists[tl_guid]
                current_names: set = set()
                for _t in otio_tl.tracks:
                    for _c in _t:
                        if isinstance(_c, otio.schema.Clip):
                            current_names.add(_c.name)
                try:
                    current_names |= {m.name for m in xs_playlist.media}
                except Exception:
                    pass
                self._xs_sequence_playlists[tl_guid] = (_sq_pl, _sq_tl, current_names)
            except Exception:
                pass
        if tl_guid in self._xs_sequence_media_names:
            try:
                self._xs_sequence_media_names[tl_guid] = {
                    m.name for m in xs_playlist.media
                }
            except Exception:
                pass

    def apply_remote_move_child(self, data: dict) -> None:
        self.plugin._structural_mutation_suppress_until = time.monotonic() + 1.5
        """Reorder a media clip in the xStudio timeline to match a remote MOVE_CHILD event.

        ``track.move_children`` triggers xStudio's QML delegate model directly
        and causes "index out of range" errors in the timeline panel.  Instead
        we re-serialise the updated OTIO timeline (the manager has already
        applied the reorder) and call ``load_otio`` with ``clear=True``.

        :param data: Payload dict with keys ``parent_uuid``, ``child_uuid``, ``to_index``.
        """
        parent_uuid = data.get("parent_uuid")
        child_uuid = data.get("child_uuid")
        to_index: int = data.get("to_index", 0)

        if not parent_uuid or not child_uuid:
            return

        # Find the OTIO timeline that owns the reordered Media track.
        tl_guid = None
        for guid, tl in self.plugin.manager.timelines.items():
            for track in tl.tracks:
                if track.metadata.get("sync", {}).get("guid") == parent_uuid:
                    tl_guid = guid
                    break
            if tl_guid:
                break

        if tl_guid is None:
            _log(f"move_child: no timeline found for track {parent_uuid[:8]}")
            return

        playlist_tuple = self.plugin._sync_playlists.get(tl_guid)
        if playlist_tuple is None:
            _log(f"move_child: no xStudio playlist for timeline {tl_guid[:8]}")
            return
        xs_playlist, xs_timeline = playlist_tuple

        otio_tl = self.plugin.manager.timelines.get(tl_guid)
        if otio_tl is None:
            _log(f"move_child: timeline {tl_guid[:8]} not in manager.timelines")
            return

        # Flat playlists: reorder the media bin with move_media.
        # Their xStudio Timeline was built from add_media calls (not load_otio),
        # so load_otio cannot be used to reorder it.
        if xs_timeline is None or otio_tl.metadata.get("xs_flat_playlist"):
            self.apply_flat_playlist_move(tl_guid, xs_playlist, otio_tl, to_index)
            return

        # Try the lightweight incremental path: move the clip directly in the
        # xStudio track without rebuilding the whole timeline.  ``_xs_media_order``
        # must track the order xStudio has *actually applied* (we mirror each
        # successful move below instead of reading the OTIO model, which the
        # manager batch-patches ahead of xStudio during a burst of moves).
        # Keeping it accurate is what stops ``from_index`` collapsing onto
        # ``to_index`` — a no-op that xStudio rejects as "Invalid Move" and that
        # forced every move onto the ~100x slower load_otio rebuild path.
        stored_order = self._xs_media_order.get(tl_guid)
        if stored_order and child_uuid in stored_order:
            from_index = stored_order.index(child_uuid)
            if from_index == to_index:
                # Already where the move wants it in xStudio's real track.
                _log(
                    f"move_child: clip {child_uuid[:8]} already at index"
                    f" {to_index} in {tl_guid[:8]}; nothing to do"
                )
                return
            try:
                # Find the Media track — the video track that actually has clips
                # (Annotations track has 0 clips and should be skipped).
                media_track = None
                for _track in xs_timeline.video_tracks:
                    try:
                        if len(_track.clips) > 0:
                            media_track = _track
                            break
                    except Exception:
                        pass
                if media_track is not None:
                    # xStudio splices the item *before* the destination index in
                    # the pre-move list, so to land at final index ``to_index``
                    # the dest is shifted by one when moving downward.
                    dest = to_index if to_index <= from_index else to_index + 1
                    media_track.move_children(from_index, 1, dest, False)
                    # Mirror the move in our tracked order so the next move in a
                    # burst computes ``from_index`` against xStudio's real state
                    # (no OTIO round-trip, no drift). Verified against the live
                    # xStudio track: this mirror stays exactly in step with what
                    # move_children produces, so no re-read is needed here.
                    new_order = list(stored_order)
                    new_order.insert(to_index, new_order.pop(from_index))
                    self._xs_media_order[tl_guid] = new_order
                    try:
                        self.plugin.connection.api.session.set_on_screen_source(xs_timeline)
                    except Exception:
                        pass
                    _log(
                        f"move_child: moved clip {child_uuid[:8]} from {from_index}"
                        f" to {to_index} in {tl_guid[:8]}"
                    )
                    return
            except Exception:
                _log_exc(
                    f"move_child: direct move_children failed for {tl_guid[:8]},"
                    f" falling back to load_otio"
                )

        # Fall back to load_otio when move_children is unavailable or fails.
        try:
            # Bootstrap mapping before preparing OTIO so prepare_otio_for_load
            # can rewrite all clip URIs to match existing media, preventing
            # load_otio from creating duplicate media items.
            self.plugin.media.bootstrap_mapping(xs_playlist, otio_tl, xs_timeline)
            prepared_otio = self.plugin.media.prepare_otio_for_load(otio_tl)
            self.plugin.builder.fill_source_ranges(prepared_otio)
            otio_str = otio.adapters.write_to_string(prepared_otio, "otio_json")
            # Suppress show_atom bursts that xStudio fires when it re-triggers
            # existing bookmarks after the timeline is rebuilt.
            self.plugin._reload_suppress_until = time.monotonic() + 2.0
            self._xs_sequence_track_names[tl_guid] = None
            xs_timeline.load_otio(otio_str, clear=True)
            if tl_guid in self.plugin._sync_playlists:
                playlist = self.plugin._sync_playlists[tl_guid][0]
                self.plugin.media.bootstrap_mapping(playlist, otio_tl, xs_timeline)
            self.update_xs_media_order(tl_guid, otio_tl)
            # Re-activate the timeline in the UI — load_otio does not restore
            # the viewed source automatically.
            try:
                self.plugin.connection.api.session.set_on_screen_source(xs_timeline)
            except Exception:
                pass
            _log(
                f"move_child: reloaded timeline {tl_guid[:8]}"
                f" — {child_uuid[:8]} now at index {to_index}"
            )
        except Exception:
            self.plugin._reload_suppress_until = 0.0
            _log_exc(f"move_child: failed to reload timeline {tl_guid[:8]}")
            return

    # ── deferred snapshots ─────────────────────────────────────────────

    def process_deferred_snapshots(self) -> None:
        """Send deferred state snapshots to requesters that arrived before timelines were ready."""
        if not self._pending_snapshot_requesters:
            return
        if not self.plugin.manager or not self.plugin.manager._timelines:
            return
        for _req_guid in list(self._pending_snapshot_requesters):
            _log(f"Deferred snapshot: sending to {_req_guid[:8]}")
            self.plugin.manager.send_state_snapshot(
                _req_guid,
                playback_state=self.plugin.playback.current_playback_state(),
            )
        self._pending_snapshot_requesters.clear()
