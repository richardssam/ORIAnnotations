"""Tests for role enforcement at the broadcast guard and the claim gate.

Assertions are made on the **sent envelope** rather than on whether a send
happened, following ``test_broadcast_authority.py``: ``SUPPRESSED`` has always
meant "sent, with fields stripped", and a message that keeps its position fields
while losing its visibility fields is the normal case, not an edge case.

The claim gate is tested beside the guard deliberately.  On its own each is
harmless; composed without the gate, a viewer's local scrub takes the position
lease from the driver and can never confirm it, which is a lease duration of
dead position sync per viewer interaction.
"""

import os
import sys

import opentimelineio as otio
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import authority  # noqa: E402
from otio_sync_core.manager import SyncManager, STATE_SYNCED  # noqa: E402
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
    monkeypatch.delenv(authority.ENFORCEMENT_ENV, raising=False)
    monkeypatch.delenv(authority.ROLE_ENFORCEMENT_ENV, raising=False)
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "1")


def _manager(role=authority.DRIVER, guid="self-guid", is_host=True):
    """A synced peer holding *role*, host by default so visibility authority
    stays out of the way of the role assertions."""
    mgr = SyncManager(
        session_id="s",
        self_guid=guid,
        network=FakeNetwork(),
        app_name="openrv",
        identity_override={"user": "alice"},
        default_role=role,
    )
    mgr.status = STATE_SYNCED
    mgr.host_guid = guid if is_host else "someone-else"
    mgr.is_host = is_host
    return mgr


def _payloads(mgr, schema):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command_schema"] == schema
    ]


def _events(mgr, event):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command"]["event"] == event
    ]


def _view_state():
    return {
        "playing": True,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 61.0, "rate": 24.0},
        "playback_mode": "loop",
        "view_mode": "source",
        "clip_guid": "clip-abc",
    }


def _timeline(mgr, guid="tl-1"):
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": guid}
    tl.tracks.append(otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video))
    mgr.register_timeline(tl)
    return guid


# ---------------------------------------------------------------------------
# The playback guard — field groups, one message
# ---------------------------------------------------------------------------

def test_a_driver_broadcast_is_untouched():
    mgr = _manager(authority.DRIVER)
    mgr.claim_category(authority.CHANNEL_POSITION)

    status = mgr.broadcast_playback_state(_view_state())

    sent = _payloads(mgr, pm.PlaybackSettingsSet.SCHEMA)[-1]
    assert status == authority.SENT
    assert authority.asserts_visibility(sent) is True
    assert authority.asserts_position(sent) is True


def test_a_reviewer_keeps_position_and_loses_visibility_in_one_message():
    mgr = _manager(authority.REVIEWER)
    mgr.claim_category(authority.CHANNEL_POSITION)

    status = mgr.broadcast_playback_state(_view_state())

    sent = _payloads(mgr, pm.PlaybackSettingsSet.SCHEMA)[-1]
    assert status == authority.SUPPRESSED
    assert authority.asserts_position(sent) is True
    assert authority.asserts_visibility(sent) is False


def test_a_viewer_emits_no_playback_message_at_all():
    """An emptied playback message is not silence.

    It still carries ``timeline_guid``, which passive peers follow, and both
    hosts read an absent ``current_time`` as ``.get("value", 0)`` — an assertion
    of frame 0. Emitting it let a viewer drive the session to the start of the
    view (observed 2026-08-12), which is the opposite of what its role says.
    """
    mgr = _manager(authority.VIEWER)

    status = mgr.broadcast_playback_state(_view_state())

    assert status == authority.SUPPRESSED
    assert _payloads(mgr, pm.PlaybackSettingsSet.SCHEMA) == []


def test_a_viewer_still_sends_nothing_when_only_position_was_offered():
    mgr = _manager(authority.VIEWER)

    mgr.broadcast_playback_state({
        "playing": True,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 12.0, "rate": 24.0},
    })

    assert _payloads(mgr, pm.PlaybackSettingsSet.SCHEMA) == []


def test_a_lease_emptied_message_still_goes_out():
    """Narrower than "any emptied message": suppressing a lease-stripped one is
    ``broadcast-ownership``'s specified behaviour and not this change's to alter."""
    mgr = _manager(authority.DRIVER)

    status = mgr.broadcast_playback_state({
        "playing": True,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 12.0, "rate": 24.0},
    })

    assert status == authority.SUPPRESSED
    assert len(_payloads(mgr, pm.PlaybackSettingsSet.SCHEMA)) == 1


