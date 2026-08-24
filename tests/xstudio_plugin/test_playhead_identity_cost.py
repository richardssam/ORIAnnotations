#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for fix-xstudio-selects-flakiness (cost half).

Property under test, from that change's ``xstudio-plugin-module-structure``
spec delta — "Routine poll work does not pay wrapper-construction cost to
answer identity questions": constructing an xStudio ``Playhead`` runs
``ModuleBase.__init__``, which fetches every one of the playhead's ~46
attributes with its own synchronous round-trip. Work that only needs to know
*which* playhead is current must not pay that, and a guard that is going to
decline must be reachable without paying it either.

The companion correctness property (an isolation's frame-0 reset not
discarding a newer position) is deliberately NOT covered here: it was
implemented, measured to never fire, and reverted — see that change's
tasks.md 5.5 for why (the plugin does not observe a seek landing on a
not-yet-adopted playhead, so it has no timestamp to compare).

Requires the xStudio Python bindings (``playback_sync`` imports ``xstudio.core``
at module level) but **not** a live session — nothing here is a real actor. Run
with the xStudio-bundled interpreter via ``./run_tests_xstudio.sh``.
"""
import os
import sys
import time
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
    default_timeout_ms = 2000

    def __init__(self) -> None:
        self.api = types.SimpleNamespace(
            session=types.SimpleNamespace(viewed_container=None)
        )


class FakePlayhead:
    """Playhead stub that records position writes.

    ``get_attribute`` is present because :meth:`_as_playhead` uses it to tell a
    wrapper from a bare actor handle — a stub without it would be re-wrapped.
    """

    def __init__(self, position: float = 0.0, playing: bool = False) -> None:
        self.position = position
        self.playing = playing
        self.frame_rate = types.SimpleNamespace(fps=lambda: 24.0)
        self.remote = "ph-actor-1"
        self._attrs: dict = {}
        self.position_writes: list = []

    def __setattr__(self, name, value):
        if name == "position" and "position_writes" in self.__dict__:
            self.__dict__["position_writes"].append(value)
        super().__setattr__(name, value)

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
        self.active_playhead = None

    def stamp_remote_apply(self, channel: str) -> None:
        pass

    def claim_lease(self, channel: str) -> None:
        pass


def _broadcast_state(ctrl):
    """The payload of the last queued broadcast_playback_state command."""
    for cmd, payload in reversed(ctrl.plugin._cmd_queue.items):
        if cmd == "broadcast_playback_state":
            return payload
    return None


# ── spec: identity questions do not pay wrapper-construction cost ────────────


def test_periodic_scan_does_not_wrap_the_playhead_to_read_its_identity():
    """``check_and_update_active_playhead`` runs ~1 Hz. It used to construct a
    ``Playhead`` (~46 actor round-trips) purely to read the identity key, then
    discard it on the common paths.
    """
    ctrl = PlaybackSyncController(FakePlugin())
    wraps: list = []
    ctrl._viewport_playhead = lambda: (wraps.append(True), FakePlayhead())[1]
    ctrl._viewport_playhead_actor = lambda: "ph-actor-1"

    # First sighting: recorded, not adopted (needs two consecutive scans).
    ctrl.check_and_update_active_playhead()
    assert ctrl._last_scanned_playhead_key == "ph-actor-1"
    assert wraps == [], "the first sighting must not wrap the playhead"

    # Second sighting of an already-adopted playhead: still nothing to do.
    ctrl.plugin.active_playhead = FakePlayhead()
    ctrl.check_and_update_active_playhead()
    assert wraps == [], (
        "an unchanged playhead must not be wrapped just to be compared"
    )


def test_remote_key_normalises_a_bare_handle_and_a_wrapper_alike():
    """The cheap identity path compares handles against keys recorded from
    wrappers. If the two forms stringified differently, every scan would look
    like a change and re-adopt — the subscription-killing churn the
    two-consecutive-sightings rule exists to prevent.
    """
    ph = FakePlayhead()
    assert (
        PlaybackSyncController._remote_key(ph)
        == PlaybackSyncController._remote_key(ph.remote)
    )
    assert PlaybackSyncController._remote_key(None) is None


def test_carry_over_guard_declines_before_wrapping():
    """The guard must be reachable without paying the wrapper cost — the exact
    defect left behind by the first fix, where ``_viewport_playhead()`` was the
    *argument* and so ran before the guard could decline.
    """
    ctrl = PlaybackSyncController(FakePlugin())
    ctrl._last_known_playback_mode = "loop"
    wrapped: list = []
    ctrl._as_playhead = lambda obj: (wrapped.append(True), FakePlayhead())[1]

    # First call goes through (and wraps).
    ctrl._carry_over_playback_mode("ph-actor-1")
    assert wrapped == [True]

    # Immediate repeat for the same playhead is declined by the (key, time)
    # guard — and must not wrap again.
    ctrl._carry_over_playback_mode("ph-actor-1")
    assert wrapped == [True], "a declined call must not construct a wrapper"
