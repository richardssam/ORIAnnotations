#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""The broadcast playback mode comes from the cache, not from a playhead read.

xStudio gives every clip its own ``Playhead`` whose native "Loop Mode" resets to
the engine's "Play Once" default rather than inheriting the session's mode. The
plugin carries the last known mode forward onto each new playhead, but that
write is an **asynchronous actor message**, so between acquiring a playhead and
the write landing the raw attribute reads "Play Once" regardless of what the
user chose. An ``active_playhead`` reference lagging a viewport swap widens the
same gap.

Reading through that gap is survivable locally — the write lands a moment later.
It is not survivable on the wire, because applying a peer's mode writes the
cache, so a single bad read becomes permanent session state. Observed
2026-08-13 during the `session-roles` task 11.4 soak, two drivers on a sequence,
with nobody touching a loop control:

    host   12:41:51.594  broadcast (mid-scrub) → play-once
    client 12:41:51.682  RECV playback: set Loop Mode=Play Once
    host   12:41:53.450  RECV playback: set Loop Mode=Play Once    ← echoed back
    host   12:43:02.774  [SEL] carried over Loop Mode=Play Once    ← now permanent

``_get_playback_mode`` therefore answers from ``_last_known_playback_mode``,
which only ever changes on a genuine signal. ``_read_playhead_loop_mode`` keeps
the raw read for ``_on_loop_mode_changed``, the one caller that must have it.

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


class FakeConnection:
    def __init__(self) -> None:
        self.default_timeout_ms = 100_000


class LaggingPlayhead:
    """A playhead whose ``set_attribute`` does not take effect immediately.

    This is the behaviour under test. A playhead that applied writes
    synchronously would make these assertions pass with or without the fix.
    """

    def __init__(self, initial: str = "Play Once") -> None:
        self._attrs = {"Loop Mode": initial}
        self.pending: dict = {}
        self._writes: dict = {}
        self.playing = False
        self.position = 0.0
        self.frame_rate = types.SimpleNamespace(fps=lambda: 24.0)

    def get_attribute(self, name):
        return self._attrs.get(name)

    def set_attribute(self, name, value) -> None:
        self.pending[name] = value
        self._writes[name] = self._writes.get(name, 0) + 1

    def writes(self, name) -> int:
        return self._writes.get(name, 0)

    def settle(self) -> None:
        """Let the actor process the queued writes."""
        self._attrs.update(self.pending)
        self.pending.clear()


class FakeQueue:
    def __init__(self) -> None:
        self.items: list = []

    def put(self, item) -> None:
        self.items.append(item)


class FakePlugin:
    def __init__(self, playhead) -> None:
        self.connection = FakeConnection()
        self.manager = types.SimpleNamespace(
            status=STATE_SYNCED,
            active_timeline_guid="tl-1",
            claim_category=lambda *a, **k: None,
            owns_visibility=lambda *a, **k: True,
        )
        self._sync_playlists: dict = {}
        self._cmd_queue = FakeQueue()
        self.active_playhead = playhead

    def stamp_remote_apply(self, channel: str) -> None:
        pass

    def claim_lease(self, channel: str) -> None:
        pass


def _controller(playhead):
    ctrl = PlaybackSyncController(FakePlugin(playhead))
    # Not isolated on a clip: the source-mode branch short-circuits to "loop"
    # without reading anything, which is the case that already worked.
    ctrl._last_pinned_source_mode = True
    return ctrl


def test_the_carried_over_mode_is_reported_before_the_write_lands():
    ph = LaggingPlayhead(initial="Play Once")
    ctrl = _controller(ph)
    ctrl._last_known_playback_mode = "loop"

    ctrl._carry_over_playback_mode(ph)

    assert ph.get_attribute("Loop Mode") == "Play Once", (
        "test scaffolding: the write must still be in flight, or this proves nothing"
    )
    assert ctrl._get_playback_mode() == "loop"


def test_a_stale_playhead_reference_cannot_downgrade_the_mode():
    """The gap is not only the write window. ``active_playhead`` can point at a
    playhead the viewport has already moved off, which never receives a
    carry-over at all and reads its untouched default forever."""
    forgotten = LaggingPlayhead(initial="Play Once")
    ctrl = _controller(forgotten)
    ctrl._last_known_playback_mode = "loop"
    ctrl._loop_mode_apply_suppress_until = 0.0  # no window is open

    assert ctrl._get_playback_mode() == "loop"


def test_no_cache_falls_through_to_the_playhead():
    """Before any genuine signal has been seen — startup — the playhead is all
    there is, so the fallback must remain."""
    ph = LaggingPlayhead(initial="Loop")
    ctrl = _controller(ph)
    ctrl._last_known_playback_mode = None

    assert ctrl._get_playback_mode() == "loop"


