#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for a joining peer adopting the session's view.

Property under test: joining must land this peer on the clip and frame the
session is looking at.  Two halves have to hold for that, and both were broken
on 2026-08-15 09:18:42:

* the snapshot has to *say* what is being viewed — it carried ``current_time``
  and no ``view_mode``/``clip_guid``, so a joiner learned a frame without the
  identity of the thing it indexes into;
* the joiner has to apply it once it has something to apply it to — the manager
  applies on receipt, which is before the joiner's playlists, viewport or
  playhead exist.

Same interpreter requirements as test_playback_echo_guard.py — see its docstring.
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
    default_timeout_ms = 2000


class FakePlayhead:
    def __init__(self, position: float = 0.0) -> None:
        self.position = position
        self.playing = False
        self.frame_rate = types.SimpleNamespace(fps=lambda: 24.0)
        self._attrs: dict = {}

    def get_attribute(self, name):
        return self._attrs.get(name, "Play Once")

    def set_attribute(self, name, value) -> None:
        self._attrs[name] = value


class FakePlugin:
    def __init__(self, playback_state=None) -> None:
        self.connection = FakeConnection()
        self.manager = types.SimpleNamespace(
            status=STATE_SYNCED,
            active_timeline_guid=None,
            playback_state=playback_state or {},
            claim_category=lambda *a, **k: None,
            owns_visibility=lambda *a, **k: True,
            remote_apply_context=lambda: None,
        )
        self._sync_playlists: dict = {}
        self._cmd_queue = FakeQueue()
        self.active_playhead = None

    def stamp_remote_apply(self, channel: str) -> None:
        pass

    def claim_lease(self, channel: str) -> None:
        pass


_SESSION_VIEW = {
    "playing": False,
    "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 48.0, "rate": 24.0},
    "playback_mode": "loop",
    "view_mode": "source",
    "clip_guid": "clip-host-is-on",
}


def _controller(playback_state=None) -> PlaybackSyncController:
    return PlaybackSyncController(FakePlugin(playback_state))


# ── the snapshot must describe the view, not just the position ──────────────


def test_snapshot_state_carries_the_view():
    """A frame without a clip indexes into something the joiner was never told."""
    ctrl = _controller()
    ctrl.plugin.active_playhead = FakePlayhead(position=48.0)
    ctrl._cur_view_mode = "source"
    ctrl._cur_clip_guid = "clip-host-is-on"

    state = ctrl.current_playback_state()

    assert state["current_time"]["value"] == 48.0
    assert state["view_mode"] == "source"
    assert state["clip_guid"] == "clip-host-is-on"


def test_snapshot_state_carries_the_view_even_when_visibility_lapsed():
    """A snapshot is an answer to "what is this session looking at", not a
    broadcast asserting a view, so the visibility lease does not gate it.

    An idle host's lease expires; it is still the screen a joiner must match.
    """
    ctrl = _controller()
    ctrl.plugin.active_playhead = FakePlayhead(position=48.0)
    ctrl.plugin.manager.owns_visibility = lambda *a, **k: False
    ctrl._cur_view_mode = "source"
    ctrl._cur_clip_guid = "clip-host-is-on"

    state = ctrl.current_playback_state()

    assert state["view_mode"] == "source"
    assert state["clip_guid"] == "clip-host-is-on"


# ── the joiner must apply it once it has a session ──────────────────────────


def test_join_adopt_retries_while_there_is_no_playhead():
    """The build parks an on-screen source applied on a later tick; the playhead
    arrives after that, so the first attempt is expected to find nothing."""
    ctrl = _controller(_SESSION_VIEW)
    ctrl.plugin.active_playhead = None
    applied: list = []
    ctrl.apply_playback_state = lambda s: applied.append(s)

    ctrl.apply_join_playback_state(attempt=0)

    assert applied == [], "nothing to apply to yet"
    assert ctrl.plugin._cmd_queue.items == [("apply_join_playback", {"attempt": 1})]


def test_join_adopt_applies_the_session_view_once_a_playhead_exists():
    ctrl = _controller(_SESSION_VIEW)
    ctrl.plugin.active_playhead = FakePlayhead()
    applied: list = []
    ctrl.apply_playback_state = lambda s: applied.append(s)

    ctrl.apply_join_playback_state(attempt=3)

    assert applied == [_SESSION_VIEW]
    assert ctrl.plugin._cmd_queue.items == [], "no further retry once applied"


def test_join_adopt_gives_up_rather_than_retrying_forever():
    ctrl = _controller(_SESSION_VIEW)
    ctrl.plugin.active_playhead = None

    ctrl.apply_join_playback_state(
        attempt=PlaybackSyncController._JOIN_PLAYBACK_MAX_ATTEMPTS
    )

    assert ctrl.plugin._cmd_queue.items == []


def test_a_seek_lost_to_a_replaced_playhead_is_deferred_not_dropped():
    """The switch to the session's clip is what destroys the playhead.

    So the seek that accompanies it is the one most likely to hit the stale-
    playhead branch — and for a joiner adopting an idle host's view there is no
    later message to correct it. Observed 2026-08-15 09:30:06.584, 2 ms after the
    joiner dispatched its own source switch: right clip, frame 0.
    """
    ctrl = _controller(_SESSION_VIEW)

    class DyingPlayhead:
        """Alive enough to set Loop Mode, gone by the time position is read."""

        frame_rate = types.SimpleNamespace(fps=lambda: 24.0)

        def get_attribute(self, name):
            return "Play Once"

        def set_attribute(self, name, value):
            pass

        @property
        def playing(self):
            raise RuntimeError("Dequeue timeout")

    ctrl.plugin.active_playhead = DyingPlayhead()
    ctrl._reacquire_active_playhead = lambda: None

    ctrl.apply_playback_state(_SESSION_VIEW)

    assert ctrl._pending_seek_frame == 48, (
        "the seek must survive the playhead that died under it"
    )
    assert ctrl._pending_seek_deadline > 0


def test_a_deferred_seek_lands_on_the_settled_playhead():
    ctrl = _controller(_SESSION_VIEW)
    ph = FakePlayhead(position=0.0)
    ctrl.plugin.active_playhead = ph
    ctrl._pending_seek_frame = 48
    ctrl._pending_seek_deadline = 0.0  # already due

    ctrl.apply_pending_seek()

    assert ph.position == 48
    assert ctrl._pending_seek_frame is None


def test_join_adopt_is_a_no_op_with_no_session_view():
    """A joiner into a session whose master never had a view has nothing to adopt."""
    ctrl = _controller(None)
    ctrl.plugin.active_playhead = FakePlayhead()
    applied: list = []
    ctrl.apply_playback_state = lambda s: applied.append(s)

    ctrl.apply_join_playback_state()

    assert applied == []
    assert ctrl.plugin._cmd_queue.items == []


if __name__ == "__main__":
    test_snapshot_state_carries_the_view()
    test_snapshot_state_carries_the_view_even_when_visibility_lapsed()
    test_join_adopt_retries_while_there_is_no_playhead()
    test_join_adopt_applies_the_session_view_once_a_playhead_exists()
    test_join_adopt_gives_up_rather_than_retrying_forever()
    test_join_adopt_is_a_no_op_with_no_session_view()
    print("PASS")
