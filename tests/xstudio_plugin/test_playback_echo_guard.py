#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for fix-playback-position-echo-loop's plugin-side fixes.

Property under test: the echo guard (``_playback_apply_suppress_until``) must
arm on *receipt* of a remote playback message even when the message is
dropped (tasks.md 1.2), a throttled scrub broadcast sampled before the guard
armed must be discarded at flush time rather than released (1.3), and an
unreadable ``current_playback_state()`` must withhold to the best known frame
instead of fabricating ``frame=0`` (3.2).

Requires the xStudio Python bindings, because
``xstudio_plugin.ori_sync.playback_sync`` imports ``xstudio.core`` /
``xstudio.api.session.playhead`` at module level. It does **not** require a
live xStudio session — no playhead/session object here is real. Run with the
xStudio-bundled interpreter, e.g.::

    /path/to/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 -m pytest \\
        tests/xstudio_plugin/test_playback_echo_guard.py -v

Lives under the repo-root ``tests/`` tree, not inside ``xstudio_plugin/``
itself — see test_sequence_reconciliation_convergence.py's docstring for why.
"""
import os
import sys
import time
import types

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_repo_root, "python"))
sys.path.insert(0, os.path.join(_repo_root, "xstudio_plugin"))

from otio_sync_core.manager import STATE_SYNCED  # noqa: E402

# Register a lightweight 'ori_sync' package stub in sys.modules so the real
# ori_sync/__init__.py never runs — it imports ori_sync_plugin, which needs
# `pika` (RabbitMQ), not part of xStudio's bundled Python. playback_sync.py's
# `from .utils import ...` only needs a valid package context to resolve, not
# the real package init.
_ori_sync_dir = os.path.join(_repo_root, "xstudio_plugin", "ori_sync")
_ori_sync_stub = types.ModuleType("ori_sync")
_ori_sync_stub.__path__ = [_ori_sync_dir]
sys.modules.setdefault("ori_sync", _ori_sync_stub)

from ori_sync import playback_sync  # noqa: E402

PlaybackSyncController = playback_sync.PlaybackSyncController


class FakeQueue:
    """Records every queued command in order; no real threading involved."""

    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)


class FakeConnection:
    default_timeout_ms = 2000


class FakePlayhead:
    """Stands in for the active xStudio playhead where a test needs one."""

    def __init__(self, position: float = 0.0, playing: bool = False) -> None:
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
            status=STATE_SYNCED, active_timeline_guid=None
        )
        self._sync_playlists: dict = {}
        self._cmd_queue = FakeQueue()
        self.active_playhead = None

    def stamp_remote_apply(self, channel: str) -> None:
        pass

    def claim_lease(self, channel: str) -> None:
        pass


def _controller() -> PlaybackSyncController:
    return PlaybackSyncController(FakePlugin())


# ── 1.2: guard arms ahead of every early return ──────────────────────────────


def test_dropped_message_still_arms_the_guard():
    """A mismatched timeline_guid while paused makes apply_playback_state
    return without applying anything — but it must still record that a peer
    is driving, or this peer's own playhead could move (a selection reset)
    and get broadcast back over the driver's seek. See design.md's "guard
    tracks driving, not applied" decision.
    """
    ctrl = _controller()
    ctrl.plugin.active_playhead = FakePlayhead()
    ctrl.plugin.manager.active_timeline_guid = "tl-other"
    # Avoid the real (xStudio-calling) viewed-container lookup: force a local
    # guid that disagrees with the incoming one, reproducing the exact
    # mismatch branch the design doc traces.
    ctrl.get_local_viewed_timeline_guid = lambda: "tl-local"

    before = time.monotonic()
    ctrl.apply_playback_state(
        {
            "playing": False,
            "current_time": {"value": 63.0},
            "timeline_guid": "tl-incoming",
        }
    )

    assert ctrl._playback_apply_suppress_until > before, (
        "the guard must be armed even though the message was dropped on a"
        " timeline mismatch"
    )
    assert ctrl._last_received_frame == 63, (
        "the driver's position must be recorded even on a dropped message —"
        " broadcast_view_state's withhold-while-driven logic depends on it"
    )
    # Nothing was applied: the playhead's own position is untouched.
    assert ctrl.plugin.active_playhead.position == 0.0


def test_dropped_message_with_playing_true_is_not_skipped():
    """A mismatched timeline_guid does NOT drop a playing=True update — only
    the seek is dropped on mismatch, per apply_playback_state's own comment.
    This is the sibling path check: guard-arming must not regress the case
    that was already handled correctly.
    """
    ctrl = _controller()
    ph = FakePlayhead(position=10.0, playing=False)
    ctrl.plugin.active_playhead = ph
    ctrl.plugin.manager.active_timeline_guid = "tl-other"
    ctrl.get_local_viewed_timeline_guid = lambda: "tl-local"

    ctrl.apply_playback_state(
        {
            "playing": True,
            "current_time": {"value": 63.0},
            "timeline_guid": "tl-incoming",
        }
    )

    assert ph.playing is True, "playing=True must still be applied on mismatch"


# ── 1.3 / 7.2: a stale pending scrub is discarded, not released ─────────────


def test_stale_pending_scrub_is_discarded_when_guard_arms_before_flush():
    """Deliberately constructs the race tasks.md 7.2 flags as never having
    fired live: a position is throttled (captured) BEFORE any peer starts
    driving, and the guard arms AFTER capture but BEFORE the flush runs. The
    queued position is now older than the driver's own seek and must be
    dropped rather than broadcast over it.
    """
    ctrl = _controller()
    ctrl._pending_scrub_state = {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 42.0, "rate": 24.0},
        "playback_mode": "play-once",
        "view_mode": "sequence",
        "clip_guid": None,
    }
    ctrl._pending_scrub_due = time.monotonic() - 0.01  # already due to flush

    # A peer's message arrives between capture and flush.
    ctrl._playback_apply_suppress_until = time.monotonic() + 0.4

    ctrl.flush_pending_scrub_broadcast()

    assert ctrl._pending_scrub_state is None, "the stale state must be cleared"
    assert ctrl.plugin._cmd_queue.items == [], (
        "a position sampled before the peer started driving must never reach"
        " the wire — it would override the seek the driver just issued"
    )


def test_pending_scrub_is_released_when_no_peer_is_driving():
    """Sibling check: the discard must not become a blanket drop. With no
    guard armed, a throttled scrub still flushes normally.
    """
    ctrl = _controller()
    ctrl._pending_scrub_state = {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 42.0, "rate": 24.0},
        "playback_mode": "play-once",
        "view_mode": "sequence",
        "clip_guid": None,
    }
    ctrl._pending_scrub_due = time.monotonic() - 0.01
    ctrl._playback_apply_suppress_until = 0.0  # no peer driving

    ctrl.flush_pending_scrub_broadcast()

    assert ctrl._pending_scrub_state is None
    assert len(ctrl.plugin._cmd_queue.items) == 1
    cmd, state = ctrl.plugin._cmd_queue.items[0]
    assert cmd == "broadcast_playback_state"
    assert state["current_time"]["value"] == 42.0


def test_pending_scrub_not_yet_due_is_left_pending():
    """Sibling check: a deadline in the future must not flush early — this is
    what makes the throttle a throttle rather than a drop.
    """
    ctrl = _controller()
    state = {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 5.0, "rate": 24.0},
    }
    ctrl._pending_scrub_state = state
    ctrl._pending_scrub_due = time.monotonic() + 10.0

    ctrl.flush_pending_scrub_broadcast()

    assert ctrl._pending_scrub_state is state
    assert ctrl.plugin._cmd_queue.items == []


# ── 3.2: an unreadable current_playback_state() withholds, not fabricates ───


def test_unreadable_state_withholds_to_the_drivers_last_frame():
    """When a peer is (or was recently) driving, its last-told position is
    the only frame we actually know to be meaningful — use it rather than a
    fabricated 0, which a receiver cannot tell apart from a genuine seek to
    the clip start.
    """
    ctrl = _controller()
    ctrl.plugin.manager.status = STATE_SYNCED
    ctrl.current_playback_state = lambda: None  # simulate the unreadable path
    ctrl._last_received_frame = 61
    ctrl._last_polled_frame = 999  # must not win over the driver's frame

    ctrl.broadcast_view_state(None, "sequence")

    assert len(ctrl.plugin._cmd_queue.items) == 1
    _, state = ctrl.plugin._cmd_queue.items[0]
    assert state["current_time"]["value"] == 61.0


def test_unreadable_state_falls_back_to_own_last_frame_when_no_peer_driving():
    ctrl = _controller()
    ctrl.plugin.manager.status = STATE_SYNCED
    ctrl.current_playback_state = lambda: None
    ctrl._last_received_frame = None
    ctrl._last_polled_frame = 17

    ctrl.broadcast_view_state(None, "sequence")

    _, state = ctrl.plugin._cmd_queue.items[0]
    assert state["current_time"]["value"] == 17.0


def test_unreadable_state_with_nothing_known_still_broadcasts_frame_zero():
    """Last resort only: with no driver frame and no local history, 0 is the
    only value left — this must keep working, just distinctly logged.
    """
    ctrl = _controller()
    ctrl.plugin.manager.status = STATE_SYNCED
    ctrl.current_playback_state = lambda: None
    ctrl._last_received_frame = None
    ctrl._last_polled_frame = None

    ctrl.broadcast_view_state(None, "sequence")

    _, state = ctrl.plugin._cmd_queue.items[0]
    assert state["current_time"]["value"] == 0.0


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
