"""Field-strip logging says what happened once, not once per message.

A follower broadcasts position on every rendered frame while the user scrubs,
and enforcement strips the same field group from every one of them.  Logging
each strip buried the log: the 2026-08-09 12:27 session recorded 36 identical
``stripped visibility fields`` lines in three minutes, all naming the same clip
and the same host.

Collapsing a run is only safe if three things stay true, so each is pinned
here: a *change* still logs, a run still reports its length, and a run that is
still going still says so.
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../python")))

from otio_sync_core import authority  # noqa: E402
from otio_sync_core import manager as manager_mod  # noqa: E402
from otio_sync_core.manager import SyncManager, STATE_SYNCED  # noqa: E402


class FakeNetwork:
    def __init__(self):
        self.sent = []

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payloads(self):
        return []

    def stop(self):
        pass


@pytest.fixture
def logged(monkeypatch):
    """Capture what reaches the log, in order."""
    lines = []
    monkeypatch.setattr(manager_mod, "_log", lines.append)
    return lines


@pytest.fixture
def follower(monkeypatch):
    """A synced non-host peer: its visibility fields are always stripped."""
    monkeypatch.delenv(authority.ENFORCEMENT_ENV, raising=False)
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "0")
    mgr = SyncManager(
        session_id="s", self_guid="guid-rv", network=FakeNetwork(), app_name="openrv"
    )
    mgr.status = STATE_SYNCED
    mgr.host_guid = "guid-host"
    mgr.is_host = False
    return mgr


def _view_state(clip="clip-abc", mode="source", frame=61.0):
    return {
        "playing": True,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": frame, "rate": 24.0},
        "playback_mode": "loop",
        "view_mode": mode,
        "clip_guid": clip,
    }


def _strip_lines(lines):
    return [l for l in lines if "stripped visibility fields" in l]


# ── the regression ──────────────────────────────────────────────────────────


def test_a_scrub_of_identical_strips_logs_once(follower, logged):
    """36 messages, one line. The position changes every frame; the view does not."""
    for frame in range(36):
        follower.broadcast_playback_state(_view_state(frame=float(frame)))

    assert len(_strip_lines(logged)) == 1


def test_the_one_line_still_names_the_view_and_the_host(follower, logged):
    """Collapsing must not cost the detail that made the line worth reading."""
    for frame in range(5):
        follower.broadcast_playback_state(_view_state(frame=float(frame)))

    line = _strip_lines(logged)[0]
    assert "'source'" in line
    assert "clip-abc" in line
    assert "guid-hos" in line  # host guid, truncated to 8


def test_every_message_is_still_stripped(follower):
    """The log collapses; the enforcement does not."""
    for frame in range(10):
        follower.broadcast_playback_state(_view_state(frame=float(frame)))

    payloads = [
        e["payload"]["command"]["payload"]
        for e in follower.network.sent
        if e["payload"]["command_schema"] == "PLAYBACK_SETTINGS_1.0"
    ]
    assert len(payloads) == 10
    assert not any("view_mode" in p or "clip_guid" in p for p in payloads)


# ── what must survive the collapse ──────────────────────────────────────────


def test_a_different_clip_logs_again(follower, logged):
    follower.broadcast_playback_state(_view_state(clip="clip-abc"))
    follower.broadcast_playback_state(_view_state(clip="clip-xyz"))

    lines = _strip_lines(logged)
    assert len(lines) == 2
    assert "clip-xyz" in lines[1]


def test_a_different_mode_logs_again(follower, logged):
    follower.broadcast_playback_state(_view_state(mode="source"))
    follower.broadcast_playback_state(_view_state(mode="sequence"))

    assert len(_strip_lines(logged)) == 2


def test_a_new_host_logs_again(follower, logged):
    """Same view, different host — the reason the strip happened has changed."""
    follower.broadcast_playback_state(_view_state())
    follower.host_guid = "guid-other"
    follower.broadcast_playback_state(_view_state())

    assert len(_strip_lines(logged)) == 2


def test_the_line_ending_a_run_reports_how_long_it_was(follower, logged):
    """Otherwise collapsing hides the scale, which is itself the finding — it is
    what showed the strips were per scrub frame."""
    for frame in range(9):
        follower.broadcast_playback_state(_view_state(clip="clip-abc", frame=float(frame)))
    follower.broadcast_playback_state(_view_state(clip="clip-xyz"))

    assert "repeated 8x" in _strip_lines(logged)[1]


def test_a_run_that_never_changes_still_reports_periodically(follower, logged):
    """A peer stuck stripping for minutes must not go silent while it is stuck —
    the trailing count alone would never be emitted."""
    beat = SyncManager.STRIP_LOG_HEARTBEAT
    for frame in range(2 * beat + 1):
        follower.broadcast_playback_state(_view_state(frame=float(frame)))

    lines = _strip_lines(logged)
    assert len(lines) == 3  # the first, plus one per heartbeat
    assert f"{beat} identical so far" in lines[1]
    assert f"{2 * beat} identical so far" in lines[2]


# ── the categories are independent ──────────────────────────────────────────


def test_visibility_and_position_runs_do_not_mask_each_other(follower, logged):
    """Tracked per category: a steady visibility strip must not swallow the
    first position strip, which is a different fact about a different lease.

    Driven at the helper, because making one message lose both groups needs an
    ownership setup that is covered in test_broadcast_ownership.
    """
    for _ in range(3):
        follower._log_field_strip(authority.VISIBILITY, ("source", "c1"), "vis")
        follower._log_field_strip(authority.POSITION, "owner-1", "pos")

    assert logged == ["vis", "pos"]


def test_a_category_alternating_with_itself_logs_each_change(follower, logged):
    for key in ("a", "b", "a"):
        follower._log_field_strip(authority.VISIBILITY, key, f"vis-{key}")

    assert logged == ["vis-a", "vis-b", "vis-a"]
