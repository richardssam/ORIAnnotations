"""Tests for the remote-apply provenance span.

The span answers one question at the point a display change is noticed: *did a
peer cause this, or did the user?*  Every other signal available there is a
proxy for it, and the bypass this change closes is the case where every proxy
answers "the user" and the true answer is "a peer".

The assertions that matter most are the ones about the window *closing* — a
window wrongly left open attributes the user's own actions to a peer, which is
the failure mode of the fix rather than of the defect.
"""

import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import (  # noqa: E402
    NON_DISPLAY_EVENTS,
    SyncManager,
    STATE_JOINING,
    STATE_SYNCED,
)


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
def mgr():
    m = SyncManager(session_id="s", self_guid="me", network=FakeNetwork(), app_name="test")
    m.status = STATE_SYNCED
    return m


def _envelope(source="peer-1", schema="TIMELINE_1.0", event="RENAME_TIMELINE", data=None):
    return {
        "source_guid": source,
        "payload": {
            "command_schema": schema,
            "command": {
                "event": event,
                "payload": data if data is not None else {"timeline_guid": "tl-1", "name": "x"},
            },
        },
    }


# ---------------------------------------------------------------------------
# Inside an apply
# ---------------------------------------------------------------------------


def test_context_reports_the_source_peer_during_the_apply(mgr):
    seen = {}

    def spy(msg, data, source):
        seen["ctx"] = mgr.remote_apply_context()
        return None

    mgr._handlers[("TIMELINE_1.0", "RENAME_TIMELINE")] = spy
    mgr.apply_patch(_envelope(source="peer-1"))

    assert seen["ctx"]["source"] == "peer-1"
    assert seen["ctx"]["in_apply"] is True
    assert seen["ctx"]["settling_for"] is None
    assert seen["ctx"]["command_schema"] == "TIMELINE_1.0"
    assert seen["ctx"]["event"] == "RENAME_TIMELINE"


def test_no_context_before_anything_has_been_applied(mgr):
    assert mgr.remote_apply_context() is None


# ---------------------------------------------------------------------------
# The settle window
# ---------------------------------------------------------------------------


def test_context_survives_the_apply_for_the_settle_window(mgr):
    """The display change lands after the apply returns — that is the point."""
    mgr.apply_patch(_envelope(source="peer-1"))

    ctx = mgr.remote_apply_context()
    assert ctx is not None
    assert ctx["source"] == "peer-1"
    assert ctx["in_apply"] is False
    assert ctx["settling_for"] >= 0


def test_context_closes_once_the_settle_window_expires(mgr):
    mgr.remote_apply_settle_seconds = 0.0
    mgr.apply_patch(_envelope(source="peer-1"))

    # Any elapsed time at all is past a zero-length window.  Asserted this way
    # rather than by sleeping, so the test cannot become slow or flaky.
    assert mgr.remote_apply_context() is None


def test_settle_window_is_tunable_per_manager(mgr):
    """Task 3.4 re-sizes this from real traces; it must not be a constant."""
    mgr.remote_apply_settle_seconds = 3600.0
    mgr.apply_patch(_envelope(source="peer-1"))
    assert mgr.remote_apply_context() is not None


# ---------------------------------------------------------------------------
# Nesting and failure — a window must never be left open
# ---------------------------------------------------------------------------


def test_nested_applies_do_not_leave_a_window_open(mgr):
    """A handler that applies another message must not strand the outer frame."""
    inner_saw = {}

    def inner(msg, data, source):
        inner_saw["source"] = mgr.remote_apply_context()["source"]
        return None

    def outer(msg, data, source):
        mgr.apply_patch(_envelope(source="peer-2", event="REMOVE_TIMELINE"))
        # Back in the outer apply: the inner frame is gone and the surviving
        # frame is the outer one, with its own peer — not peer-2.
        assert mgr.remote_apply_context()["source"] == "peer-1"
        assert mgr.remote_apply_context()["in_apply"] is True
        return None

    mgr._handlers[("TIMELINE_1.0", "RENAME_TIMELINE")] = outer
    mgr._handlers[("TIMELINE_1.0", "REMOVE_TIMELINE")] = inner
    mgr.apply_patch(_envelope(source="peer-1"))

    assert inner_saw["source"] == "peer-2"
    assert mgr._remote_apply_stack == []


def test_a_raising_handler_still_closes_its_frame(mgr):
    def boom(msg, data, source):
        raise RuntimeError("handler blew up")

    mgr._handlers[("TIMELINE_1.0", "RENAME_TIMELINE")] = boom
    with pytest.raises(RuntimeError):
        mgr.apply_patch(_envelope(source="peer-1"))

    assert mgr._remote_apply_stack == []
    mgr.remote_apply_settle_seconds = 0.0
    assert mgr.remote_apply_context() is None


# ---------------------------------------------------------------------------
# Messages that are never applied open no window
# ---------------------------------------------------------------------------


def test_own_message_opens_no_window(mgr):
    mgr.apply_patch(_envelope(source="me"))
    assert mgr.remote_apply_context() is None


def test_message_buffered_while_joining_opens_no_window(mgr):
    """Buffered now, applied on replay — the window belongs to the replay."""
    mgr.status = STATE_JOINING
    mgr.apply_patch(_envelope(source="peer-1"))
    assert mgr.remote_apply_context() is None
    assert len(mgr._delta_buffer) == 1


def test_unknown_command_opens_no_window(mgr):
    mgr.apply_patch(_envelope(schema="NOT_A_SCHEMA", event="NOPE", data={}))
    assert mgr.remote_apply_context() is None


# ---------------------------------------------------------------------------
# Plumbing must not hold the window open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema,event", sorted(NON_DISPLAY_EVENTS))
def test_plumbing_opens_no_window(mgr, schema, event):
    """The heartbeat is the one that matters, and it is the one that bit.

    A 5 s liveness heartbeat against a 5 s settle window keeps the window open
    permanently: the first instrumented soak blamed ``PEER_ANNOUNCE`` for all 12
    of the host's own selection events.
    """
    mgr._handlers[(schema, event)] = lambda msg, data, source: None
    mgr.apply_patch(_envelope(source="peer-1", schema=schema, event=event, data={}))
    assert mgr.remote_apply_context() is None


def test_plumbing_does_not_extend_a_real_window(mgr):
    """A heartbeat arriving mid-settle must not refresh someone else's window."""
    mgr.apply_patch(_envelope(source="peer-1"))
    first = mgr.remote_apply_context()
    assert first is not None

    mgr._handlers[("LiveSession.1", "PEER_ANNOUNCE")] = lambda m, d, s: None
    mgr.apply_patch(_envelope(
        source="peer-2", schema="LiveSession.1", event="PEER_ANNOUNCE", data={},
    ))

    still = mgr.remote_apply_context()
    assert still is not None
    # Same window as before — the heartbeat neither replaced nor restarted it.
    assert still["source"] == "peer-1"
    assert still["event"] == "RENAME_TIMELINE"
    assert still["settling_for"] >= first["settling_for"]


def test_structure_and_position_do_open_a_window(mgr):
    """The denylist must not have swallowed the messages that matter."""
    for schema, event in (
        ("TIMELINE_1.0", "ADD_TIMELINE"),
        ("PLAYBACK_SETTINGS_1.0", "SET"),
        ("LiveSession.1", "STATE_SNAPSHOT"),
    ):
        assert (schema, event) not in NON_DISPLAY_EVENTS, (schema, event)
