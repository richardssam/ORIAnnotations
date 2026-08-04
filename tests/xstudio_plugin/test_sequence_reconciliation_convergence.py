#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Regression tests for fix-sequence-track-reconciliation.

Property under test: a ``sync_container`` pass over an unchanged xStudio
sequence timeline must broadcast nothing, and a rebuild triggered by a
direct-drag addition (which lands on a separate xStudio ``Dropped`` track)
must converge within one pass rather than looping.

Requires the xStudio Python bindings, because
``xstudio_plugin.ori_sync.structure_sync`` imports ``xstudio.core`` /
``xstudio.api.session.playlist.timeline`` at module level. It does **not**
require a live xStudio session: xStudio's timeline/playlist objects are
faked below. Run with the xStudio-bundled interpreter, e.g.::

    /path/to/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 -m pytest \\
        tests/xstudio_plugin/test_sequence_reconciliation_convergence.py -v

Lives under the repo-root ``tests/`` tree, not inside ``xstudio_plugin/``
itself — xStudio's plugin loader enumerates every subdirectory of
``xstudio_plugin/`` as a plugin candidate, and a non-plugin package there
(e.g. a ``tests/`` dir) makes it log a "Failed to load plugin" error on
every launch.
"""
import os
import sys
import types

import opentimelineio as otio

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_repo_root, "python"))
sys.path.insert(0, os.path.join(_repo_root, "xstudio_plugin"))

from otio_sync_core.manager import SyncManager, STATE_SYNCED  # noqa: E402

# Register a lightweight 'ori_sync' package stub in sys.modules so the real
# ori_sync/__init__.py never runs — it imports ori_sync_plugin, which needs
# `pika` (RabbitMQ), not part of xStudio's bundled Python. structure_sync.py's
# `from .utils import ...` only needs a valid package context to resolve, not
# the real package init.
_ori_sync_dir = os.path.join(_repo_root, "xstudio_plugin", "ori_sync")
_ori_sync_stub = types.ModuleType("ori_sync")
_ori_sync_stub.__path__ = [_ori_sync_dir]
sys.modules.setdefault("ori_sync", _ori_sync_stub)

from ori_sync import structure_sync  # noqa: E402
from ori_sync import timeline_build  # noqa: E402

StructureSyncController = structure_sync.StructureSyncController
TimelineBuildController = timeline_build.TimelineBuildController


def _clip(name: str, url: str, start: float, duration: float, rate: float = 24.0) -> otio.schema.Clip:
    return otio.schema.Clip(
        name=name,
        media_reference=otio.schema.ExternalReference(
            target_url=url,
            available_range=otio.opentime.TimeRange(
                otio.opentime.RationalTime(0, rate),
                otio.opentime.RationalTime(10000, rate),
            ),
        ),
        source_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(start, rate),
            otio.opentime.RationalTime(duration, rate),
        ),
    )


class FakeXsTimeline:
    """Minimal xStudio Timeline stand-in.

    Fixed ``to_otio_string()`` output and no live ``tracks``/``item_prop`` —
    this models a peer that never durably stamps sync guids back into
    xStudio, forcing URL/stem fallback matching every pass. That is the
    harder case for identity matching, not the easier one.
    """

    def __init__(self, uuid: str, otio_str: str) -> None:
        self.uuid = uuid
        self._otio_str = otio_str

    def to_otio_string(self) -> str:
        return self._otio_str


class FakeXsPlaylist:
    def __init__(self, name: str = "Test Playlist") -> None:
        self.uuid = "fake-playlist-uuid"
        self.name = name
        self.media: list = []


class FakeConnection:
    default_timeout_ms = 2000


class FakeNetwork:
    """Records every payload's event name, in send order."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_payload(self, payload: dict) -> None:
        event = payload.get("payload", {}).get("command", {}).get("event")
        self.sent.append(event)


class FakePlugin:
    def __init__(self) -> None:
        self.manager = SyncManager(session_id="test", self_guid="peer-a", network=FakeNetwork())
        self.manager.status = STATE_SYNCED
        self.manager.is_master = True
        self.connection = FakeConnection()
        self.media = types.SimpleNamespace(register=lambda *a, **k: None)
        self._sync_playlists: dict = {}
        self.builder = None  # set by _build_plugin(), needs the back-reference


def _build_plugin() -> FakePlugin:
    plugin = FakePlugin()
    plugin.builder = TimelineBuildController(plugin)
    return plugin


