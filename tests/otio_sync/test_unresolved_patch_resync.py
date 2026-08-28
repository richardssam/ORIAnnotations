"""Tests for unresolved-patch-resync.

A peer handed a structural patch naming an object it does not hold records
the fact and — before this capability — did nothing about it, forever. That
is what left xStudio holding zero objects for the remaining ~45s of
``contended_position_scrub`` after a master handover cost it the establishing
``ADD_TIMELINE``.

The repair is deliberately *not* a widened ``structure_diverged``: these tests
pin that separation as hard as they pin the request itself, because
``structure-divergence-recovery`` earns its trustworthiness at the UI from
divergence meaning exactly one thing.
"""

import os
import sys
import time as time_module

import opentimelineio as otio
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import authority  # noqa: E402
from otio_sync_core.manager import (  # noqa: E402
    SyncManager,
    STATE_SYNCED,
    RECOVERY_COOLDOWN,
    UNRESOLVED_SETTLE,
)
from otio_sync_core.patcher import OTIOPatcher  # noqa: E402
from otio_sync_core import protocol_messages as pm  # noqa: E402

ABSENT = "c7b6b324-0000-0000-0000-000000000000"


class FakeNetwork:
    def __init__(self):
        self.sent = []

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payloads(self):
        return []

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def _enforcement_on(monkeypatch):
    monkeypatch.delenv(authority.ROLE_ENFORCEMENT_ENV, raising=False)
    monkeypatch.delenv(authority.OWNERSHIP_ENFORCEMENT_ENV, raising=False)


def _manager(guid="self-guid", master_guid="master-guid"):
    mgr = SyncManager(
        session_id="s",
        self_guid=guid,
        network=FakeNetwork(),
        app_name="xstudio",
        identity_override={"user": "alice"},
        default_role=authority.DRIVER,
    )
    mgr.status = STATE_SYNCED
    mgr.master_guid = master_guid
    return mgr


def _events(mgr, event):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command"]["event"] == event
    ]


def _insert_child(mgr, parent_uuid, name="new.mov"):
    """Deliver an ``INSERT_CHILD`` from a peer, as the network would."""
    msg = pm.InsertChild(
        parent_uuid=parent_uuid,
        index=-1,
        child_data=otio.schema.Clip(name=name),
        sync_timestamp=time_module.time(),
    )
    return mgr.apply_patch({
        "session": "s",
        "source_guid": "other-peer",
        "payload": {
            "command_schema": msg.SCHEMA,
            "command": {"event": msg.EVENT, "payload": msg.to_payload()},
        },
    })


def _clock(monkeypatch, start=1000.0):
    t = [start]
    monkeypatch.setattr(time_module, "monotonic", lambda: t[0])
    return t


# ---------------------------------------------------------------------------
# The resync
#
# The reproduction these grew from (tasks.md 1.1) was the first of them with
# its assertion inverted: an INSERT_CHILD against an absent target, and no
# state request ever. It passed before the tick() branch existed and fails
# after, so it is not kept alongside its successor below.
# ---------------------------------------------------------------------------

def test_a_patch_against_an_absent_target_resyncs_the_peer(monkeypatch):
    """Spec scenario 1: still absent when the window expires, so ask."""
    mgr = _manager()
    t = _clock(monkeypatch)

    _insert_child(mgr, ABSENT, name="car_ACES_sRGB.mov")
    assert mgr.unresolved_patch_count == 1
    assert len(mgr._object_map) == 0, "the media had nowhere to land"

    t[0] += UNRESOLVED_SETTLE * 0.5
    mgr.tick()
    assert _events(mgr, pm.StateRequest.EVENT) == [], "not past the window yet"

    t[0] += UNRESOLVED_SETTLE * 0.6
    mgr.tick()

    assert len(_events(mgr, pm.StateRequest.EVENT)) == 1
    assert mgr.state_request_reason == "recovery"

    # ...and the answer closes the gap the patch could not: the peer ends up
    # holding the media it had nowhere to put.
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": ABSENT}
    tl.tracks = otio.schema.Stack("tracks")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(otio.schema.Clip(name="car_ACES_sRGB.mov"))
    tl.tracks.append(track)
    mgr.apply_snapshot(pm.StateSnapshot(
        target_guid=mgr.self_guid,
        timelines={ABSENT: tl},
        active_timeline_guid=ABSENT,
        snapshot_timestamp=time_module.time(),
    ).to_payload())

    assert mgr.status == STATE_SYNCED
    assert ABSENT in mgr._object_map
    names = [
        o.name for o in mgr._object_map.values()
        if isinstance(o, otio.schema.Clip)
    ]
    assert "car_ACES_sRGB.mov" in names


