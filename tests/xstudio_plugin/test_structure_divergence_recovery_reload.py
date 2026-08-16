#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Tests for reload_existing_timelines()'s flat-playlist reconciliation.

structure-divergence-recovery, design D2a: ``do_load_timelines()`` skips any
timeline guid this peer already has a playlist for, which is exactly the
timeline a diverged peer needs reconciled — it already held that playlist,
that is how it made the disallowed local edit. ``reload_existing_timelines()``
is the recovery-only counterpart. The proposal's own motivating incident
(``broadcast_remove_child: suppressed`` immediately followed by ``flat
playlist deleted media: 'seq_A' removed``) is a flat playlist, so this is the
primary case, not an edge one.

Requires the xStudio Python bindings — see
test_sequence_reconciliation_convergence.py for why and how to run this file.
"""
import os
import sys
import types

import opentimelineio as otio

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_repo_root, "python"))
sys.path.insert(0, os.path.join(_repo_root, "xstudio_plugin"))

from otio_sync_core.manager import SyncManager, STATE_SYNCED  # noqa: E402

_ori_sync_dir = os.path.join(_repo_root, "xstudio_plugin", "ori_sync")
_ori_sync_stub = types.ModuleType("ori_sync")
_ori_sync_stub.__path__ = [_ori_sync_dir]
sys.modules.setdefault("ori_sync", _ori_sync_stub)

from ori_sync import structure_sync  # noqa: E402
from ori_sync.media_map import MediaMapController  # noqa: E402

StructureSyncController = structure_sync.StructureSyncController


class FakeMedia:
    _next_id = 0

    def __init__(self, name: str):
        FakeMedia._next_id += 1
        self.uuid = f"media-uuid-{FakeMedia._next_id}"
        self.name = name


class FakeXsFlatPlaylist:
    """Minimal xStudio flat (media-bin) Playlist stand-in."""

    def __init__(self):
        self.media: list = []

    def add_media(self, path: str):
        m = FakeMedia(os.path.basename(path))
        self.media.append(m)
        return m

    def remove_media(self, media_obj) -> None:
        self.media = [m for m in self.media if m is not media_obj]

    def move_media(self, media_obj, before=None) -> None:
        self.media = [m for m in self.media if m is not media_obj]
        if before is None:
            self.media.append(media_obj)
        else:
            idx = self.media.index(before)
            self.media.insert(idx, media_obj)


class FakeNetwork:
    def send_payload(self, payload):
        pass


class FakePlugin:
    def __init__(self):
        self.manager = SyncManager(session_id="test", self_guid="peer-a", network=FakeNetwork())
        self.manager.status = STATE_SYNCED
        self.media = MediaMapController(self)
        self._sync_playlists: dict = {}

    def stamp_remote_apply(self, channel: str) -> None:
        pass

    def claim_lease(self, channel: str) -> None:
        pass


def _clip(name: str, url: str, guid: str) -> otio.schema.Clip:
    c = otio.schema.Clip(
        name=name,
        media_reference=otio.schema.ExternalReference(target_url=url),
    )
    c.metadata["sync"] = {"guid": guid}
    return c


def _flat_timeline(tl_guid: str, clip_names_urls_guids) -> otio.schema.Timeline:
    tl = otio.schema.Timeline(name="Bin")
    tl.metadata["sync"] = {"guid": tl_guid}
    tl.metadata["xs_flat_playlist"] = True
    tl.tracks = otio.schema.Stack("tracks")
    video = otio.schema.Track(name="Media", kind=otio.schema.TrackKind.Video)
    video.metadata["sync"] = {"guid": f"{tl_guid}-track"}
    for name, url, guid in clip_names_urls_guids:
        video.append(_clip(name, url, guid))
    tl.tracks.append(video)
    return tl


def test_reload_restores_a_locally_deleted_clip_and_removes_a_local_addition():
    """The proposal's exact incident, generalised: a locally-deleted clip the
    recovered structure still has must come back; a locally-added clip the
    recovered structure never had must go.
    """
    plugin = FakePlugin()
    structure = StructureSyncController(plugin)
    tl_guid = "flat-1"

    # Recovered (post-apply_snapshot) structure: clip_a and clip_b.
    recovered = _flat_timeline(
        tl_guid,
        [
            ("clip_a.mov", "file:///media/clip_a.mov", "guid-a"),
            ("clip_b.mov", "file:///media/clip_b.mov", "guid-b"),
        ],
    )
    plugin.manager.register_timeline(recovered)

    xs_playlist = FakeXsFlatPlaylist()
    media_a = xs_playlist.add_media("/media/clip_a.mov")
    media_c = xs_playlist.add_media("/media/clip_c.mov")  # locally added, refused, phantom
    # register() + _flat_clip_to_media, matching what the live insert path
    # (poll_flat_playlist_new_media / apply_flat_playlist_insert) always does
    # for a clip xStudio's media map is expected to know about.
    plugin.media.register(media_a, "guid-a", tl_guid)
    plugin.media.register(media_c, "guid-c", tl_guid)
    plugin.media._flat_clip_to_media["guid-a"] = media_a
    plugin.media._flat_clip_to_media["guid-c"] = media_c

    plugin._sync_playlists[tl_guid] = (xs_playlist, None)

    structure.reload_existing_timelines()

    final_names = {m.name for m in xs_playlist.media}
    assert final_names == {"clip_a.mov", "clip_b.mov"}, (
        f"expected the deleted clip restored and the phantom addition removed, got {final_names}"
    )


def test_reload_reorders_membership_that_already_matches():
    plugin = FakePlugin()
    structure = StructureSyncController(plugin)
    tl_guid = "flat-2"

    recovered = _flat_timeline(
        tl_guid,
        [
            ("clip_a.mov", "file:///media/clip_a.mov", "guid-a"),
            ("clip_b.mov", "file:///media/clip_b.mov", "guid-b"),
        ],
    )
    plugin.manager.register_timeline(recovered)

    xs_playlist = FakeXsFlatPlaylist()
    # Locally reordered: b before a, membership already matches the recovered set.
    media_b = xs_playlist.add_media("/media/clip_b.mov")
    media_a = xs_playlist.add_media("/media/clip_a.mov")
    plugin.media.register(media_a, "guid-a", tl_guid)
    plugin.media.register(media_b, "guid-b", tl_guid)

    plugin._sync_playlists[tl_guid] = (xs_playlist, None)

    structure.reload_existing_timelines()

    assert [m.name for m in xs_playlist.media] == ["clip_a.mov", "clip_b.mov"]


def test_reload_skips_a_timeline_this_peer_does_not_have_a_playlist_for():
    """do_load_timelines() owns that case; reload_existing_timelines() must not."""
    plugin = FakePlugin()
    structure = StructureSyncController(plugin)
    tl_guid = "flat-3"
    recovered = _flat_timeline(tl_guid, [("clip_a.mov", "file:///media/clip_a.mov", "guid-a")])
    plugin.manager.register_timeline(recovered)
    # Deliberately not added to plugin._sync_playlists.

    structure.reload_existing_timelines()  # must not raise


if __name__ == "__main__":
    test_reload_restores_a_locally_deleted_clip_and_removes_a_local_addition()
    print("test_reload_restores_a_locally_deleted_clip_and_removes_a_local_addition: PASS")
    test_reload_reorders_membership_that_already_matches()
    print("test_reload_reorders_membership_that_already_matches: PASS")
    test_reload_skips_a_timeline_this_peer_does_not_have_a_playlist_for()
    print("test_reload_skips_a_timeline_this_peer_does_not_have_a_playlist_for: PASS")