def test_clip_isolation_still_forces_loop():
    ph = LaggingPlayhead(initial="Play Once")
    ctrl = _controller(ph)
    ctrl._last_pinned_source_mode = False
    ctrl._last_known_playback_mode = "play-once"

    assert ctrl._get_playback_mode() == "loop"


# ---------------------------------------------------------------------------
# The cache must still track the user
# ---------------------------------------------------------------------------
#
# Preferring the cache is only safe if a real user choice reaches it. These
# pin the update path, without which the mode would latch at its first value
# and the fix would be worse than the bug.


def test_a_user_loop_mode_change_updates_the_cache_and_broadcasts():
    ph = LaggingPlayhead(initial="Loop")
    ctrl = _controller(ph)
    ctrl._last_known_playback_mode = "play-once"
    ctrl._loop_mode_apply_suppress_until = 0.0  # a genuine change, not our echo

    ctrl._on_loop_mode_changed()

    assert ctrl._last_known_playback_mode == "loop"
    assert ctrl._get_playback_mode() == "loop"
    queued = [c for c, _ in ctrl.plugin._cmd_queue.items]
    assert "broadcast_playback_state" in queued


def test_a_user_choosing_play_once_is_honoured():
    """The inverse direction: the cache must not pin the session to Loop."""
    ph = LaggingPlayhead(initial="Play Once")
    ctrl = _controller(ph)
    ctrl._last_known_playback_mode = "loop"
    ctrl._loop_mode_apply_suppress_until = 0.0

    ctrl._on_loop_mode_changed()

    assert ctrl._get_playback_mode() == "play-once"


def test_our_own_write_echoing_back_does_not_update_the_cache():
    """``_carry_over_playback_mode``'s write fires an attribute-changed event.
    Treating that echo as a user choice is how a value the plugin itself wrote
    gets promoted to intent."""
    ph = LaggingPlayhead(initial="Play Once")
    ctrl = _controller(ph)
    ctrl._last_known_playback_mode = "loop"

    ctrl._carry_over_playback_mode(ph)   # arms the suppression window
    ctrl._on_loop_mode_changed()          # the echo, arriving inside it

    assert ctrl._last_known_playback_mode == "loop"
    assert ctrl.plugin._cmd_queue.items == []


# ---------------------------------------------------------------------------
# Applying a peer's mode must not rewrite it on every message
# ---------------------------------------------------------------------------
#
# Measured 2026-08-13 on a 45-second session: 64 Loop Mode writes for 60
# applied frames — one per incoming message, where one write in total was
# required. The read-back guard could never fire, because `set_attribute` is
# asynchronous and the read lands inside the window where the previous write has
# not taken effect.
#
# The writes themselves were cheap. Each one re-armed the 0.4 s
# apply-suppression window, and at scrub rates those windows overlap
# continuously, so for as long as a peer scrubbed this peer's *own* Loop Mode
# changes were read as our echo and discarded.


def _apply_controller(ph):
    ctrl = _controller(ph)
    ctrl.plugin.active_playhead = ph
    ctrl._last_pinned_source_mode = True
    ctrl.get_local_viewed_timeline_guid = lambda: "tl-1"
    return ctrl


def _playback_msg(mode="loop", frame=10.0):
    return {
        "playing": False,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": frame, "rate": 24.0},
        "playback_mode": mode,
        "timeline_guid": "tl-1",
    }


def test_repeated_applies_of_the_same_mode_write_once():
    ph = LaggingPlayhead(initial="Play Once")
    ctrl = _apply_controller(ph)

    for frame in range(10, 20):
        ctrl.apply_playback_state(_playback_msg("loop", float(frame)))

    assert ph.writes("Loop Mode") == 1, (
        f"one mode change must cost one write, not {ph.writes('Loop Mode')}"
    )


def test_a_changed_mode_is_still_applied():
    """The guard must not pin the mode — a peer switching to Play Once has to
    reach us, or the sessions silently disagree."""
    ph = LaggingPlayhead(initial="Play Once")
    ctrl = _apply_controller(ph)

    ctrl.apply_playback_state(_playback_msg("loop"))
    ctrl.apply_playback_state(_playback_msg("play-once"))

    assert ph.writes("Loop Mode") == 2
    assert ctrl._last_known_playback_mode == "play-once"


def test_the_suppression_window_is_not_re_armed_by_a_no_op():
    """The actual harm: a continuously re-armed window swallows this peer's own
    loop-mode changes for as long as any peer keeps scrubbing."""
    ph = LaggingPlayhead(initial="Play Once")
    ctrl = _apply_controller(ph)

    ctrl.apply_playback_state(_playback_msg("loop"))
    ctrl._loop_mode_apply_suppress_until = 0.0      # let the window lapse
    for frame in range(10, 20):
        ctrl.apply_playback_state(_playback_msg("loop", float(frame)))

    assert ctrl._loop_mode_apply_suppress_until == 0.0, (
        "a no-op apply re-armed the window that guards the user's own changes"
    )