def _make_two_track_xs_otio_string(tl_guid: str) -> str:
    """The exact xStudio-native split a direct drag produces: the original
    clip on 'Video Track', the newly dragged clip on a separate 'Dropped'
    track — see design.md's addendum to fix-sequence-track-reconciliation.
    """
    tl = otio.schema.Timeline(name="Test Sequence")
    tl.metadata.setdefault("sync", {})["guid"] = tl_guid
    video = otio.schema.Track(name="Video Track", kind=otio.schema.TrackKind.Video)
    video.append(_clip("clip_a.mov", "file:///media/clip_a.mov", 0, 100))
    dropped = otio.schema.Track(name="Dropped", kind=otio.schema.TrackKind.Video)
    dropped.append(_clip("clip_b.mov", "file:///media/clip_b.mov", 100, 50))
    tl.tracks.append(video)
    tl.tracks.append(dropped)
    return otio.adapters.write_to_string(tl, "otio_json")


def test_direct_drag_rebuild_converges_in_one_pass() -> None:
    """Reproduces the 2026-08-03 live-test regression: a clip dragged
    directly onto the sequence track (landing on xStudio's own 'Dropped'
    track) must trigger exactly one rebuild, after which repeated passes
    against the same unchanged xStudio state broadcast nothing.
    """
    plugin = _build_plugin()
    structure = StructureSyncController(plugin)
    network = plugin.manager.network

    tl_guid = "11111111-1111-1111-1111-111111111111"
    xs_otio_str = _make_two_track_xs_otio_string(tl_guid)
    xs_tl = FakeXsTimeline(tl_guid, xs_otio_str)
    xs_playlist = FakeXsPlaylist()

    # Seed the manager with the *before* state: just clip_a on a single
    # video track, as if clip_b had not yet been dragged in.
    before_tl = otio.schema.Timeline(name="Test Sequence")
    before_tl.metadata.setdefault("sync", {})["guid"] = tl_guid
    before_video = otio.schema.Track(name="Video Track", kind=otio.schema.TrackKind.Video)
    before_video.append(_clip("clip_a.mov", "file:///media/clip_a.mov", 0, 100))
    before_tl.tracks.append(before_video)
    plugin.manager.register_timeline(before_tl)

    structure._xs_sequence_playlists[tl_guid] = (xs_playlist, xs_tl, {"clip_a.mov"})
    structure._reset_sequence_broadcast_record(tl_guid, before_tl)
    plugin._sync_playlists[tl_guid] = (xs_playlist, xs_tl)

    # Pass 1: detects the drag, converges via exactly one rebuild.
    structure.execute_sync_container(tl_guid)
    assert network.sent.count("REPLACE_TIMELINE") == 1, (
        f"expected exactly one REPLACE_TIMELINE to carry the drag, got: {network.sent}"
    )
    assert network.sent.count("INSERT_CHILD") == 0, (
        f"incremental path should have sat out this pass in favour of the rebuild, got: {network.sent}"
    )
    settled = list(network.sent)

    # Passes 2-5: xStudio state is unchanged. This is the exact regression —
    # the old single-track fingerprint re-triggered a rebuild every pass
    # because the manager's own read could never match xStudio's
    # multi-track state, even though nothing was actually changing.
    for _ in range(4):
        structure.execute_sync_container(tl_guid)

    assert network.sent == settled, (
        f"reconciliation did not converge — unchanged xStudio state kept broadcasting: "
        f"{network.sent[len(settled):]}"
    )


def test_idle_pass_over_converged_sequence_broadcasts_nothing() -> None:
    """The pure §5.3 property: a single-track sequence with no changes
    broadcasts nothing across repeated passes.
    """
    plugin = _build_plugin()
    structure = StructureSyncController(plugin)
    network = plugin.manager.network

    tl_guid = "22222222-2222-2222-2222-222222222222"
    tl = otio.schema.Timeline(name="Idle Sequence")
    tl.metadata.setdefault("sync", {})["guid"] = tl_guid
    video = otio.schema.Track(name="Video Track", kind=otio.schema.TrackKind.Video)
    video.append(_clip("clip_a.mov", "file:///media/clip_a.mov", 0, 100))
    tl.tracks.append(video)
    plugin.manager.register_timeline(tl)

    xs_otio_str = otio.adapters.write_to_string(tl, "otio_json")
    xs_tl = FakeXsTimeline(tl_guid, xs_otio_str)
    xs_playlist = FakeXsPlaylist()

    structure._xs_sequence_playlists[tl_guid] = (xs_playlist, xs_tl, {"clip_a.mov"})
    structure._reset_sequence_broadcast_record(tl_guid, tl)
    plugin._sync_playlists[tl_guid] = (xs_playlist, xs_tl)

    for _ in range(10):
        structure.execute_sync_container(tl_guid)

    assert network.sent == [], f"idle session broadcast structural messages: {network.sent}"


if __name__ == "__main__":
    test_direct_drag_rebuild_converges_in_one_pass()
    print("test_direct_drag_rebuild_converges_in_one_pass: PASS")
    test_idle_pass_over_converged_sequence_broadcasts_nothing()
    print("test_idle_pass_over_converged_sequence_broadcasts_nothing: PASS")
