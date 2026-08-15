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
from otio_sync_core.authority import POSITION_FIELDS  # noqa: E402

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
            status=STATE_SYNCED,
            active_timeline_guid=None,
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
    # Nothing was applied: the playhead's own position is untouched.
    assert ctrl.plugin.active_playhead.position == 0.0


def test_a_position_stripped_message_does_not_arm_the_guard():
    """A peer that asserts no position must not silence the peer that does.

    The 20:26:05 regression.  This peer held the position lease and was the one
    scrubbing; it applied a position-stripped view announcement from the other
    peer, which armed the guard, and its own next two scrubs were then dropped
    as "a peer is driving playback".  The session sat at 197 while this peer sat
    at 81, with nothing to re-align them.
    """
    ctrl = _controller()
    ctrl.plugin.active_playhead = FakePlayhead()

    ctrl.apply_playback_state({"view_mode": "sequence", "clip_guid": "c1"})

    assert ctrl._playback_apply_suppress_until == 0.0, (
        "a message carrying no position group says nothing about position and"
        " must leave the echo guard alone"
    )


def test_a_malformed_position_does_not_arm_the_guard():
    """Present-but-unusable is as silent as absent (authority.position_frame)."""
    ctrl = _controller()
    ctrl.plugin.active_playhead = FakePlayhead()

    ctrl.apply_playback_state(
        {"playing": False, "current_time": {"OTIO_SCHEMA": "RationalTime.1"}}
    )

    assert ctrl._playback_apply_suppress_until == 0.0


def test_a_scrub_survives_a_position_stripped_message_from_a_peer():
    """End to end: the drop that lost frames 118 and 81."""
    ctrl = _controller()
    ctrl.plugin.active_playhead = FakePlayhead()
    ctrl._pending_scrub_state = {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 118.0, "rate": 24.0},
        "playback_mode": "play-once",
        "view_mode": "sequence",
        "clip_guid": None,
    }
    ctrl._pending_scrub_due = time.monotonic() - 0.01

    # A peer announces a view without asserting any position.
    ctrl.apply_playback_state({"view_mode": "sequence", "clip_guid": "c1"})
    ctrl.flush_pending_scrub_broadcast()

    queued = [item for item in ctrl.plugin._cmd_queue.items
              if item[0] == "broadcast_playback_state"]
    assert queued, "this peer's own scrub must still reach the wire"
    assert queued[-1][1]["current_time"]["value"] == 118.0


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


# ── 3.2: a position we cannot vouch for is omitted, never substituted ───────
#
# These previously asserted the opposite — that an unreadable playhead was
# replaced with the last frame a peer told us, or with our own last polled one.
# That is a no-op for the peer only while it is still on that frame and we have
# not moved since, and neither survives a scrub.  Observed 2026-08-14 19:59:00:
# this peer scrubbed to 160, received nothing throughout, then broadcast the
# frame=45 it had been told before the scrub; the peer applied 45, this one
# stayed at 160, and nothing re-aligned them.  Omitting the group says nothing,
# which is the only honest thing a message with no trustworthy position can say.


def _assert_no_position(state):
    for field in POSITION_FIELDS:
        assert field not in state, f"{field} must be omitted, not substituted"


def test_unreadable_state_omits_position_rather_than_substituting_a_peers_frame():
    ctrl = _controller()
    ctrl.plugin.manager.status = STATE_SYNCED
    ctrl.current_playback_state = lambda: None  # simulate the unreadable path
    ctrl._last_polled_frame = 999

    ctrl.broadcast_view_state(None, "sequence")

    assert len(ctrl.plugin._cmd_queue.items) == 1
    _, state = ctrl.plugin._cmd_queue.items[0]
    _assert_no_position(state)
    assert state["view_mode"] == "sequence", "the view is still announced"


def test_unreadable_state_omits_position_rather_than_substituting_our_own_stale_frame():
    ctrl = _controller()
    ctrl.plugin.manager.status = STATE_SYNCED
    ctrl.current_playback_state = lambda: None
    ctrl._last_polled_frame = 17

    ctrl.broadcast_view_state(None, "sequence")

    _, state = ctrl.plugin._cmd_queue.items[0]
    _assert_no_position(state)


def test_unreadable_state_with_nothing_known_omits_position_rather_than_fabricating_zero():
    """The fabricated 0 a receiver could not tell from a genuine seek to start."""
    ctrl = _controller()
    ctrl.plugin.manager.status = STATE_SYNCED
    ctrl.current_playback_state = lambda: None
    ctrl._last_polled_frame = None

    ctrl.broadcast_view_state(None, "sequence")

    _, state = ctrl.plugin._cmd_queue.items[0]
    _assert_no_position(state)


def test_position_is_omitted_inside_the_echo_guard_window():
    """The 19:59:00 regression: a readable-but-untrustworthy position.

    A remote apply has just landed, so a view switch may have re-acquired the
    playhead and the value we read means nothing.  Say nothing about position
    rather than asserting either our reading or a remembered one.
    """
    ctrl = _controller()
    ctrl.plugin.manager.status = STATE_SYNCED
    ctrl.current_playback_state = lambda: {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 82.0, "rate": 24.0},
        "playback_mode": "loop",
    }
    ctrl._playback_apply_suppress_until = time.monotonic() + 5.0

    ctrl.broadcast_view_state(None, "sequence")

    _, state = ctrl.plugin._cmd_queue.items[0]
    _assert_no_position(state)


