#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""MediaMapController — sync-GUID ↔ xStudio-media bidirectional mapping."""

import os
import opentimelineio as otio

from .utils import _log, _log_exc, _uri_to_posix_path


class MediaMapController:
    """Owns the bidirectional sync-GUID ↔ xStudio media object mapping.

    Instantiated first in ORISyncPlugin.__init__ so sibling controllers can
    reference self.plugin.media safely.

    :param plugin: Back-reference to the ORISyncPlugin instance.
    """

    def __init__(self, plugin) -> None:
        self.plugin = plugin

        # Bidirectional mapping between sync GUIDs and xStudio media objects/UUIDs.
        self._sync_guid_to_xs_media: dict = {}
        self._xs_uuid_to_sync_guid: dict = {}

        # Maps clip_guid → Media for clips added to flat playlists on this
        # (client) peer via _do_load_timelines or _apply_flat_playlist_insert.
        # Avoids fragile name-based lookups when xStudio uses the full file path
        # as the media name after add_media(path).
        self._flat_clip_to_media: dict = {}

    def reset(self) -> None:
        """Clear all mappings (called from ORISyncPlugin.disconnect)."""
        self._sync_guid_to_xs_media.clear()
        self._xs_uuid_to_sync_guid.clear()
        self._flat_clip_to_media.clear()

    # ── registration ──────────────────────────────────────────────────────────

    def register(self, media_obj, sync_guid: str, tl_guid: str = None) -> None:
        """Register a media object and its sync GUID in the bidirectional mapping."""
        if not media_obj or not sync_guid:
            return
        uuid_str = str(media_obj.uuid)
        self._sync_guid_to_xs_media[sync_guid] = media_obj
        manager = self.plugin.manager
        if not tl_guid and manager:
            for g, tl in manager.timelines.items():
                found = False
                for track in tl.tracks:
                    for child in track:
                        if child.metadata.get("sync", {}).get("guid") == sync_guid:
                            tl_guid = g
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
        key = (tl_guid or "global", uuid_str)
        self._xs_uuid_to_sync_guid[key] = sync_guid

    def evict(self, sync_guid: str, tl_guid: str = None) -> None:
        """Remove a media object from the bidirectional mapping by its sync GUID."""
        media_obj = self._sync_guid_to_xs_media.pop(sync_guid, None)
        if media_obj:
            uuid_str = str(media_obj.uuid)
            if tl_guid:
                self._xs_uuid_to_sync_guid.pop((tl_guid, uuid_str), None)
            else:
                for k in list(self._xs_uuid_to_sync_guid.keys()):
                    if isinstance(k, tuple) and len(k) == 2 and k[1] == uuid_str:
                        self._xs_uuid_to_sync_guid.pop(k, None)

    # ── lookup ────────────────────────────────────────────────────────────────

    def media_for_sync_guid(self, sync_guid: str) -> tuple:
        """Look up the xStudio media item corresponding to an OTIO clip GUID.

        :param sync_guid: Sync GUID of the OTIO media clip.
        :returns: ``(media, aspect_half)`` or ``(None, 0.8889)`` on failure.
        :rtype: tuple
        """
        media = self._sync_guid_to_xs_media.get(sync_guid)
        if media is None:
            media = self._flat_clip_to_media.get(sync_guid)
            if media is None:
                media, aspect = self.find_media_for_clip_guid(sync_guid)
                if media is not None:
                    _log(f"WARNING: media_for_sync_guid fell back to name-scan for guid {sync_guid[:8]}")
                return media, aspect

        def _aspect(m):
            try:
                ms = m.media_source()
                streams = ms.streams()
                if streams:
                    res = streams[0].media_stream_detail.resolution()
                    if res.y > 0:
                        return res.x / (2.0 * res.y)
            except Exception:
                pass
            return 0.8889

        return media, _aspect(media)

    def sync_guid_for_xs_uuid(self, xs_uuid_str: str, tl_guid: str = None) -> "str | None":
        """Return the OTIO clip GUID for an xStudio media item by its UUID string.

        :param xs_uuid_str: String representation of the media UUID.
        :param tl_guid: Optional sync GUID of the owner timeline.
        :returns: GUID string, or ``None`` if not found.
        :rtype: str or None
        """
        uuid_str = str(xs_uuid_str)
        if tl_guid:
            guid = self._xs_uuid_to_sync_guid.get((tl_guid, uuid_str))
            if guid:
                return guid
        for k, v in list(self._xs_uuid_to_sync_guid.items()):
            if isinstance(k, tuple) and len(k) == 2 and k[1] == uuid_str:
                return v
        return None

    def clip_guid_for_media_name(self, media_name: str) -> "str | None":
        """Return the OTIO clip GUID for an xStudio media item by its display name.

        :param media_name: ``media.name`` as returned by xStudio.
        :returns: GUID string, or ``None`` if not found.
        :rtype: str or None
        """
        manager = self.plugin.manager
        if not manager:
            return None
        bn = os.path.basename(media_name)
        stem = os.path.splitext(bn)[0]
        for otio_tl in manager.timelines.values():
            for track in otio_tl.tracks:
                for child in track:
                    if not isinstance(child, otio.schema.Clip):
                        continue
                    cname = child.name or ""
                    if cname == media_name or cname == bn or cname == stem:
                        return child.metadata.get("sync", {}).get("guid")
        return None

    def find_media_for_clip_guid(self, clip_guid: str) -> tuple:
        """Search all synced playlists for a media item matching the OTIO clip GUID.

        Slow path fallback; prefer media_for_sync_guid for O(1) lookup.

        :param clip_guid: Sync GUID of the OTIO media clip.
        :returns: ``(media, aspect_half)`` or ``(None, 0.8889)`` on failure.
        :rtype: tuple
        """
        manager = self.plugin.manager
        if not manager:
            return None, 0.8889
        otio_clip = manager._object_map.get(clip_guid)
        if otio_clip is None:
            _log(f"find_media_for_clip_guid: {clip_guid[:8]} not in object_map")
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
        clip_stem = os.path.splitext(os.path.basename(clip_name or ""))[0]
        clip_uri = ""
        clip_path = ""
        mr = getattr(otio_clip, "media_reference", None)
        if isinstance(mr, otio.schema.ExternalReference):
            clip_uri = mr.target_url or ""
            clip_path = _uri_to_posix_path(clip_uri)

        for playlist, _ in self.plugin._sync_playlists.values():
            try:
                stem_match = None
                uri_match = None
                for media in playlist.media:
                    mname = media.name or ""
                    if mname == clip_name:
                        return media, _aspect(media)
                    if stem_match is None and os.path.splitext(os.path.basename(mname))[0] == clip_stem:
                        stem_match = media
                    if uri_match is None:
                        try:
                            ms = media.media_source()
                            m_uri = str(ms.media_reference.uri())
                            m_path = _uri_to_posix_path(m_uri)
                            if (clip_uri and m_uri == clip_uri) or (clip_path and m_path == clip_path):
                                uri_match = media
                        except Exception:
                            pass
                best = uri_match or stem_match
                if best is not None:
                    return best, _aspect(best)
            except Exception:
                _log_exc("find_media_for_clip_guid: error scanning playlist")
        return None, 0.8889

    # ── bootstrap ─────────────────────────────────────────────────────────────

    def bootstrap_mapping(self, playlist, otio_tl, xs_timeline=None) -> None:
        """Scan playlist.media and match each item to its OTIO clip to build initial mapping."""
        if not playlist or not otio_tl:
            return

        otio_guid_by_path = {}
        otio_guid_by_stem = {}
        for track in otio_tl.tracks:
            if track.kind != otio.schema.TrackKind.Video:
                continue
            for clip in track:
                if not isinstance(clip, otio.schema.Clip):
                    continue
                guid = clip.metadata.get("sync", {}).get("guid")
                if not guid:
                    continue
                mr = clip.media_reference
                if isinstance(mr, otio.schema.ExternalReference) and mr.target_url:
                    posix_path = _uri_to_posix_path(mr.target_url)
                    norm_path = os.path.normpath(posix_path) if posix_path else ""
                    if norm_path:
                        otio_guid_by_path[norm_path] = guid
                        otio_guid_by_path[norm_path.lower()] = guid
                    stem = os.path.splitext(os.path.basename(posix_path))[0]
                    if stem:
                        otio_guid_by_stem[stem] = guid
                        otio_guid_by_stem[stem.lower()] = guid
                elif clip.name:
                    stem = os.path.splitext(os.path.basename(clip.name))[0]
                    if stem:
                        otio_guid_by_stem[stem] = guid
                        otio_guid_by_stem[stem.lower()] = guid

        try:
            media_list = playlist.media
        except Exception:
            _log_exc("bootstrap_mapping: failed to read playlist media")
            return

        referenced_media_uuids = set()
        if xs_timeline:
            try:
                for track in xs_timeline.tracks:
                    if track.is_video:
                        for clip in track.clips:
                            if clip.media:
                                referenced_media_uuids.add(str(clip.media.uuid))
            except Exception:
                _log_exc("bootstrap_mapping: failed to gather referenced media UUIDs")

        guid_to_matched_media = {}
        unmatched = []
        for media in media_list:
            mname = media.name or ""
            m_path = ""
            m_uri = ""
            try:
                ms = media.media_source()
                m_uri = str(ms.media_reference.uri())
                m_path = _uri_to_posix_path(m_uri)
            except Exception:
                pass

            guid = None
            if m_path:
                norm_m_path = os.path.normpath(m_path)
                guid = otio_guid_by_path.get(norm_m_path) or otio_guid_by_path.get(norm_m_path.lower())
            if not guid and mname:
                stem = os.path.splitext(os.path.basename(mname))[0]
                guid = otio_guid_by_stem.get(stem) or otio_guid_by_stem.get(stem.lower())
            if not guid and m_path:
                stem = os.path.splitext(os.path.basename(m_path))[0]
                guid = otio_guid_by_stem.get(stem) or otio_guid_by_stem.get(stem.lower())

            if guid:
                guid_to_matched_media.setdefault(guid, []).append(media)
            else:
                unmatched.append(f"name={mname!r} path={m_path!r}")

        for guid, matched_list in guid_to_matched_media.items():
            if len(matched_list) == 1:
                self.register(matched_list[0], guid)
            else:
                keepers = [m for m in matched_list if str(m.uuid) in referenced_media_uuids]
                others = [m for m in matched_list if str(m.uuid) not in referenced_media_uuids]
                if len(keepers) == 1:
                    self.register(keepers[0], guid)
                    for other in others:
                        try:
                            _other_name = other.name
                        except Exception:
                            _other_name = str(getattr(other, "uuid", "???"))
                        try:
                            playlist.remove_media(other)
                            _log(f"Removed duplicate unreferenced media item {_other_name!r} for guid {guid[:8]}")
                        except Exception:
                            _log_exc(f"Failed to remove duplicate media {_other_name!r}")
                else:
                    _log(f"WARNING: duplicate media items for guid {guid[:8]} referenced count={len(keepers)}; keeping both")
                    self.register(matched_list[0], guid)

    def prepare_otio_for_load(self, otio_tl: "otio.schema.Timeline") -> "otio.schema.Timeline":
        """Return a copy of otio_tl with clip URLs rewritten to matched xStudio media URIs.

        Prevents load_otio from importing duplicate media items when the OTIO
        clip URL differs slightly from the already-imported media URI.
        """
        otio_copy = otio_tl.deepcopy()
        # Strip the synthetic annotations-only track injected by RV's
        # _stamp_sync_identity.  That track has a sync guid and no
        # ExternalReference clips; it is not a real media track and must not
        # reach load_otio (it would show up as a spurious video track in
        # xStudio's OTIO export).  We distinguish it from any legitimate
        # user-created track by requiring BOTH the sync marker AND the absence
        # of ExternalReference clips — a real track with that name would have
        # actual media clips.
        def _is_injected_ann_track(t):
            if not t.metadata.get("sync", {}).get("guid"):
                return False
            for child in t:
                if isinstance(child, otio.schema.Clip) and isinstance(
                    getattr(child, "media_reference", None),
                    otio.schema.ExternalReference,
                ):
                    return False
            return True

        otio_copy.tracks[:] = [
            t for t in otio_copy.tracks if not _is_injected_ann_track(t)
        ]
        for track in otio_copy.tracks:
            if track.kind != otio.schema.TrackKind.Video:
                continue
            for clip in track:
                if not isinstance(clip, otio.schema.Clip):
                    continue
                clip_guid = clip.metadata.get("sync", {}).get("guid")
                if clip_guid:
                    media_obj, _ = self.media_for_sync_guid(clip_guid)
                    if media_obj:
                        try:
                            ms = media_obj.media_source()
                            m_uri = str(ms.media_reference.uri())
                            if m_uri:
                                if not clip.media_reference:
                                    clip.media_reference = otio.schema.ExternalReference()
                                clip.media_reference.target_url = m_uri
                        except Exception:
                            pass
        return otio_copy