def test_display_state_survives_for_every_role():
    """Per-peer presentation, not a session event: no role strips it."""
    for role in authority.ROLES:
        mgr = _manager(role)
        mgr.claim_category(authority.CHANNEL_DISPLAY)

        status = mgr.broadcast_display_state({"exposure": 1.5, "channel": "R"})

        assert status == authority.SENT, role
        assert _payloads(mgr, pm.DisplaySettingsSet.SCHEMA)[-1]["exposure"] == 1.5


# ---------------------------------------------------------------------------
# Structure and annotation
# ---------------------------------------------------------------------------

def test_a_reviewer_may_not_reshape_the_timeline():
    mgr = _manager(authority.REVIEWER)
    tl_guid = _timeline(mgr)
    mgr.claim_category(authority.CHANNEL_STRUCTURE)

    assert mgr.broadcast_add_timeline(tl_guid) == authority.SUPPRESSED
    assert mgr.broadcast_timeline_rename(tl_guid, "renamed") == authority.SUPPRESSED
    assert mgr.broadcast_replace_timeline(tl_guid) == authority.SUPPRESSED
    assert _events(mgr, pm.AddTimeline.EVENT) == []
    assert _events(mgr, pm.RenameTimeline.EVENT) == []


def test_a_reviewer_may_still_annotate():
    mgr = _manager(authority.REVIEWER)

    status = mgr.broadcast_partial_annotation("clip-1", 3.0, 24.0, [])

    assert status == authority.SENT


def test_a_viewer_annotates_nothing():
    mgr = _manager(authority.VIEWER)

    assert mgr.broadcast_partial_annotation("clip-1", 3.0, 24.0, []) == authority.SUPPRESSED
    assert mgr.broadcast_add_annotation("track-1", "clip-1",
                                        otio.opentime.RationalTime(0, 24), []) is None


def test_a_clip_timeline_announcement_is_exempt_like_the_lease():
    """Not a mutation: a reviewer that could not announce a clip timeline would
    have its own annotations fail to bind on every peer."""
    mgr = _manager(authority.REVIEWER)
    tl = otio.schema.Timeline(name="clip-tl")
    tl.metadata["sync"] = {"guid": "clip-tl-1"}
    tl.metadata["clip_timeline_for"] = "seq-clip-1"
    mgr.register_timeline(tl)

    assert mgr.broadcast_clip_timeline("clip-tl-1") == authority.SENT


def test_a_destructive_annotation_op_is_driver_only():
    """The one row where role is finer-grained than the category — and the only
    row that removes a permission a reviewer has today."""
    reviewer = _manager(authority.REVIEWER)
    tl = otio.schema.Timeline(name="seq")
    track = otio.schema.Track(name="Annotations", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(track)
    clip = otio.schema.Clip(name="ann", source_range=otio.opentime.TimeRange(
        otio.opentime.RationalTime(0, 24), otio.opentime.RationalTime(1, 24)))
    track.append(clip)
    reviewer.register_timeline(tl)
    clip_guid = clip.metadata["sync"]["guid"]

    assert reviewer.broadcast_replace_annotation_commands(clip_guid, []) == authority.SENT
    assert reviewer.broadcast_replace_annotation_commands(
        clip_guid, [], destructive=True
    ) == authority.SUPPRESSED


def test_a_viewer_emits_no_property_set():
    mgr = _manager(authority.VIEWER)
    tl_guid = _timeline(mgr)

    mgr.set_property(tl_guid, "name", "renamed-locally")

    assert _events(mgr, pm.SetProperty.EVENT) == []


def test_a_viewer_emits_no_structural_child_insert():
    mgr = _manager(authority.VIEWER)
    tl = otio.schema.Timeline(name="seq")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(track)
    mgr.register_timeline(tl)
    track_guid = track.metadata["sync"]["guid"]

    mgr.insert_child(track_guid, otio.schema.Clip(name="new"))

    assert _events(mgr, pm.InsertChild.EVENT) == []


# ---------------------------------------------------------------------------
# The claim gate (D8) — role gates claiming, not only broadcasting
# ---------------------------------------------------------------------------

def test_a_viewer_scrubbing_emits_no_claim():
    mgr = _manager(authority.VIEWER)

    mgr.claim_category(authority.CHANNEL_POSITION)

    assert _events(mgr, pm.ClaimOwnership.EVENT) == []
    assert mgr._leases[authority.CHANNEL_POSITION].owner_guid is None


def test_a_refused_claim_does_not_release_the_current_owner():
    """Not claiming is sufficient; releasing would be the same defect, sign
    reversed — a viewer's local activity taking a lease *away* from a driver."""
    mgr = _manager(authority.VIEWER)
    mgr._apply_claim(authority.CHANNEL_POSITION, 1.0, "driver-guid")

    mgr.claim_category(authority.CHANNEL_POSITION)

    assert mgr._leases[authority.CHANNEL_POSITION].owner_guid == "driver-guid"
    assert _events(mgr, pm.ReleaseOwnership.EVENT) == []


def test_every_role_may_claim_the_display_channel():
    for role in authority.ROLES:
        mgr = _manager(role)

        mgr.claim_category(authority.CHANNEL_DISPLAY)

        assert mgr._leases[authority.CHANNEL_DISPLAY].owner_guid == mgr.self_guid, role


def test_a_reviewer_may_claim_position_but_not_structure():
    mgr = _manager(authority.REVIEWER)

    mgr.claim_category(authority.CHANNEL_POSITION)
    mgr.claim_category(authority.CHANNEL_STRUCTURE)

    assert mgr._leases[authority.CHANNEL_POSITION].owner_guid == mgr.self_guid
    assert mgr._leases[authority.CHANNEL_STRUCTURE].owner_guid is None


def test_a_reviewer_may_not_claim_visibility():
    mgr = _manager(authority.REVIEWER)

    mgr.claim_category(authority.CHANNEL_VISIBILITY)

    assert mgr._leases[authority.CHANNEL_VISIBILITY].owner_guid is None


def test_a_refused_visibility_claim_does_not_release_the_current_owner():
    mgr = _manager(authority.REVIEWER)
    mgr._apply_claim(authority.CHANNEL_VISIBILITY, 1.0, "driver-guid")

    mgr.claim_category(authority.CHANNEL_VISIBILITY)

    assert mgr._leases[authority.CHANNEL_VISIBILITY].owner_guid == "driver-guid"
    assert _events(mgr, pm.ClaimOwnership.EVENT) == []


def test_claim_visibility_noops_under_ownership_kill_switch(monkeypatch):
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "0")
    mgr = _manager(authority.DRIVER)

    mgr.claim_category(authority.CHANNEL_VISIBILITY)

    assert mgr._leases[authority.CHANNEL_VISIBILITY].owner_guid is None
    assert _events(mgr, pm.ClaimOwnership.EVENT) == []