def test_new_source_clip_announces_the_frame_it_resets_to():
    """The 20:38:57 regression, and the invariant that block must hold.

    Isolating a new source clip moves the local playhead to 0 unconditionally,
    so the broadcast has to say 0 too.  It used to gate the announcement on
    whether this peer already held visibility, which is false for the FIRST clip
    any peer isolates — it cannot hold a lease it is in the act of taking.  The
    peer then reset itself to 0 and told the session 75, and the two sat 75
    frames apart with nothing to re-align them.
    """
    ctrl = _controller()
    ph = FakePlayhead(position=75.0)
    ctrl.plugin.active_playhead = ph
    ctrl.plugin.manager.owns_visibility = lambda *a, **k: False  # not yet the holder
    ctrl.current_playback_state = lambda: {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 75.0, "rate": 24.0},
        "playback_mode": "play-once",
    }

    ctrl.broadcast_view_state("clip-new", "source")

    _, state = ctrl.plugin._cmd_queue.items[0]
    assert state["current_time"]["value"] == 0.0, (
        "the announced frame must match the local reset"
    )
    assert ph.position == 0, "the local playhead is reset to the clip start"


def test_a_clip_change_while_scrubbing_is_not_an_isolation():
    """The 20:50:44-47 regression: frame 1, over and over, mid-scrub.

    Dragging the playhead across a sequence changes the on-screen media once per
    edit, exactly as playing it does. With a stale Pinned Source Mode making the
    mode read as ``source``, every crossing looked like a new isolation and
    forced frame 0 — the peer received ``0, 0, 208, 0, 0, 0, ... 227, 0`` and
    jumped to the start each time.
    """
    ctrl = _controller()
    ph = FakePlayhead(position=225.0)
    ctrl.plugin.active_playhead = ph
    ctrl.current_playback_state = lambda: {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 225.0, "rate": 24.0},
        "playback_mode": "play-once",
    }
    ctrl._last_position_change_at = time.monotonic()  # mid-drag

    ctrl.broadcast_view_state("clip-next", "source")

    _, state = ctrl.plugin._cmd_queue.items[0]
    assert state["current_time"]["value"] == 225.0, (
        "a sequence crossing an edit must keep the scrub position"
    )
    assert ph.position == 225.0, "and must not reset the local playhead"


def test_an_isolation_with_the_playhead_at_rest_still_resets():
    """The sibling check: the guard must not swallow a real double-click."""
    ctrl = _controller()
    ph = FakePlayhead(position=225.0)
    ctrl.plugin.active_playhead = ph
    ctrl.current_playback_state = lambda: {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 225.0, "rate": 24.0},
        "playback_mode": "play-once",
    }
    ctrl._last_position_change_at = time.monotonic() - 5.0  # settled

    ctrl.broadcast_view_state("clip-next", "source")

    _, state = ctrl.plugin._cmd_queue.items[0]
    assert state["current_time"]["value"] == 0.0
    assert ph.position == 0


def test_new_source_clip_announces_zero_even_inside_the_echo_guard():
    """Frame 0 here is an assertion, not a reading, so withholding must not eat it."""
    ctrl = _controller()
    ctrl.plugin.active_playhead = FakePlayhead(position=75.0)
    ctrl.current_playback_state = lambda: {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 75.0, "rate": 24.0},
        "playback_mode": "play-once",
    }
    ctrl._playback_apply_suppress_until = time.monotonic() + 5.0

    ctrl.broadcast_view_state("clip-new", "source")

    _, state = ctrl.plugin._cmd_queue.items[0]
    assert state["current_time"]["value"] == 0.0


def test_a_trustworthy_position_is_still_broadcast():
    """Withholding must not become the default — an ordinary view change carries
    the position it actually read."""
    ctrl = _controller()
    ctrl.plugin.manager.status = STATE_SYNCED
    ctrl.current_playback_state = lambda: {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 82.0, "rate": 24.0},
        "playback_mode": "loop",
    }
    ctrl._playback_apply_suppress_until = 0.0

    ctrl.broadcast_view_state(None, "sequence")

    _, state = ctrl.plugin._cmd_queue.items[0]
    assert state["current_time"]["value"] == 82.0


# ── view switch echo guard ──────────────────────────────────────────────────


def test_remote_induced_selection_returns_true():
    ctrl = _controller()
    ctrl.plugin.manager.remote_apply_context = lambda: {
        "source": "peer1",
        "command_schema": "PLAYBACK_SETTINGS",
        "event": "SET",
        "in_apply": False,
        "settling_for": 0.05,
        "age": 0.05,
    }

    is_remote, note = ctrl._provenance()
    assert is_remote is True
    assert "remote-induced" in note


def test_genuine_local_selection_returns_false():
    ctrl = _controller()
    ctrl.plugin.manager.remote_apply_context = lambda: None

    is_remote, note = ctrl._provenance()
    assert is_remote is False
    assert note == ""


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
