#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for poll-thread-owns-actor-io.

Property under test, from that change's ``xstudio-plugin-module-structure``
spec delta — "Only the poll thread performs xStudio actor I/O": the xStudio
Python ``Connection`` is not safe for concurrent use, and a ``request_receive``
issued while another thread has one in flight on the same connection can block
for the *entire* ``default_timeout_ms`` (measured: 100 s and 200 s stalls).
Handlers that run on an xStudio callback thread must therefore confine
themselves to data the event already carries, cheap local state, and enqueuing.

The enforcement here is a connection whose every actor-facing method raises.
That is deliberately louder than counting calls: a handler that reaches for an
actor fails with a named error pointing at this requirement, rather than
quietly passing because a stub happened to answer.

Requires the xStudio Python bindings (``playback_sync`` imports ``xstudio.core``
at module level) but **not** a live session — nothing here is a real actor. Run
with the xStudio-bundled interpreter via ``./run_tests_xstudio.sh``.
"""
import os
import sys
import types

import pytest

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


class ActorIOOnCallbackThread(AssertionError):
    """Raised when a callback-thread handler touches an actor."""


class TrapConnection:
    """A connection that refuses to be used.

    ``default_timeout_ms`` is a real attribute because ``bounded_timeout`` reads
    and restores it — and the fact that it is one process-wide value, not a
    thread-local, is half of why this requirement exists.
    """

    default_timeout_ms = 2000

    def __init__(self) -> None:
        self.api = types.SimpleNamespace(
            session=types.SimpleNamespace(viewed_container=self._trap)
        )

    def _trap(self, *_a, **_k):
        raise ActorIOOnCallbackThread(
            "actor I/O from an xStudio callback thread — enqueue onto "
            "_cmd_queue and do this on the poll thread instead"
        )

    request_receive = _trap
    send = _trap
    remote = _trap


class FakeQueue:
    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)

    def kinds(self) -> list:
        return [cmd for cmd, _ in self.items]


class TrapPlayhead:
    """A playhead whose every property read is actor I/O — because it is.

    ``position``, ``playing`` and ``frame_rate`` are all round-trips on the real
    wrapper, which is exactly what the old ``on_playhead_attribute_changed``
    did per rendered frame while scrubbing.
    """

    remote = "ph-actor-1"

    def _trap(self, *_a, **_k):
        raise ActorIOOnCallbackThread("playhead read from a callback thread")

    get_attribute = _trap
    set_attribute = _trap

    @property
    def position(self):
        return self._trap()

    @property
    def playing(self):
        return self._trap()

    @property
    def frame_rate(self):
        return self._trap()


class FakePlugin:
    def __init__(self) -> None:
        self.connection = TrapConnection()
        self.manager = types.SimpleNamespace(
            status=STATE_SYNCED,
            active_timeline_guid="tl-1",
            timelines={},
            remote_apply_context=lambda: None,
            claim_category=lambda *a, **k: None,
            owns_visibility=lambda *a, **k: True,
        )
        self._sync_playlists: dict = {}
        self._cmd_queue = FakeQueue()
        self.active_playhead = TrapPlayhead()
        self.annotation = types.SimpleNamespace(
            reload_in_progress=lambda: False,
            _annotation_pending_time=0.0,
        )

    def stamp_remote_apply(self, channel: str) -> None:
        pass

    def claim_lease(self, channel: str) -> None:
        pass


# ── event stubs ──────────────────────────────────────────────────────────────
#
# The handlers dispatch on xStudio atom types, so these use the real ones. The
# *payloads* are stubs: a UuidActor's ``uuid`` and a playhead actor handle are
# fields of the delivered message, which is why reading them is free.

from xstudio.core import (  # noqa: E402
    event_atom,
    show_atom,
    source_atom,
    viewport_playhead_atom,
)


class FakeUuidActor:
    def __init__(self, uuid: str = "media-uuid-1") -> None:
        self.uuid = uuid


class FakeAttr:
    """A module attribute. ``uuid`` is local; ``name`` is a round-trip."""

    def __init__(self, uuid: str = "attr-1") -> None:
        self.uuid = uuid

    @property
    def name(self):
        raise ActorIOOnCallbackThread("attr.name read from a callback thread")


def _controller():
    ctrl = PlaybackSyncController(FakePlugin())
    return ctrl, ctrl.plugin._cmd_queue


# ── spec: handlers on a callback thread perform no actor I/O ─────────────────


def test_on_selection_event_enqueues_and_reads_no_actor():
    """It used to call ``check_and_update_active_playhead`` directly, throttled
    to 1 Hz — the same call the poll loop was already making at the same
    cadence, on the correct thread.
    """
    ctrl, q = _controller()

    ctrl.on_selection_event([event_atom(), source_atom()])

    assert q.kinds() == ["check_active_playhead", "resolve_selection"], (
        "the playhead re-check must be queued ahead of the resolution that reads it"
    )


def test_selection_storm_queues_one_playhead_check_not_one_each():
    """Coalescing, not throttling: the check still happens, and still happens
    before the resolutions — there is just one of it per batch.
    """
    ctrl, q = _controller()

    for _ in range(20):
        ctrl.on_selection_event([event_atom(), source_atom()])

    assert q.kinds().count("check_active_playhead") == 1
    assert q.kinds().count("resolve_selection") == 20, (
        "every selection still gets resolved — only the idempotent re-check coalesces"
    )

    # Once the poll thread has run it, the next event queues a fresh one.
    ctrl._playhead_check_pending = False
    ctrl.on_selection_event([event_atom(), source_atom()])
    assert q.kinds().count("check_active_playhead") == 2


def test_on_playhead_attribute_changed_identifies_the_attribute_without_reading_it():
    """``attr.name`` is a round-trip and this fires per rendered frame while
    scrubbing; ``attr.uuid`` is a local field. The name map is seeded from the
    wrapper's own ``attrs_by_name_``, which was already paid for.
    """
    ctrl, q = _controller()
    attr = FakeAttr("attr-logical-frame")
    ph = types.SimpleNamespace(attrs_by_name_={"Logical Frame": attr})
    ctrl._seed_attr_names(ph)

    ctrl.on_playhead_attribute_changed(attr, 0)

    assert q.kinds() == ["playhead_sample"]


def test_unseeded_attribute_is_deferred_not_dropped():
    """A miss must not read the name here as a fallback — it hands the whole
    event to the poll thread, which resolves the name and dispatches it.
    """
    ctrl, q = _controller()

    ctrl.on_playhead_attribute_changed(FakeAttr("never-seen"), 0)

    assert q.kinds() == ["playhead_attr_name"]
    assert q.items[0][1].uuid == "never-seen"


def test_scrub_burst_coalesces_to_one_sample():
    """xStudio fires these ~60 Hz while dragging. The poll thread samples the
    playhead live, so one queued marker covers every event before it runs.
    """
    ctrl, q = _controller()
    attr = FakeAttr("attr-logical-frame")
    ctrl._seed_attr_names(types.SimpleNamespace(attrs_by_name_={"Logical Frame": attr}))

    for _ in range(60):
        ctrl.on_playhead_attribute_changed(attr, 0)

    assert q.kinds() == ["playhead_sample"]


def test_loop_mode_and_media_change_are_deferred():
    """Both used to run inline: ``_on_loop_mode_changed`` reads the playhead,
    and ``_carry_over_playback_mode`` re-fetches all ~46 of its attributes.
    """
    ctrl, q = _controller()
    loop = FakeAttr("attr-loop")
    media = FakeAttr("attr-media")
    ctrl._seed_attr_names(
        types.SimpleNamespace(
            attrs_by_name_={"Loop Mode": loop, "Current Media Uuid": media}
        )
    )

    ctrl.on_playhead_attribute_changed(loop, 0)
    ctrl.on_playhead_attribute_changed(media, 0)

    assert q.kinds() == ["playhead_loop_mode", "playhead_media_changed"]


def test_on_global_playhead_event_defers_the_media_change_branch():
    """That branch resolves clip guids, reads Pinned Source Mode fresh and
    broadcasts view state — all actor I/O, all now on the poll thread. The
    media uuid it passes along is a field of the delivered ``UuidActor``.
    """
    ctrl, q = _controller()

    ctrl.on_global_playhead_event(
        [event_atom(), show_atom(), FakeUuidActor("m-1"), FakeUuidActor("m-2"), "x"]
    )

    assert q.kinds() == ["show_atom_media"]
    assert q.items[0][1]["media_uuid"] == "m-1"


def test_on_global_playhead_event_defers_playhead_adoption():
    """Wrapping the actor handle in a ``Playhead`` is ~46 round-trips, since
    ``ModuleBase.__init__`` fetches every attribute one at a time. The handler
    passes the bare handle.
    """
    ctrl, q = _controller()

    ctrl.on_global_playhead_event(
        [event_atom(), viewport_playhead_atom(), "viewport0", "ph-actor-9"]
    )

    assert q.kinds() == ["viewport_playhead"]
    payload = q.items[0][1]
    assert payload == {"viewport": "viewport0", "ph_remote": "ph-actor-9"}
    assert ctrl.plugin.active_playhead is not None, (
        "the handler must not adopt — that is the poll thread's job"
    )


def test_annotation_show_atom_branch_touches_no_actor():
    """The bookmark/annotation shape only stamps local state. Covered so a
    later edit cannot quietly reintroduce a read on the cheap path.
    """
    ctrl, q = _controller()

    ctrl.on_global_playhead_event(
        [event_atom(), show_atom(), FakeUuidActor(), FakeUuidActor(), "x", 7]
    )

    assert q.kinds() == []
    assert ctrl.plugin.annotation._annotation_pending_time > 0


# ── spec: the deferred work still reaches the poll thread ────────────────────


def test_poll_thread_resolves_an_unseeded_name_and_dispatches_it():
    """The fallback path closes the loop: the poll thread may read the name (it
    is allowed to), caches it, and dispatches the same work a seeded hit would.
    """
    ctrl, q = _controller()

    class ResolvableAttr:
        uuid = "attr-late"
        name = "Logical Frame"

    ctrl.resolve_playhead_attr_name(ResolvableAttr())

    assert q.kinds() == ["playhead_sample"]
    assert ctrl._attr_name_by_uuid["attr-late"] == "Logical Frame"

    # And the next event for it is now a local hit — no second deferral.
    q.items.clear()
    ctrl._playhead_sample_pending = False
    ctrl.on_playhead_attribute_changed(ResolvableAttr(), 0)
    assert q.kinds() == ["playhead_sample"]


def test_refreshing_attributes_reseeds_the_name_map():
    """``refresh_playhead_attributes`` replaces the playhead's attribute uuids,
    which would otherwise leave the callback thread's map stale and send the
    next ~60 scrub events down the slow path one at a time.
    """
    ctrl, _q = _controller()
    ctrl._seed_attr_names(
        types.SimpleNamespace(attrs_by_name_={"Logical Frame": FakeAttr("old-uuid")})
    )
    assert ctrl._attr_name_by_uuid["old-uuid"] == "Logical Frame"

    ctrl._seed_attr_names(
        types.SimpleNamespace(attrs_by_name_={"Logical Frame": FakeAttr("new-uuid")})
    )
    assert ctrl._attr_name_by_uuid["new-uuid"] == "Logical Frame"


def test_sample_clears_its_pending_flag_before_reading():
    """An event landing while the sample's reads are in flight must queue
    another sample rather than be swallowed by the one already running.
    """
    ctrl, _q = _controller()
    ctrl._playhead_sample_pending = True
    ctrl.plugin.active_playhead = None  # returns straight after clearing the flag

    ctrl.sample_and_broadcast_playhead("Logical Frame")

    assert ctrl._playhead_sample_pending is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
