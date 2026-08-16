"""Tests for structure-divergence-recovery.

A peer whose role or write lease forbids structure keeps making local
structural edits regardless — ``insert_child``'s docstring calls this out as
deliberate: the insert always happens locally, only the broadcast is
role-gated. Nothing previously told the session, or this peer, that the two
had now diverged. These tests cover the three places that gains a second
consequence (``manager.py`` ``insert_child`` / ``broadcast_move_child`` /
``broadcast_remove_child``), the debounced recovery trigger in ``tick()``, and
``request_state``/``apply_snapshot`` reachable mid-session rather than only
while joining.
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
    STATE_DISCOVERING,
    DIVERGENCE_SETTLE,
    RECOVERY_COOLDOWN,
)
from otio_sync_core import protocol_messages as pm  # noqa: E402


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


def _timeline(mgr, guid="tl-1", track_guid="track-1"):
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": guid}
    tl.tracks = otio.schema.Stack("tracks")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.metadata["sync"] = {"guid": track_guid}
    tl.tracks.append(track)
    clip = otio.schema.Clip(name="existing.mov")
    track.append(clip)
    mgr.register_timeline(tl)
    clip_guid = clip.metadata["sync"]["guid"]
    return track_guid, clip_guid


def _manager(role=authority.DRIVER, guid="self-guid", master_guid="master-guid"):
    mgr = SyncManager(
        session_id="s",
        self_guid=guid,
        network=FakeNetwork(),
        app_name="openrv",
        identity_override={"user": "alice"},
        default_role=role,
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


# ---------------------------------------------------------------------------
# 1. insert_child's return value
# ---------------------------------------------------------------------------

def test_insert_child_reports_sent_when_broadcast():
    mgr = _manager()
    _timeline(mgr)

    status = mgr.insert_child("track-1", otio.schema.Clip(name="new.mov"))

    assert status == authority.SENT
    assert len(_events(mgr, pm.InsertChild.EVENT)) == 1


def test_insert_child_reports_suppressed_when_role_forbids():
    mgr = _manager(role=authority.VIEWER)
    _timeline(mgr)

    status = mgr.insert_child("track-1", otio.schema.Clip(name="new.mov"))

    assert status == authority.SUPPRESSED
    assert _events(mgr, pm.InsertChild.EVENT) == []


# ---------------------------------------------------------------------------
# 2. Attribution: which refusals mark divergence
# ---------------------------------------------------------------------------

def test_a_role_refused_remove_child_marks_divergence():
    mgr = _manager(role=authority.VIEWER)
    _, clip_guid = _timeline(mgr)

    status = mgr.broadcast_remove_child("track-1", clip_guid)

    assert status == authority.SUPPRESSED
    assert mgr.structure_diverged is True


def test_a_role_refused_move_child_marks_divergence():
    mgr = _manager(role=authority.VIEWER)
    track_guid, clip_guid = _timeline(mgr)
    mgr.insert_child(track_guid, otio.schema.Clip(name="second.mov"))
    # insert_child above was itself refused (viewer), so do it as the role
    # that may broadcast, then re-lower the role to isolate the move refusal.
    mgr._self_role = authority.DRIVER
    mgr.insert_child(track_guid, otio.schema.Clip(name="second.mov"))
    mgr._self_role = authority.VIEWER

    status = mgr.broadcast_move_child(track_guid, clip_guid, 1)

    assert status == authority.SUPPRESSED
    assert mgr.structure_diverged is True


def test_a_role_refused_structural_insert_marks_divergence():
    mgr = _manager(role=authority.VIEWER)
    _timeline(mgr)

    mgr.insert_child("track-1", otio.schema.Clip(name="new.mov"), group=authority.STRUCTURE)

    assert mgr.structure_diverged is True


def test_a_permitted_structural_broadcast_does_not_mark_divergence():
    mgr = _manager(role=authority.DRIVER)
    track_guid, clip_guid = _timeline(mgr)
    mgr.claim_category(authority.CHANNEL_STRUCTURE)

    mgr.insert_child(track_guid, otio.schema.Clip(name="new.mov"))
    mgr.broadcast_move_child(track_guid, clip_guid, 1)
    mgr.broadcast_remove_child(track_guid, clip_guid)

    assert mgr.structure_diverged is False


def test_a_suppressed_playback_message_is_not_a_structural_divergence():
    """Spec: withheld or field-stripped playback traffic must never trip this."""
    mgr = _manager(role=authority.VIEWER)
    _timeline(mgr)

    status = mgr.broadcast_playback_state({"playing": True, "current_time": {
        "OTIO_SCHEMA": "RationalTime.1", "value": 1.0, "rate": 24.0,
    }})

    assert status == authority.SUPPRESSED
    assert mgr.structure_diverged is False


def test_a_permitted_annotation_insert_is_not_a_structural_divergence():
    """Spec: a reviewer may emit an annotation while it may not reshape structure."""
    mgr = _manager(role=authority.REVIEWER)
    _timeline(mgr)

    status = mgr.insert_child(
        "track-1", otio.schema.Clip(name="ann.mov"), group=authority.ANNOTATION
    )

    assert status == authority.SENT
    assert mgr.structure_diverged is False


def test_a_role_refused_annotation_insert_is_not_a_structural_divergence():
    """The group gate: a VIEWER refused on ANNOTATION must not be read as STRUCTURE."""
    mgr = _manager(role=authority.VIEWER)
    _timeline(mgr)

    status = mgr.insert_child(
        "track-1", otio.schema.Clip(name="ann.mov"), group=authority.ANNOTATION
    )

    assert status == authority.SUPPRESSED
    assert mgr.structure_diverged is False


def test_a_lease_refusal_marks_divergence_when_ownership_enforced(monkeypatch):
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "1")
    mgr = _manager(role=authority.DRIVER)
    _, clip_guid = _timeline(mgr)
    mgr._leases[authority.CHANNEL_STRUCTURE].owner_guid = "someone-else"
    mgr._leases[authority.CHANNEL_STRUCTURE].deadline = time_module.monotonic() + 60

    status = mgr.broadcast_remove_child("track-1", clip_guid)

    assert status == authority.SUPPRESSED
    assert mgr.structure_diverged is True


def test_a_lease_refusal_marks_nothing_when_ownership_disabled(monkeypatch):
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "0")
    mgr = _manager(role=authority.DRIVER)
    _, clip_guid = _timeline(mgr)
    mgr._leases[authority.CHANNEL_STRUCTURE].owner_guid = "someone-else"
    mgr._leases[authority.CHANNEL_STRUCTURE].deadline = time_module.monotonic() + 60

    status = mgr.broadcast_remove_child("track-1", clip_guid)

    assert status == authority.SENT
    assert mgr.structure_diverged is False


def test_a_patcher_noop_does_not_mark_divergence():
    """DRIVER, lease held, but the child does not exist — nothing to diverge over."""
    mgr = _manager(role=authority.DRIVER)
    _timeline(mgr)
    mgr.claim_category(authority.CHANNEL_STRUCTURE)

    status = mgr.broadcast_remove_child("track-1", "no-such-child")

    assert status == authority.SUPPRESSED
    assert mgr.structure_diverged is False


def test_a_remote_apply_does_not_mark_divergence():
    """``_is_syncing`` — the change came from the session, not from this peer."""
    mgr = _manager(role=authority.VIEWER)
    _, clip_guid = _timeline(mgr)
    mgr._is_syncing = True

    status = mgr.broadcast_remove_child("track-1", clip_guid)

    assert status == authority.SUPPRESSED
    assert mgr.structure_diverged is False


# ---------------------------------------------------------------------------
# 3. request_state reachable from STATE_SYNCED; recovery vs. join timeout
# ---------------------------------------------------------------------------

def test_a_synced_peer_can_request_state_without_leaving():
    mgr = _manager()

    mgr.request_state(reason="recovery")

    assert len(_events(mgr, pm.StateRequest.EVENT)) == 1
    # No session-plumbing departure message of any kind went out for this.
    depart_events = [
        e["payload"]["command"]["event"]
        for e in mgr.network.sent
        if "DEPART" in e["payload"]["command"]["event"].upper()
    ]
    assert depart_events == []


def test_deltas_during_a_rerequest_are_buffered_and_replayed():
    mgr = _manager()
    track_guid, _ = _timeline(mgr)
    mgr.structure_diverged = True

    mgr.request_state(reason="recovery")
    assert mgr.status == "JOINING"

    clip = otio.schema.Clip(name="late.mov")
    msg = pm.InsertChild(
        parent_uuid=track_guid, index=-1, child_data=clip,
        sync_timestamp=time_module.time() + 5,
    )
    envelope = {
        "session": "s",
        "source_guid": "other-peer",
        "payload": {
            "command_schema": msg.SCHEMA,
            "command": {"event": msg.EVENT, "payload": msg.to_payload()},
        },
    }
    mgr.apply_patch(envelope)
    assert len(mgr._delta_buffer) == 1

    snapshot = pm.StateSnapshot(
        target_guid=mgr.self_guid,
        timelines=dict(mgr._timelines),
        active_timeline_guid="tl-1",
        snapshot_timestamp=time_module.time(),
    ).to_payload()
    results = mgr.apply_snapshot(snapshot)

    assert any(action == "insert_child" for action, _ in results)
    assert mgr.status == STATE_SYNCED


def test_an_unanswered_recovery_request_returns_to_synced_not_discovering(monkeypatch):
    mgr = _manager()
    mgr.structure_diverged = True
    mgr.request_state(reason="recovery")
    assert mgr.status == "JOINING"

    future = time_module.time() + 10
    monkeypatch.setattr(time_module, "time", lambda: future)

    events = mgr.tick()

    assert mgr.status == STATE_SYNCED
    assert mgr.master_guid == "master-guid"
    assert any(a == "structure_recovery_failed" for a, _ in events)
    assert mgr.structure_diverged is True


def test_an_unanswered_join_request_still_falls_back_to_discovering(monkeypatch):
    """The pre-existing join-timeout behaviour must be unchanged."""
    mgr = _manager()
    mgr.request_state(reason="join")
    assert mgr.status == "JOINING"

    future = time_module.time() + 10
    monkeypatch.setattr(time_module, "time", lambda: future)

    events = mgr.tick()

    assert mgr.status == STATE_DISCOVERING
    assert mgr.master_guid is None
    assert any(a == "state_request_timeout" for a, _ in events)


def test_a_master_serves_a_mid_session_request_identically_to_a_join():
    mgr = _manager()
    mgr.is_master = True
    envelope = {
        "session": "s",
        "source_guid": "already-joined-peer",
        "payload": {
            "command_schema": pm.StateRequest.SCHEMA,
            "command": {
                "event": pm.StateRequest.EVENT,
                "payload": pm.StateRequest(
                    target_guid=mgr.self_guid, requester_guid="already-joined-peer",
                ).to_payload(),
            },
        },
    }

    result = mgr.apply_patch(envelope)

    assert result == ("state_request_received", "already-joined-peer")


# ---------------------------------------------------------------------------
# 4. Coalescing and pacing recovery from tick()
# ---------------------------------------------------------------------------

def test_several_refusals_in_the_settle_window_trigger_one_request(monkeypatch):
    mgr = _manager(role=authority.VIEWER)
    _, clip_guid = _timeline(mgr)

    t = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: t[0])

    mgr.broadcast_remove_child("track-1", clip_guid)
    t[0] += DIVERGENCE_SETTLE * 0.5
    mgr.broadcast_remove_child("track-1", clip_guid)
    assert mgr.structure_diverged is True
    assert len(_events(mgr, pm.StateRequest.EVENT)) == 0

    t[0] += DIVERGENCE_SETTLE + 0.01
    mgr.tick()

    assert len(_events(mgr, pm.StateRequest.EVENT)) == 1


def test_divergence_during_a_rebuild_is_not_lost(monkeypatch):
    mgr = _manager(role=authority.VIEWER)
    _, clip_guid = _timeline(mgr)

    t = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: t[0])
    mgr.broadcast_remove_child("track-1", clip_guid)
    t[0] += DIVERGENCE_SETTLE + 0.01
    mgr.tick()
    assert mgr.status == "JOINING"

    # A fresh divergence lands (white-box: the manager-level bookkeeping,
    # regardless of exactly which host-side path could reach it) while the
    # recovery snapshot is still in flight — strictly after the request went
    # out, which is the comparison apply_snapshot makes.
    t[0] += 0.01
    mgr._note_structure_diverged("test", "reentrant")

    snapshot = pm.StateSnapshot(
        target_guid=mgr.self_guid,
        timelines=dict(mgr._timelines),
        active_timeline_guid="tl-1",
        snapshot_timestamp=time_module.time(),
    ).to_payload()
    mgr.apply_snapshot(snapshot)

    assert mgr.status == STATE_SYNCED
    assert mgr.structure_diverged is True, "a later divergence must survive the rebuild"

    t[0] += RECOVERY_COOLDOWN + DIVERGENCE_SETTLE + 0.01
    mgr.tick()
    assert len(_events(mgr, pm.StateRequest.EVENT)) == 2, "must rebuild again, not settle stale"


def test_sustained_contention_is_paced_by_the_cooldown(monkeypatch):
    mgr = _manager(role=authority.DRIVER)
    _, clip_guid = _timeline(mgr)
    mgr._leases[authority.CHANNEL_STRUCTURE].owner_guid = "someone-else"
    mgr._leases[authority.CHANNEL_STRUCTURE].deadline = 10 ** 9

    import os as _os
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "1")

    t = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: t[0])

    for _ in range(3):
        mgr.broadcast_remove_child("track-1", clip_guid)
        t[0] += DIVERGENCE_SETTLE + 0.01
        mgr.tick()
        # Recovery request answers with nothing (still leased elsewhere); stays
        # diverged and re-marks on the next refusal.
        mgr.status = STATE_SYNCED
        mgr.structure_diverged = True

    # Fewer requests than refusals: the cooldown, not the settle window, paced them.
    assert len(_events(mgr, pm.StateRequest.EVENT)) < 3


def test_no_master_stays_diverged_and_recovers_once_one_appears(monkeypatch):
    mgr = _manager(role=authority.VIEWER, master_guid=None)
    _, clip_guid = _timeline(mgr)

    t = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: t[0])
    mgr.broadcast_remove_child("track-1", clip_guid)
    t[0] += DIVERGENCE_SETTLE + 0.01

    events = mgr.tick()

    assert any(a == "structure_recovery_failed" for a, _ in events)
    assert mgr.structure_diverged is True
    assert len(_events(mgr, pm.StateRequest.EVENT)) == 0
    assert mgr.status == STATE_SYNCED, "must not discard local content or leave the session"

    mgr.master_guid = "master-guid"
    t[0] += RECOVERY_COOLDOWN + 0.01
    mgr.tick()

    assert len(_events(mgr, pm.StateRequest.EVENT)) == 1


def test_a_diverged_master_reports_unrecoverable_without_sending(monkeypatch):
    mgr = _manager(role=authority.DRIVER)
    mgr.is_master = True
    mgr.master_guid = mgr.self_guid
    _, clip_guid = _timeline(mgr)
    mgr._leases[authority.CHANNEL_STRUCTURE].owner_guid = "someone-else"
    mgr._leases[authority.CHANNEL_STRUCTURE].deadline = 10 ** 9

    import os as _os
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "1")

    t = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: t[0])
    mgr.broadcast_remove_child("track-1", clip_guid)
    t[0] += DIVERGENCE_SETTLE + 0.01

    events = mgr.tick()

    assert any(a == "structure_recovery_failed" for a, _ in events)
    assert len(_events(mgr, pm.StateRequest.EVENT)) == 0
    assert mgr.structure_diverged is True