def test_a_role_stripped_broadcast_never_confirms_a_lease():
    """The specific reason role is evaluated before category authority.

    A confirmed lease is the one state a competing claim will not preempt, and
    ``resolve_claim`` prefers the *earlier* claim — so a viewer that could
    confirm would outrank the driver's fresh claim until expiry.
    """
    mgr = _manager(authority.VIEWER)
    # Force the lease into this peer's hands without going through the gate,
    # which is the state a pre-D8 build would reach by claiming on a scrub.
    mgr._apply_claim(authority.CHANNEL_POSITION, 1.0, mgr.self_guid)
    assert mgr._leases[authority.CHANNEL_POSITION].confirmed is False

    mgr.broadcast_playback_state(_view_state())

    assert mgr._leases[authority.CHANNEL_POSITION].confirmed is False


def test_a_reviewer_broadcast_still_confirms_the_position_lease():
    """Role first does not mean category never: what role permits, the lease
    still governs, and a permitted broadcast confirms it as before."""
    mgr = _manager(authority.REVIEWER)
    mgr.claim_category(authority.CHANNEL_POSITION)

    mgr.broadcast_playback_state(_view_state())

    assert mgr._leases[authority.CHANNEL_POSITION].confirmed is True


# ---------------------------------------------------------------------------
# Inertness — the property that makes this change safe to ship
# ---------------------------------------------------------------------------

def test_everything_is_inert_under_the_permissive_default():
    """``default_role: driver`` reproduces pre-roles behaviour exactly, and is
    also the rollback."""
    mgr = _manager(authority.DRIVER)
    tl_guid = _timeline(mgr)
    mgr.claim_category(authority.CHANNEL_POSITION)
    mgr.claim_category(authority.CHANNEL_STRUCTURE)

    assert mgr.broadcast_playback_state(_view_state()) == authority.SENT
    assert mgr.broadcast_add_timeline(tl_guid) == authority.SENT
    assert mgr.broadcast_partial_annotation("clip-1", 3.0, 24.0, []) == authority.SENT
    assert len(_events(mgr, pm.ClaimOwnership.EVENT)) == 2


def test_the_kill_switch_reverts_enforcement_completely(monkeypatch):
    monkeypatch.setenv(authority.ROLE_ENFORCEMENT_ENV, "0")
    mgr = _manager(authority.VIEWER)

    mgr.claim_category(authority.CHANNEL_POSITION)
    status = mgr.broadcast_playback_state(_view_state())

    sent = _payloads(mgr, pm.PlaybackSettingsSet.SCHEMA)[-1]
    assert status == authority.SENT
    assert authority.asserts_position(sent) is True
    assert len(_events(mgr, pm.ClaimOwnership.EVENT)) == 1
