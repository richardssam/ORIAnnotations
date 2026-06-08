#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""TimelineBuildController — OTIO timeline construction (master side)."""

import hashlib
import time

import opentimelineio as otio
from xstudio.api.session.playlist.timeline import Timeline

from .utils import _log, _log_exc, _uri_to_posix_path


class TimelineBuildController:
    """Owns OTIO timeline construction and xStudio playlist/timeline loading.

    :param plugin: Back-reference to the ORISyncPlugin instance.
    """

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        # Throttle for the "viewport not ready" log during retry loop.
        self._last_timeline_defer_log_time: float = 0.0

    # ── static helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def fill_source_ranges(otio_tl: otio.schema.Timeline) -> None:
        """Backfill source_range from media available_range on clips where it is absent.

        xStudio's load_otio requires an explicit source_range to position and
        size each clip correctly; without it clips render as separate playlist
        items rather than a joined sequence.
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

    # ── load ───────────────────────────────────────────────────────────────────

    def do_load_timelines(self) -> None:
        """Create one xStudio Sequence playlist per OTIO timeline in the snapshot."""
        plugin = self.plugin
        if not plugin.manager or not plugin.manager.timelines:
            _log("Snapshot had no timelines")
            return

        if plugin.display.get_viewport() is None:
            now = time.monotonic()
            if now - self._last_timeline_defer_log_time >= 5.0:
                _log("Deferring timeline loading — viewport not ready")
                self._last_timeline_defer_log_time = now
            time.sleep(0.2)
            plugin._cmd_queue.put(("load_timelines", {}))
            return

        first_xs_timeline = None
        for guid, otio_tl in plugin.manager.timelines.items():
            if guid in plugin._sync_playlists:
                continue

            if otio_tl.metadata.get("clip_timeline_for"):
                _log(f"Skipping loading of dynamic clip timeline {otio_tl.name!r} to xStudio")
                continue

            playlist_name = otio_tl.metadata.get("xs_playlist_name") or otio_tl.name or guid[:8]
            timeline_name = otio_tl.name or guid[:8]

            self.fill_source_ranges(otio_tl)

            tracks = list(otio_tl.tracks)
            _log(f"OTIO Timeline {timeline_name!r}: {len(tracks)} track(s)")
            for i, track in enumerate(tracks):
                children = list(track)
                _log(f"  Track {i} {track.name!r} kind={track.kind}: {len(children)} child(ren)")
                for j, child in enumerate(children[:8]):
                    sr = getattr(child, "source_range", None)
                    _log(f"    [{j}] {type(child).__name__} {getattr(child, 'name', '?')!r} sr={sr}")

            if otio_tl.metadata.get("xs_flat_playlist"):
                xs_seq_guid = otio_tl.metadata.get("xs_sequence_guid")
                if xs_seq_guid and xs_seq_guid in plugin._sync_playlists:
                    try:
                        playlist, xs_timeline = plugin._sync_playlists[xs_seq_guid]
                        video_track = next(
                            (t for t in otio_tl.tracks if t.kind == otio.schema.TrackKind.Video),
                            None,
                        )
                        if video_track:
                            for clip in video_track:
                                if isinstance(clip, otio.schema.Clip):
                                    cg = clip.metadata.get("sync", {}).get("guid")
                                    if cg:
                                        media_obj = plugin.media.media_for_sync_guid(cg)[0]
                                        if media_obj:
                                            playlist.move_media(media_obj)

                        plugin._sync_playlists[guid] = (playlist, None)
                        plugin.structure.subscribe_timeline_item_events(guid, playlist)
                        try:
                            current_media = playlist.media
                            plugin.structure._xs_flat_playlists[guid] = (
                                playlist,
                                [plugin.media.sync_guid_for_xs_uuid(str(m.uuid), guid) or str(m.uuid) for m in current_media],
                            )
                        except Exception:
                            pass
                        if first_xs_timeline is None:
                            first_xs_timeline = playlist
                        _log(f"Linked flat playlist OTIO {guid[:8]} to existing sequence playlist for sequence {xs_seq_guid[:8]}")
                        plugin.annotation.load_snapshot_annotations(otio_tl, playlist)
                        continue
                    except Exception:
                        _log_exc(f"Failed to link/reorder flat playlist for sequence {xs_seq_guid[:8]} — falling through to create new")

                try:
                    playlist = plugin.connection.api.session.create_playlist(playlist_name)[1]
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
                                    clip_guid = clip.metadata.get("sync", {}).get("guid")
                                    if clip_guid and media_obj:
                                        plugin.media._flat_clip_to_media[clip_guid] = media_obj
                                except Exception:
                                    _log_exc(f"  Could not add {path!r}")
                    plugin._sync_playlists[guid] = (playlist, None)
                    plugin.structure.subscribe_timeline_item_events(guid, playlist)
                    plugin.media.bootstrap_mapping(playlist, otio_tl, None)
                    try:
                        current_media = playlist.media
                        plugin.structure._xs_flat_playlists[guid] = (
                            playlist,
                            [plugin.media.sync_guid_for_xs_uuid(str(m.uuid), guid) or str(m.uuid) for m in current_media],
                        )
                    except Exception:
                        _log_exc("Could not init _xs_flat_playlists entry from load")
                    if first_xs_timeline is None:
                        first_xs_timeline = playlist
                    _log(f"Created flat playlist {playlist_name!r} from OTIO timeline {guid[:8]}")
                    plugin.annotation.load_snapshot_annotations(otio_tl, playlist)
                except Exception:
                    _log_exc(f"Failed to create flat playlist for {playlist_name!r}")
            else:
                try:
                    playlist = plugin.connection.api.session.create_playlist(playlist_name)[1]
                    xs_timeline = playlist.create_timeline(timeline_name)[1]
                    otio_str = otio.adapters.write_to_string(otio_tl, "otio_json")
                    xs_timeline.load_otio(otio_str, clear=True)
                    plugin.structure._xs_sequence_track_names[guid] = None
                    plugin._sync_playlists[guid] = (playlist, xs_timeline)
                    plugin.media.bootstrap_mapping(playlist, otio_tl, xs_timeline)
                    _media_tr = next(
                        (t for t in otio_tl.tracks
                         if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                        next(
                            (t for t in otio_tl.tracks
                             if t.kind == otio.schema.TrackKind.Video),
                            None,
                        ),
                    )
                    _known = {
                        c.name for c in (_media_tr or [])
                        if isinstance(c, otio.schema.Clip)
                    }
                    try:
                        _known |= {m.name for m in playlist.media}
                    except Exception:
                        pass
                    plugin.structure._xs_sequence_playlists[guid] = (playlist, xs_timeline, _known)
                    try:
                        plugin.structure._xs_sequence_media_names[guid] = {m.name for m in playlist.media}
                    except Exception:
                        plugin.structure._xs_sequence_media_names[guid] = set()
                    plugin.structure.update_xs_media_order(guid, otio_tl)
                    if first_xs_timeline is None:
                        first_xs_timeline = xs_timeline
                    _log(f"Created playlist {playlist_name!r} / timeline {timeline_name!r} from OTIO timeline {guid[:8]}")
                    plugin.annotation.load_snapshot_annotations(otio_tl, playlist)
                except Exception:
                    _log_exc(f"Failed to create playlist for {playlist_name!r}")

        if first_xs_timeline is not None:
            plugin.display._pending_on_screen_source = first_xs_timeline

    # ── OTIO construction ─────────────────────────────────────────────────────

    def build_otio_timelines(self) -> list:
        """Convert all xStudio session playlists into OTIO Timelines."""
        plugin = self.plugin
        result: list[otio.schema.Timeline] = []
        try:
            playlists = plugin.connection.api.session.playlists
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
                        tl_guid = str(xs_tl.uuid)
                        tl.metadata.setdefault("sync", {})["guid"] = tl_guid
                        tl.metadata["xs_playlist_name"] = playlist.name

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
                        _media_tr_m = next(
                            (t for t in tl.tracks
                             if t.kind == otio.schema.TrackKind.Video and t.name != "Annotations"),
                            next(
                                (t for t in tl.tracks
                                 if t.kind == otio.schema.TrackKind.Video),
                                None,
                            ),
                        )
                        _known_seq = {
                            c.name for c in (_media_tr_m or [])
                            if isinstance(c, otio.schema.Clip)
                        }
                        try:
                            _known_seq |= {m.name for m in playlist.media}
                        except Exception:
                            pass
                        plugin.structure._xs_sequence_playlists[tl_guid] = (playlist, xs_tl, _known_seq)
                        plugin._sync_playlists[tl_guid] = (playlist, xs_tl)
                        plugin.media.bootstrap_mapping(playlist, tl, xs_tl)
                        plugin.structure.update_xs_media_order(tl_guid, tl)
                        try:
                            plugin.structure._xs_sequence_media_names[tl_guid] = {m.name for m in playlist.media}
                        except Exception:
                            plugin.structure._xs_sequence_media_names[tl_guid] = set()
                        plugin.structure.subscribe_timeline_item_events(tl_guid, xs_tl)
                        try:
                            flat_tl = self.build_otio_from_playlist_media(playlist)
                            if flat_tl is not None:
                                flat_tl.metadata["xs_sequence_guid"] = tl_guid
                                result.append(flat_tl)
                        except Exception:
                            _log_exc(f"Could not build flat playlist OTIO for playlist {playlist.name!r}")
                    except Exception:
                        _log_exc(f"Could not export Timeline {getattr(xs_tl, 'name', '?')!r}")
            else:
                tl = self.build_otio_from_playlist_media(playlist)
                if tl is not None:
                    result.append(tl)

        if not result:
            tl = self.build_otio_from_viewed_container()
            if tl is not None:
                result.append(tl)

        return result

    def build_otio_from_viewed_container(self) -> "otio.schema.Timeline | None":
        """Export the currently-viewed xStudio container as an OTIO Timeline."""
        try:
            container = self.plugin.playback.get_viewed_container_safe()
            if container is None:
                _log("build_otio_from_viewed_container: no valid viewed_container (session may be empty)")
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

    def build_otio_from_playlist_media(self, playlist) -> "otio.schema.Timeline | None":
        """Build a synthetic OTIO Timeline from a flat Playlist's media items."""
        plugin = self.plugin
        try:
            media_list = playlist.media
        except Exception:
            _log_exc(f"Could not get media from playlist {getattr(playlist, 'name', '?')!r}")
            return None

        name = getattr(playlist, "name", "Playlist")
        tl = otio.schema.Timeline(name=name)
        tl_guid = str(playlist.uuid)
        tl.metadata.setdefault("sync", {})["guid"] = tl_guid
        tl.metadata["xs_flat_playlist"] = True
        plugin._sync_playlists[tl_guid] = (playlist, None)
        plugin.structure.subscribe_timeline_item_events(tl_guid, playlist)
        track = otio.schema.Track(name="Video Track", kind=otio.schema.TrackKind.Video)
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
                frame_count: int | None = None
                try:
                    info = media.display_info
                    for key in ("frames", "Frames", "frame_count", "num_frames", "duration_frames"):
                        if key in info and info[key]:
                            frame_count = int(info[key])
                            break
                except Exception:
                    pass
                clip_guid = hashlib.sha1(
                    f"{track_guid}:{media_idx}:{media.name}".encode("utf-8")
                ).hexdigest()
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
                plugin.media._flat_clip_to_media[clip_guid] = media
                plugin.media.register(media, clip_guid, tl_guid)
                track.append(clip)
                _log(f"  Flat media clip: {media.name!r} fps={fps} frames={frame_count}")
            except Exception:
                _log_exc(f"Could not convert media {getattr(media, 'name', '?')!r} to OTIO clip")

        clips = list(track)
        tl.tracks.append(track)
        plugin.structure._xs_flat_playlists[tl_guid] = (
            playlist,
            [plugin.media.sync_guid_for_xs_uuid(str(m.uuid), tl_guid) or str(m.uuid) for m in media_list],
        )
        _log(f"Built synthetic OTIO timeline for flat playlist {name!r}: {len(clips)} clip(s)")
        return tl

    def build_single_sequence_otio(self, playlist, xs_tl) -> "otio.schema.Timeline | None":
        """Build an OTIO Timeline from a single xStudio Timeline container."""
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

            if not any(t.kind == otio.schema.TrackKind.Video for t in tl.tracks):
                video_track = otio.schema.Track(name="Video", kind=otio.schema.TrackKind.Video)
                tl.tracks.append(video_track)

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
            _log(f"build_single_sequence_otio: {tl.name!r}")
            return tl
        except Exception:
            _log_exc(
                f"build_single_sequence_otio: failed for "
                f"{getattr(xs_tl, 'name', '?')!r}"
            )
            return None
