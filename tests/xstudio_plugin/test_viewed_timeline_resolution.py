#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""A flat playlist's own timeline resolves to the playlist's published guid.

xStudio gives every ``Playlist`` an internal ``Timeline`` with its own container
uuid, and the viewport reports *that* Timeline — not the Playlist — once
playback starts. The content is published under the Playlist's guid, so the
viewport hands back the wrong one of the two uuids the same content answers to,
and the raw local-only uuid went on the wire as ``timeline_guid``. Peers have
nothing to match it against: OpenRV logs "MIRROR FAILED … not guessing" and
xStudio logs "mismatched timeline_guid — ignoring (not playing)", both keeping
their previous view. The session simply stops following, indefinitely.

Observed 2026-08-12 and again 2026-08-13 13:51:42:

    host   13:51:42.225  [VIEW] viewing Timeline 91206382 which is NOT a
                         published sync timeline. Known sync timelines: ['53c1596d']
    client 13:52:43.569  RECV playback state: mismatched timeline_guid
                         (local=53c1596d, target=897fc17c, incoming=91206382)

It presented as a *publishing* failure, and the structure scan was instrumented
on the theory that it could not see the container. The census disproved that —
``'Added Media'(53c1596d): 0 timeline(s)`` is correct, because a playlist's own
timeline is not one of its child containers. Nothing was missing from the
session; only the resolution was wrong.

