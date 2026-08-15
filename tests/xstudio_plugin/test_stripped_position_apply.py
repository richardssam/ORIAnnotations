#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""A playback message with no position fields must not move xStudio's playhead.

Position is stripped on the way out when the sender may not assert it — it does
not hold the position lease (``broadcast-ownership``), or its session role
forbids the field group (``session-roles``). "Absent" therefore means *not mine
to say*, and reading it as a value turns silence into an assertion.

The failure this covers was observed on 2026-08-12: an OpenRV peer holding the
``viewer`` role scrubbed, its playback message left with every position and
visibility field stripped, and the xStudio host jumped to the start of the view
each time — a viewer driving the session it is expressly not permitted to drive.
``current_time.get("value", 0)`` is the whole of the mechanism.

The OpenRV plugin already had this guard (``StrippedPositionTest`` in
tests/otio_sync/test_playback_view_dispatch.py) and xStudio did not, which is
the hand-replication drift the shared core exists to prevent — so this is the
same property, asserted against the other host.

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
    default_timeout_ms = 2000


class FakePlayhead:
    def __init__(self, position: float = 0.0, playing: bool = True) -> None:
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
        self.active_playhead = None

    def stamp_remote_apply(self, channel: str) -> None:
        pass

    def claim_lease(self, channel: str) -> None:
        pass


def _controller():
    ctrl = PlaybackSyncController(FakePlugin())
    # The playhead must sit somewhere other than the frame a stripped message
    # would compute (value 0), or the erroneous seek is a no-op and the test
    # passes for the wrong reason. Playing, likewise, so an erroneous
    # playing=False is observable as a stop.
    ctrl.plugin.active_playhead = FakePlayhead(position=100.0, playing=True)
    ctrl.get_local_viewed_timeline_guid = lambda: "tl-1"
    return ctrl


def test_a_wholly_stripped_message_does_not_move_the_playhead():
    """Exactly what a ``viewer``'s message looks like on the wire."""
    ctrl = _controller()
    ph = ctrl.plugin.active_playhead

    ctrl.apply_playback_state({"timeline_guid": "tl-1", "sync_timestamp": 1.0})

    assert ph.position == 100.0, "a message asserting nothing moved the playhead"
    assert ph.playing is True, "a message asserting nothing stopped playback"


def test_a_view_only_message_does_not_move_the_playhead():
    """The lease-stripped shape: the sender is host but does not hold position."""
    ctrl = _controller()
    ph = ctrl.plugin.active_playhead

    ctrl.apply_playback_state(
        {"view_mode": "sequence", "timeline_guid": "tl-1", "clip_guid": None}
    )

    assert ph.position == 100.0
    assert ph.playing is True


def test_a_message_carrying_position_still_applies():
    """The guard must not swallow ordinary traffic — a test that only proved
    nothing was applied would pass against a peer that had stopped syncing."""
    ctrl = _controller()
    ph = ctrl.plugin.active_playhead

    ctrl.apply_playback_state(
        {
            "playing": False,
            "current_time": {"value": 63.0},
            "playback_mode": "loop",
            "timeline_guid": "tl-1",
        }
    )

    assert ph.position == 63.0
    assert ph.playing is False


# ---------------------------------------------------------------------------
# An annotation must not move what the session is looking at
# ---------------------------------------------------------------------------
#
# Applying a peer's stroke makes xStudio *show* the annotated clip. That
# show_atom was being read as a fresh local isolation, so this peer broadcast
# mode=source with "forcing frame=0" — resetting the drawing peer's playhead
# mid-stroke, after which the rest of its live stroke arrived stamped frame 0
# (2026-08-13 11:21:08, a reviewer annotating while the driver drove).
#
# The predicate has to be narrow in two directions at once, and these tests pin
# both: an annotation within the window suppresses, a *playback* message never
# does (the existing echo guards own that case), and neither does an annotation
# older than the causal window — provenance stays true for 5 s, and suppressing
# on that alone would stop this peer broadcasting its own view changes for five
# seconds after every stroke anyone drew.


class FakeManagerWithProvenance:
    def __init__(self, ctx=None):
        self.status = STATE_SYNCED
        self.active_timeline_guid = "tl-1"
        self._ctx = ctx

    def remote_apply_context(self):
        return self._ctx


