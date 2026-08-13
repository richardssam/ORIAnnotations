#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""``on_playhead_attribute_changed`` must survive a stale playhead actor.

This callback runs on xStudio's **message-dispatch thread**, not the poll
thread. Anything it raises propagates into ``xstudio.api.module.message_handler``
and takes the dispatcher's handler with it, so an exception here is not a
missed frame — it is that peer's event handling stopping.

``attr.name`` is the hazard: not a local field but a synchronous
``request_receive`` round-trip bounded by the connection's 100 s default. The
failure was observed live on 2026-08-13 12:25:10 while testing two drivers. A
peer's playhead went stale mid-scrub, ``attr.name`` raised ``TimeoutError`` out
of this frame, and the peer spent the following three minutes heartbeating
normally — so it never aged out, and it kept the session's host seat — while
every playhead read timed out and it broadcast nothing at all.

Two properties are pinned here:

1. A raising ``attr.name`` is contained, and nothing is broadcast from it.
2. The name is read **once** per event. It was read three times (two branch
   tests and the log f-string, which is evaluated whether or not logging is
   enabled), which is three actor round-trips per rendered frame while
   scrubbing — on the thread that dispatches every other xStudio event.

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


class FakeQueue:
    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)


class FakeConnection:
    """``bounded_timeout`` writes this attribute, so it must be settable."""

    def __init__(self) -> None:
        self.default_timeout_ms = 100_000


class FakePlayhead:
    def __init__(self, position: float = 12.0, playing: bool = False) -> None:
        self.position = position
        self.playing = playing
        self.frame_rate = types.SimpleNamespace(fps=lambda: 24.0)
        self._attrs: dict = {}

    def get_attribute(self, name):
        return self._attrs.get(name, "Play Once")

    def set_attribute(self, name, value) -> None:
        self._attrs[name] = value


class FakePlugin:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.manager = types.SimpleNamespace(
            status=STATE_SYNCED, active_timeline_guid="tl-1"
        )
        self._sync_playlists: dict = {}
        self._cmd_queue = FakeQueue()
        self.active_playhead = FakePlayhead()

    def stamp_remote_apply(self, channel: str) -> None:
        pass

    def claim_lease(self, channel: str) -> None:
        pass


class StaleAttr:
    """An attribute whose actor no longer answers."""

    def __init__(self) -> None:
        self.reads = 0

    @property
    def name(self):
        self.reads += 1
        raise TimeoutError("Dequeue timeout")


class CountingAttr:
    """A healthy attribute that counts how many round-trips it is charged."""

    def __init__(self, value: str) -> None:
        self._value = value
        self.reads = 0

    @property
    def name(self):
        self.reads += 1
        return self._value


def test_a_stale_attribute_does_not_raise_into_the_dispatcher():
    ctrl = PlaybackSyncController(FakePlugin())
    attr = StaleAttr()

    # No pytest.raises: the whole point is that this returns normally. If it
    # propagates, xStudio's message handler dies with it.
    ctrl.on_playhead_attribute_changed(attr, role=0)

    assert attr.reads == 1, "a failed read must not be retried per branch"
    assert ctrl.plugin._cmd_queue.items == [], "nothing may be broadcast from an unreadable event"


def test_the_connection_timeout_is_restored_after_a_failed_read():
    """``bounded_timeout`` lowers a *shared* connection attribute. Leaking the
    lowered value would silently bound every later call — including the heavy
    ones (``load_otio``) that deliberately keep the long default."""
    plugin = FakePlugin()
    ctrl = PlaybackSyncController(plugin)

    ctrl.on_playhead_attribute_changed(StaleAttr(), role=0)

    assert plugin.connection.default_timeout_ms == 100_000


def test_the_attribute_name_is_read_once_per_event():
    """Three reads per event, at one actor round-trip each, on the thread that
    dispatches every other xStudio event, once per rendered frame."""
    ctrl = PlaybackSyncController(FakePlugin())
    attr = CountingAttr("Logical Frame")

    ctrl.on_playhead_attribute_changed(attr, role=0)

    assert attr.reads == 1


def test_an_uninteresting_attribute_is_still_read_once():
    ctrl = PlaybackSyncController(FakePlugin())
    attr = CountingAttr("Velocity")

    ctrl.on_playhead_attribute_changed(attr, role=0)

    assert attr.reads == 1
    assert ctrl.plugin._cmd_queue.items == []


def test_a_readable_frame_event_still_broadcasts():
    """The guard must not swallow ordinary traffic — a test that only proved
    nothing was queued would pass against a controller that had stopped
    working entirely."""
    ctrl = PlaybackSyncController(FakePlugin())
    ctrl._cur_view_mode = "sequence"
    ctrl._cur_clip_guid = None

    ctrl.on_playhead_attribute_changed(CountingAttr("Logical Frame"), role=0)

    queued = [c for c, _ in ctrl.plugin._cmd_queue.items]
    assert "broadcast_playback_state" in queued