Requires the xStudio Python bindings; run with the xStudio-bundled interpreter
(``./run_tests_xstudio.sh``).
"""
import os
import sys
import types

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_repo_root, "python"))
sys.path.insert(0, os.path.join(_repo_root, "xstudio_plugin"))

from otio_sync_core.manager import STATE_SYNCED  # noqa: E402

_ori_sync_dir = os.path.join(_repo_root, "xstudio_plugin", "ori_sync")
_ori_sync_stub = types.ModuleType("ori_sync")
_ori_sync_stub.__path__ = [_ori_sync_dir]
sys.modules.setdefault("ori_sync", _ori_sync_stub)

from ori_sync import playback_sync  # noqa: E402

PlaybackSyncController = playback_sync.PlaybackSyncController

PLAYLIST_UUID = "53c1596d-0000-0000-0000-000000000001"
PLAYLIST_TIMELINE_UUID = "91206382-0000-0000-0000-000000000002"
FOREIGN_TIMELINE_UUID = "cb2e25ae-0000-0000-0000-000000000003"
SEQUENCE_UUID = "79a59f7a-0000-0000-0000-000000000004"


class FakeConnection:
    def __init__(self, viewed_uuid) -> None:
        self.default_timeout_ms = 100_000
        self.api = types.SimpleNamespace(
            session=types.SimpleNamespace(remote=object())
        )
        self._viewed_uuid = viewed_uuid

    def request_receive_timeout(self, timeout_ms, actor, atom):
        return [types.SimpleNamespace(uuid=self._viewed_uuid, actor=object())]


class FakePlugin:
    def __init__(self, viewed_uuid) -> None:
        self.connection = FakeConnection(viewed_uuid)
        self.manager = types.SimpleNamespace(
            status=STATE_SYNCED,
            active_timeline_guid="tl-1",
            claim_category=lambda *a, **k: None,
            owns_visibility=lambda *a, **k: True,
        )
        self._sync_playlists: dict = {}
        self._cmd_queue = types.SimpleNamespace(put=lambda item: None)
        self.active_playhead = None


def _install_fakes(monkeypatch, *, container_type, parent_uuid):
    """Replace the two xStudio classes the resolver constructs.

    ``Container`` reports what kind of thing the viewport returned; ``Timeline``
    answers ``parent_playlist``, the mapping the fix relies on.
    """
    class FakeContainer:
        def __init__(self, connection, actor):
            self.type = container_type

    class FakeTimeline:
        def __init__(self, connection, actor, uuid):
            pass

        @property
        def parent_playlist(self):
            if parent_uuid is None:
                return None
            return types.SimpleNamespace(uuid=parent_uuid)

    monkeypatch.setattr(playback_sync, "Container", FakeContainer)
    monkeypatch.setattr(playback_sync, "Timeline", FakeTimeline)


def _controller(viewed_uuid):
    return PlaybackSyncController(FakePlugin(viewed_uuid))


def test_a_flat_playlists_own_timeline_resolves_to_the_playlist_guid(monkeypatch):
    _install_fakes(monkeypatch, container_type="Timeline", parent_uuid=PLAYLIST_UUID)
    ctrl = _controller(PLAYLIST_TIMELINE_UUID)
    # A flat playlist is stored with no sequence timeline: (playlist, None).
    ctrl.plugin._sync_playlists = {
        "guid-flat": (types.SimpleNamespace(uuid=PLAYLIST_UUID), None)
    }

    assert ctrl.get_local_viewed_timeline_guid() == "guid-flat"


def test_a_published_sequence_timeline_still_matches_by_uuid(monkeypatch):
    """The pre-existing path must keep winning, and must not be re-routed
    through parent_playlist — a sequence's guid is its own, not its parent's."""
    _install_fakes(monkeypatch, container_type="Timeline", parent_uuid=PLAYLIST_UUID)
    ctrl = _controller(SEQUENCE_UUID)
    ctrl.plugin._sync_playlists = {
        "guid-seq": (
            types.SimpleNamespace(uuid=PLAYLIST_UUID),
            types.SimpleNamespace(uuid=SEQUENCE_UUID),
        )
    }

    assert ctrl.get_local_viewed_timeline_guid() == "guid-seq"


def test_a_genuinely_unpublished_timeline_still_returns_its_raw_uuid(monkeypatch):
    """The fallback must survive: a Timeline belonging to no published playlist
    is still unresolvable, and the [VIEW] diagnostic still has a job."""
    _install_fakes(monkeypatch, container_type="Timeline", parent_uuid="some-other-pl")
    ctrl = _controller(FOREIGN_TIMELINE_UUID)
    ctrl.plugin._sync_playlists = {
        "guid-flat": (types.SimpleNamespace(uuid=PLAYLIST_UUID), None)
    }

    assert ctrl.get_local_viewed_timeline_guid() == FOREIGN_TIMELINE_UUID


def test_an_unreadable_parent_falls_back_rather_than_blocking(monkeypatch):
    """A stale actor must not cost the caller its answer — this runs on the
    poll thread, on every scrub."""
    class FakeContainer:
        def __init__(self, connection, actor):
            self.type = "Timeline"

    class ExplodingTimeline:
        def __init__(self, connection, actor, uuid):
            pass

        @property
        def parent_playlist(self):
            raise TimeoutError("Dequeue timeout")

    monkeypatch.setattr(playback_sync, "Container", FakeContainer)
    monkeypatch.setattr(playback_sync, "Timeline", ExplodingTimeline)

    ctrl = _controller(PLAYLIST_TIMELINE_UUID)
    ctrl.plugin._sync_playlists = {
        "guid-flat": (types.SimpleNamespace(uuid=PLAYLIST_UUID), None)
    }

    assert ctrl.get_local_viewed_timeline_guid() == PLAYLIST_TIMELINE_UUID
    assert ctrl.plugin.connection.default_timeout_ms == 100_000


def test_viewing_the_playlist_itself_is_unchanged(monkeypatch):
    """The Playlist branch already handled the flat case; the new lookup must
    not disturb it."""
    _install_fakes(monkeypatch, container_type="Playlist", parent_uuid=None)
    ctrl = _controller(PLAYLIST_UUID)
    ctrl.plugin._sync_playlists = {
        "guid-flat": (types.SimpleNamespace(uuid=PLAYLIST_UUID), None)
    }

    assert ctrl.get_local_viewed_timeline_guid() == "guid-flat"