def _ctx(schema, event, settling_for=0.05, in_apply=False):
    return {
        "source": "peer-guid",
        "command_schema": schema,
        "event": event,
        "age": 2.13,
        "settling_for": settling_for,
        "in_apply": in_apply,
    }


def _ctrl_with(ctx):
    ctrl = PlaybackSyncController(FakePlugin())
    ctrl.plugin.manager = FakeManagerWithProvenance(ctx)
    return ctrl


def test_a_live_stroke_apply_suppresses_a_view_broadcast():
    ctrl = _ctrl_with(_ctx("Annotation.1", "PARTIAL"))
    assert ctrl._induced_by_remote_apply() is True


def test_a_committed_annotation_apply_suppresses_too():
    ctrl = _ctrl_with(_ctx("OTIO_SESSION_1.0", "INSERT_CHILD"))
    assert ctrl._induced_by_remote_apply() is True


def test_still_inside_the_apply_counts():
    ctrl = _ctrl_with(_ctx("Annotation.1", "PARTIAL", settling_for=None, in_apply=True))
    assert ctrl._induced_by_remote_apply() is True


def test_a_playback_apply_does_not_suppress():
    """A remote playback message legitimately changes this peer's view; the
    existing echo guards own that case, and swallowing it here would stop a
    genuine local view change from ever reaching the session."""
    ctrl = _ctrl_with(_ctx("PLAYBACK_SETTINGS_1.0", "SET"))
    assert ctrl._induced_by_remote_apply() is False


def test_an_old_annotation_apply_does_not_suppress():
    """Provenance stays true for 5 s. The causal chain is sub-second, and the
    difference decides whether a user's own double-click propagates."""
    ctrl = _ctrl_with(_ctx("Annotation.1", "PARTIAL", settling_for=2.5))
    assert ctrl._induced_by_remote_apply() is False


def test_applying_a_session_snapshot_suppresses_a_view_broadcast():
    """Joining a session is not a user action.

    The 2026-08-15 09:07 regression: a peer joined a session whose host was on a
    later clip mid-shot. Building the session put the snapshot's *first* timeline
    on screen, the show_atom was read as a fresh local isolation, and the joiner
    broadcast mode=source with frame 0 — dragging the host onto the first clip at
    frame 1. Nothing about joining may change what the session is viewing.
    """
    ctrl = _ctrl_with(_ctx("LiveSession.1", "STATE_SNAPSHOT", settling_for=1.63))
    assert ctrl._induced_by_remote_apply() is True


def test_a_snapshot_gets_a_longer_window_than_an_annotation():
    """A session build trails its apply by far more than a stroke does.

    1.63 s was observed live — past the annotation window, well inside the
    snapshot one. If both used the annotation's, this would not be suppressed.
    """
    settling = 2.5  # past the annotation window, inside the snapshot one
    snapshot = _ctrl_with(_ctx("LiveSession.1", "STATE_SNAPSHOT", settling_for=settling))
    annotation = _ctrl_with(_ctx("Annotation.1", "PARTIAL", settling_for=settling))

    assert snapshot._induced_by_remote_apply() is True
    assert annotation._induced_by_remote_apply() is False


def test_a_long_stale_snapshot_still_expires():
    """A window, not a licence — a user who joins and then picks a clip must
    eventually be able to."""
    ctrl = _ctrl_with(_ctx("LiveSession.1", "STATE_SNAPSHOT", settling_for=9.0))
    assert ctrl._induced_by_remote_apply() is False


def test_no_remote_context_means_a_local_action():
    ctrl = _ctrl_with(None)
    assert ctrl._induced_by_remote_apply() is False


def test_an_unreadable_context_is_treated_as_local():
    """A broken provenance read must not silently stop this peer broadcasting."""
    class Boom:
        status = STATE_SYNCED
        active_timeline_guid = "tl-1"

        def remote_apply_context(self):
            raise RuntimeError("actor gone")

    ctrl = PlaybackSyncController(FakePlugin())
    ctrl.plugin.manager = Boom()

    assert ctrl._induced_by_remote_apply() is False