def test_a_peer_that_was_merely_behind_does_not_request_state(monkeypatch):
    """Spec scenario 2 — the false positive structure-divergence-recovery warns of.

    An unresolved patch cannot, at the instant it fails, tell a diverged peer
    from one that has not caught up. Time is what separates them: only one of
    the two clears itself, and this is that one.
    """
    mgr = _manager()
    t = _clock(monkeypatch)

    _insert_child(mgr, ABSENT)
    assert mgr.unresolved_targets

    # The establishing ADD_TIMELINE lands, late but inside the window.
    t[0] += UNRESOLVED_SETTLE * 0.5
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": ABSENT}
    mgr.register_timeline(tl)

    t[0] += UNRESOLVED_SETTLE * 10
    mgr.tick()

    assert _events(mgr, pm.StateRequest.EVENT) == []
    assert mgr.unresolved_targets == {}, "a resolved target leaves nothing behind"


def test_a_burst_against_one_missing_target_requests_once(monkeypatch):
    """One gap costs one request, however many children name it."""
    mgr = _manager()
    t = _clock(monkeypatch)

    for i in range(5):
        _insert_child(mgr, ABSENT, name=f"clip_{i}.mov")
        t[0] += 0.01

    assert mgr.unresolved_patch_count == 5
    assert len(mgr.unresolved_targets) == 1, "keyed by target, not by patch"

    t[0] += UNRESOLVED_SETTLE + 0.01
    mgr.tick()
    mgr.status = STATE_SYNCED  # the request left; pretend it is answered
    t[0] += 0.01
    mgr.tick()

    assert len(_events(mgr, pm.StateRequest.EVENT)) == 1


def test_a_master_holding_an_unapplied_patch_records_unreachable(monkeypatch):
    """A master has no higher authority to ask, so it says so instead."""
    mgr = _manager(master_guid="self-guid")
    mgr.is_master = True
    t = _clock(monkeypatch)

    _insert_child(mgr, ABSENT)
    t[0] += UNRESOLVED_SETTLE + 0.01

    events = mgr.tick()

    assert _events(mgr, pm.StateRequest.EVENT) == []
    assert mgr._resync_unreachable is True
    assert any(a == "unresolved_resync_failed" for a, _ in events)
    assert mgr.status == STATE_SYNCED


def test_a_peer_with_no_master_records_unreachable_then_asks_when_one_appears(monkeypatch):
    mgr = _manager(master_guid=None)
    t = _clock(monkeypatch)

    _insert_child(mgr, ABSENT)
    t[0] += UNRESOLVED_SETTLE + 0.01
    events = mgr.tick()

    assert _events(mgr, pm.StateRequest.EVENT) == []
    assert mgr._resync_unreachable is True
    assert any(a == "unresolved_resync_failed" for a, _ in events)

    mgr.master_guid = "master-guid"
    t[0] += RECOVERY_COOLDOWN + 0.01
    mgr.tick()

    assert len(_events(mgr, pm.StateRequest.EVENT)) == 1
    assert mgr._resync_unreachable is False


def test_resync_leaves_the_divergence_state_alone(monkeypatch):
    """Being told about a change is not the same as making one.

    ``structure-divergence-recovery`` declares divergence from the *attempt* to
    change structure, never from observed mismatch, and reports it to the user.
    A receiving peer has attempted nothing, so nothing here may set the flag —
    nor clear one this peer legitimately earned.
    """
    mgr = _manager()
    t = _clock(monkeypatch)

    _insert_child(mgr, ABSENT)
    t[0] += UNRESOLVED_SETTLE + 0.01
    mgr.tick()

    assert len(_events(mgr, pm.StateRequest.EVENT)) == 1
    assert mgr.structure_diverged is False, "a resync is not a divergence"

    # And a divergence this peer did earn is not cleared by the resync path.
    mgr.status = STATE_SYNCED
    mgr._note_structure_diverged("test", "earned")
    _insert_child(mgr, ABSENT)
    t[0] += RECOVERY_COOLDOWN + UNRESOLVED_SETTLE + 0.01
    mgr.tick()

    assert mgr.structure_diverged is True


def test_applying_a_snapshot_clears_the_retained_targets(monkeypatch):
    """The answer to the request ends the condition that raised it."""
    mgr = _manager()
    t = _clock(monkeypatch)

    _insert_child(mgr, ABSENT)
    assert mgr.unresolved_targets

    t[0] += UNRESOLVED_SETTLE + 0.01
    mgr.tick()

    mgr.apply_snapshot(pm.StateSnapshot(
        target_guid=mgr.self_guid,
        timelines={},
        active_timeline_guid=None,
        snapshot_timestamp=time_module.time(),
    ).to_payload())

    assert mgr.unresolved_targets == {}
    assert mgr.status == STATE_SYNCED


def test_the_retained_set_is_bounded(monkeypatch):
    """A peer failing continuously cannot grow the record without limit."""
    mgr = _manager()
    _clock(monkeypatch)

    for i in range(200):
        _insert_child(mgr, f"absent-{i:04d}")

    assert mgr.unresolved_patch_count == 200
    assert len(mgr.unresolved_targets) <= OTIOPatcher._UNRESOLVED_TARGETS
