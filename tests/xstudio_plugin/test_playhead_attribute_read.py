#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Reading a playhead attribute's name must not be able to deafen a peer.

``attr.name`` is the hazard: not a local field but a synchronous
``request_receive`` round-trip bounded by the connection's 100 s default. The
failure was observed live on 2026-08-13 12:25:10 while testing two drivers. A
peer's playhead went stale mid-scrub, ``attr.name`` raised ``TimeoutError`` out
of ``on_playhead_attribute_changed``, and since that callback runs on xStudio's
**message-dispatch thread** the exception propagated into
``xstudio.api.module.message_handler`` and took the dispatcher's handler with
it. The peer spent the following three minutes heartbeating normally — so it
never aged out, and it kept the session's host seat — while every playhead read
timed out and it broadcast nothing at all.

That was originally contained by reading the name *once*, bounded and guarded.
Under ``poll-thread-owns-actor-io`` it is contained more thoroughly: the
callback thread does not read the name at all. ``attr.uuid`` is a local field
(``ModuleAttribute.__init__``), and the uuid→name map is seeded from the
playhead wrapper's own ``attrs_by_name_``, whose names were already paid for
when the wrapper was built. So the steady-state cost is zero round-trips per
event, on a thread that could not afford one.

The read still exists for an attribute the map has not seen — but on the poll
thread, where a timeout costs a poll interval instead of the dispatcher. The
properties pinned here follow that move:

1. The callback thread reads nothing, ever — see
   ``test_callback_thread_no_actor_io.py`` for the broader version.
2. The poll-thread fallback reads the name **once**, contains a failure, and
   restores the shared connection timeout.

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

    def kinds(self) -> list:
        return [cmd for cmd, _ in self.items]


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
            status=STATE_SYNCED,
            active_timeline_guid="tl-1",
            claim_category=lambda *a, **k: None,
            owns_visibility=lambda *a, **k: True,
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

    def __init__(self, uuid: str = "attr-stale") -> None:
        self.uuid = uuid
        self.reads = 0

    @property
    def name(self):
        self.reads += 1
        raise TimeoutError("Dequeue timeout")


class CountingAttr:
    """A healthy attribute that counts how many round-trips it is charged."""

    def __init__(self, value: str, uuid: str = "attr-1") -> None:
        self._value = value
        self.uuid = uuid
        self.reads = 0

    @property
    def name(self):
        self.reads += 1
        return self._value


def _seeded(ctrl, attr, name):
    """Register *attr* under *name*, as ``_seed_attr_names`` does at adoption."""
    ctrl._seed_attr_names(types.SimpleNamespace(attrs_by_name_={name: attr}))


# ── the callback thread reads nothing ────────────────────────────────────────


def test_a_known_attribute_costs_no_round_trip_at_all():
    """The steady state. Previously one read per event, ~60 times a second
    while scrubbing, on the thread that dispatches every other xStudio event.
    """
    ctrl = PlaybackSyncController(FakePlugin())
    attr = CountingAttr("Logical Frame")
    _seeded(ctrl, attr, "Logical Frame")

    ctrl.on_playhead_attribute_changed(attr, role=0)

    assert attr.reads == 0
    assert ctrl.plugin._cmd_queue.kinds() == ["playhead_sample"]


def test_a_stale_attribute_does_not_raise_into_the_dispatcher():
    """The 2026-08-13 incident, at the point it entered the process. A stale
    actor cannot raise here any more because nothing here asks it anything —
    the name comes from the local map, and a miss is deferred, not read.
    """
    ctrl = PlaybackSyncController(FakePlugin())
    attr = StaleAttr()

    # No pytest.raises: the whole point is that this returns normally. If it
    # propagates, xStudio's message handler dies with it.
    ctrl.on_playhead_attribute_changed(attr, role=0)

    assert attr.reads == 0, "the callback thread must not touch the actor at all"
    assert ctrl.plugin._cmd_queue.kinds() == ["playhead_attr_name"], (
        "an unknown attribute is handed to the poll thread, not read here"
    )


def test_an_uninteresting_attribute_is_dropped_without_a_read():
    ctrl = PlaybackSyncController(FakePlugin())
    attr = CountingAttr("Velocity")
    _seeded(ctrl, attr, "Velocity")

    ctrl.on_playhead_attribute_changed(attr, role=0)

    assert attr.reads == 0
    assert ctrl.plugin._cmd_queue.items == []


# ── the poll-thread fallback still has to be safe ────────────────────────────


def test_the_fallback_reads_the_name_once_per_event():
    """Once, not three times: it used to be read by two branch tests and the
    log f-string, which is evaluated whether or not logging is enabled.
    """
    ctrl = PlaybackSyncController(FakePlugin())
    attr = CountingAttr("Logical Frame")

    ctrl.resolve_playhead_attr_name(attr)

    assert attr.reads == 1
    assert ctrl._attr_name_by_uuid[attr.uuid] == "Logical Frame", (
        "the read must be cached, or every later event repeats it"
    )


def test_a_stale_attribute_is_contained_on_the_poll_thread_too():
    """Deferring moves the timeout off the dispatcher; it does not make one
    harmless. A raise here would abort the poll loop's whole drain.
    """
    ctrl = PlaybackSyncController(FakePlugin())
    attr = StaleAttr()

    ctrl.resolve_playhead_attr_name(attr)

    assert attr.reads == 1, "a failed read must not be retried per branch"
    assert ctrl.plugin._cmd_queue.items == [], (
        "nothing may be broadcast from an unreadable event"
    )
    assert attr.uuid not in ctrl._attr_name_by_uuid, (
        "a failed read must not poison the cache"
    )


def test_the_connection_timeout_is_restored_after_a_failed_read():
    """``bounded_timeout`` lowers a *shared* connection attribute — process-wide,
    not thread-local, which is half of why this work moved threads at all.
    Leaking the lowered value would silently bound every later call, including
    the heavy ones (``load_otio``) that deliberately keep the long default.
    """
    plugin = FakePlugin()
    ctrl = PlaybackSyncController(plugin)

    ctrl.resolve_playhead_attr_name(StaleAttr())

    assert plugin.connection.default_timeout_ms == 100_000


def test_a_readable_frame_event_still_broadcasts():
    """The guard must not swallow ordinary traffic — a test that only proved
    nothing was queued would pass against a controller that had stopped
    working entirely. End to end now: handler queues a sample, poll thread
    reads the playhead and broadcasts.
    """
    ctrl = PlaybackSyncController(FakePlugin())
    ctrl._cur_view_mode = "sequence"
    ctrl._cur_clip_guid = None
    attr = CountingAttr("Logical Frame")
    _seeded(ctrl, attr, "Logical Frame")

    ctrl.on_playhead_attribute_changed(attr, role=0)
    assert ctrl.plugin._cmd_queue.kinds() == ["playhead_sample"]

    ctrl.sample_and_broadcast_playhead("Logical Frame")

    assert "broadcast_playback_state" in ctrl.plugin._cmd_queue.kinds()
