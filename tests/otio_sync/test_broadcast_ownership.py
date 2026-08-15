"""Tests for the broadcast-ownership write lease (position/display/structure).

Mirrors the conventions in ``test_broadcast_authority.py`` and
``test_host_election.py``: assertions on the wire and on local lease state,
a ``FakeNetwork`` capturing sent envelopes, and ``apply_patch`` used to
simulate cross-peer message delivery rather than calling handlers directly.
"""

import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

import opentimelineio as otio  # noqa: E402
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
def _ownership_on(monkeypatch):
    """Ownership enforcement defaults to disabled (migration step 1a); tests
    that exercise the lease turn it on explicitly, mirroring how
    test_broadcast_authority.py turns visibility enforcement on by default."""
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "1")


def _manager(guid, is_host=True):
    mgr = SyncManager(session_id="s", self_guid=guid, network=FakeNetwork(), app_name="openrv")
    mgr.status = STATE_SYNCED
    # Keep visibility enforcement out of these tests' way: host by default.
    mgr.host_guid = guid if is_host else "someone-else"
    mgr.is_host = is_host
    return mgr


def _deliver(source, *targets):
    for envelope in list(source.network.sent):
        for target in targets:
            target.apply_patch(envelope)


def _claim_envelope(peer_guid, category, claim_ts):
    return {
        "session": "s",
        "source_guid": peer_guid,
        "payload": {
            "command_schema": pm.ClaimOwnership.SCHEMA,
            "command": {
                "event": pm.ClaimOwnership.EVENT,
                "payload": {"category": category, "peer_guid": peer_guid, "claim_ts": claim_ts},
            },
        },
    }


def _view_state():
    return {
        "playing": True,
        "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 61.0, "rate": 24.0},
        "playback_mode": "loop",
    }


def _playback_payloads(mgr):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command_schema"] == pm.PlaybackSettingsSet.SCHEMA
    ]


# ---------------------------------------------------------------------------
# Channel table and pure functions
# ---------------------------------------------------------------------------


def test_lease_channels_are_position_display_structure():
    assert set(authority.LEASE_CHANNELS) == {"position", "display", "structure", "visibility"}


def test_strip_position_fields_is_a_pure_function():
    state = {"playing": True, "current_time": 1, "playback_mode": "loop", "extra": 1}

    stripped = authority.strip_position_fields(state)

    assert stripped == {"extra": 1}
    assert "playing" in state, "must not mutate the caller's dict"


def test_resolve_claim_picks_the_earlier_timestamp():
    winner = authority.resolve_claim((5.0, "b"), (2.0, "a"))
    assert winner == (2.0, "a")


def test_resolve_claim_breaks_exact_ties_on_guid():
    winner = authority.resolve_claim((5.0, "zzz"), (5.0, "aaa"))
    assert winner == (5.0, "aaa")


def test_resolve_claim_with_no_current_returns_incoming():
    assert authority.resolve_claim(None, (5.0, "a")) == (5.0, "a")


# ---------------------------------------------------------------------------
# Reading the asserted position
# ---------------------------------------------------------------------------


def test_position_frame_reads_an_asserted_frame():
    state = {"playing": False, "current_time": {"value": 61.0}, "playback_mode": "loop"}
    assert authority.position_frame(state) == 61.0


def test_position_frame_is_none_when_the_group_is_absent():
    assert authority.position_frame({"view_mode": "sequence", "clip_guid": "c"}) is None


@pytest.mark.parametrize(
    "current_time",
    [
        {},                                   # present but empty
        {"OTIO_SCHEMA": "RationalTime.1"},    # schema only, no value
        {"value": None},                      # explicit null
        {"value": "notanumber"},
        {"value": True},                      # bool is an int in Python; not a frame
        None,
        "61.0",                               # not a mapping
    ],
    ids=["empty", "schema-only", "null", "nan", "bool", "none", "str"],
)
def test_position_frame_is_none_for_a_malformed_current_time(current_time):
    """The hole the group-absence check cannot see.

    ``current_time`` is passed through unvalidated by
    ``PlaybackSettingsSet.from_payload``, so a message can carry the key while
    saying nothing usable. Read with a ``0`` default that became "seek to the
    start"; every one of these must now assert nothing at all.
    """
    state = {"playing": False, "current_time": current_time}
    assert authority.position_frame(state) is None


def test_position_frame_does_not_use_a_negative_sentinel():
    """A negative value is data, not a signal.

    Both hosts do arithmetic on this: OpenRV adds the view's frame base and
    xStudio clamps with ``max(0, ...)``. A ``-1`` meaning "no position" would
    therefore land as ``base - 1`` on one and as frame 0 — the very bug — on the
    other. Absence is the only thing that survives both.
    """
    state = {"playing": False, "current_time": {"value": -1.0}}
    assert authority.position_frame(state) == -1.0


def test_resolve_visibility_later_wins():
    current = (0.0, "a")
    incoming = (1.5, "b")
    winner = authority._resolve_visibility(current, incoming)
    assert winner == incoming
    assert authority._resolve_visibility(incoming, current) == incoming


def test_resolve_visibility_later_wins_by_any_margin():
    """No tolerance band in the ordering — damping is the claim site's job.

    A "within N seconds" comparison here would not be transitive, and
    ``_apply_claim`` folds a sequence of claims (see
    ``test_resolve_visibility_is_order_independent``).
    """
    current = (0.0, "a")
    incoming = (0.001, "b")
    assert authority._resolve_visibility(current, incoming) == incoming
    assert authority._resolve_visibility(incoming, current) == incoming


@pytest.mark.parametrize("channel", list(authority.CLAIM_RESOLVERS))
def test_resolve_claim_is_order_independent(channel):
    """Every resolver must be a total order, not just commutative.

    Each peer folds the same claims in a different order: ``claim_category``
    applies this peer's own claim before broadcasting it, while every other peer
    sees it arrive among the rest.  A fold that depends on order puts two peers
    on different owners, and both then believe they may broadcast the category.

    This is the regression test for a pairwise "within VISIBILITY_HOLDOFF"
    visibility rule, under which 0.0/1.0/2.0s resolved to a different winner
    under each rotation.
    """
    import itertools

    claims = [(0.0, "aaa"), (1.0, "bbb"), (2.0, "ccc")]
    winners = set()
    for order in itertools.permutations(claims):
        best = None
        for claim in order:
            best = authority.resolve_claim(best, claim, channel)
        winners.add(best)

    assert len(winners) == 1, f"{channel} resolution depends on arrival order: {winners}"


def test_resolve_visibility_exact_timestamp_tie_breaks_to_lower_guid():
    claim1 = (1.0, "b")
    claim2 = (1.0, "a")
    assert authority._resolve_visibility(claim1, claim2) == claim2
    assert authority._resolve_visibility(claim2, claim1) == claim2


def test_resolve_visibility_none_current_returns_incoming():
    assert authority._resolve_visibility(None, (5.0, "a")) == (5.0, "a")


def test_resolve_claim_delegates_by_category():
    assert authority.resolve_claim((5.0, "b"), (2.0, "a"), authority.CHANNEL_POSITION) == (2.0, "a")
    assert authority.resolve_claim((2.0, "a"), (5.0, "b"), authority.CHANNEL_POSITION) == (2.0, "a")

    assert authority.resolve_claim((2.0, "a"), (5.0, "b"), authority.CHANNEL_VISIBILITY) == (5.0, "b")


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


def test_ownership_enabled_by_default(monkeypatch):
    monkeypatch.delenv(authority.OWNERSHIP_ENFORCEMENT_ENV, raising=False)
    assert authority.ownership_enforcement_enabled() is True


def test_ownership_kill_switch_makes_every_peer_own_every_channel(monkeypatch):
    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "0")
    rv = _manager("rv")

    status = rv.broadcast_playback_state(_view_state())

    assert status == authority.SENT
    assert _playback_payloads(rv)[-1]["playing"] is True


def test_ownership_kill_switch_is_read_per_call(monkeypatch):
    rv = _manager("rv")

    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "0")
    assert rv.broadcast_playback_state(_view_state()) == authority.SENT

    monkeypatch.setenv(authority.OWNERSHIP_ENFORCEMENT_ENV, "1")
    assert rv.broadcast_playback_state(_view_state()) == authority.SUPPRESSED


# ---------------------------------------------------------------------------
# A peer without the lease is stripped/suppressed; the owner broadcasts freely
# ---------------------------------------------------------------------------


def test_peer_without_position_lease_has_position_stripped():
    rv = _manager("rv")

    status = rv.broadcast_playback_state(_view_state())

    assert status == authority.SUPPRESSED
    sent = _playback_payloads(rv)[-1]
    assert "playing" not in sent
    assert "current_time" not in sent
    assert "playback_mode" not in sent


def test_peer_holding_position_lease_broadcasts_freely():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)

    status = rv.broadcast_playback_state(_view_state())

    assert status == authority.SENT
    assert _playback_payloads(rv)[-1]["playing"] is True


def test_display_lease_is_independent_of_position_lease():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_DISPLAY)

    # Holds display, not position: display broadcasts, position is suppressed.
    assert rv.broadcast_display_state({"channel": "R"}) == authority.SENT
    assert rv.broadcast_playback_state(_view_state()) == authority.SUPPRESSED


def test_structure_lease_gates_add_timeline():
    import opentimelineio as otio

    rv = _manager("rv")
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": "tl-1"}
    rv.register_timeline(tl)

    assert rv.broadcast_add_timeline("tl-1") == authority.SUPPRESSED

    rv.claim_category(authority.CHANNEL_STRUCTURE)
    assert rv.broadcast_add_timeline("tl-1") == authority.SENT


def test_clip_timeline_announcement_is_not_lease_gated():
    """A clip timeline is a derived announcement, not a structural mutation.

    Its GUID is deterministic, so every peer computes the same one and a
    duplicate is ignored on receipt — two peers announcing it cannot conflict.
    Gating it dropped a follower's clip timelines silently, including from the
    annotation paths that need the peer to hold the Annotations track before an
    INSERT_CHILD can bind to it.
    """
    rv = _manager("rv")
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    track.metadata["sync"] = {"guid": "trk-1"}
    for guid in ("clip-1", "clip-2"):
        clip = otio.schema.Clip(name=guid)
        clip.metadata["sync"] = {"guid": guid}
        track.append(clip)
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": "tl-1"}
    tl.tracks.append(track)
    rv.register_timeline(tl)

    clip_tl = rv.get_or_create_clip_timeline("clip-1")
    assert clip_tl

    # No structure lease held, and a *confirmed* one held elsewhere — the case
    # where claiming would not have helped, because a claim queues behind a
    # confirmed owner rather than granting.
    assert rv.broadcast_clip_timeline(clip_tl) == authority.SENT

    rv.apply_patch(_claim_envelope("someone-else", authority.CHANNEL_STRUCTURE, 0.0))
    rv._leases[authority.CHANNEL_STRUCTURE].confirmed = True
    # A *different* clip, so this tests the lease rather than the announce-once
    # rule covered by the next test.
    clip_tl2 = rv.get_or_create_clip_timeline("clip-2")
    assert rv.broadcast_clip_timeline(clip_tl2) == authority.SENT
    # The sequence timeline is still gated — only the derived one is exempt.
    assert rv.broadcast_add_timeline("tl-1") == authority.SUPPRESSED


def test_clip_timeline_is_announced_once_regardless_of_who_created_it():
    """"Have my peers been told?" — not "did I create it?".

    The callers used to gate on ``clip_guid not in _clip_timelines``, so a clip
    timeline this peer built while *applying a remote message* was never
    announced: it answers no to "did I create it" and yes to "does it exist".
    """
    rv = _manager("rv")
    clip = otio.schema.Clip(name="c")
    clip.metadata["sync"] = {"guid": "clip-1"}
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    track.metadata["sync"] = {"guid": "trk-1"}
    track.append(clip)
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": "tl-1"}
    tl.tracks.append(track)
    rv.register_timeline(tl)

    # Built while applying something else — exactly the case that went silent.
    clip_tl = rv.get_or_create_clip_timeline("clip-1")

    assert rv.broadcast_clip_timeline(clip_tl) == authority.SENT
    # ... and not again, without the caller having to know.
    assert rv.broadcast_clip_timeline(clip_tl) == authority.SUPPRESSED

    sent = [
        e for e in rv.network.sent
        if e["payload"]["command_schema"] == pm.AddTimeline.SCHEMA
    ]
    assert len(sent) == 1, "announced more than once"


# ---------------------------------------------------------------------------
# D2 — convergence on simultaneous claims for a free channel
# ---------------------------------------------------------------------------


def test_two_simultaneous_claims_converge_on_the_same_owner_either_order():
    """Both peers see the *other's* claim after their own — arrival order
    differs, but every peer must resolve the same winner (design.md D2)."""
    a_claim = ("a", 10.0)
    b_claim = ("b", 5.0)  # earlier claim_ts: b should win regardless of order

    # Peer 1 processes its own (a) first, then the remote one (b).
    peer1 = _manager("peer1")
    peer1._apply_claim(authority.CHANNEL_POSITION, a_claim[1], a_claim[0])
    peer1._apply_claim(authority.CHANNEL_POSITION, b_claim[1], b_claim[0])

    # Peer 2 processes the remote one (a) first, then its own (b).
    peer2 = _manager("peer2")
    peer2._apply_claim(authority.CHANNEL_POSITION, a_claim[1], a_claim[0])
    peer2._apply_claim(authority.CHANNEL_POSITION, b_claim[1], b_claim[0])

    assert peer1._leases[authority.CHANNEL_POSITION].owner_guid == "b"
    assert peer2._leases[authority.CHANNEL_POSITION].owner_guid == "b"


def test_claim_resolves_identically_for_claimant_and_receiver():
    """The claiming peer applies the same rule to its own claim it would to
    a received one — so its local view matches everyone else's from the start."""
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)
    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "rv"

    xs = _manager("xs")
    _deliver(rv, xs)
    assert xs._leases[authority.CHANNEL_POSITION].owner_guid == "rv"


# ---------------------------------------------------------------------------
# Owner holds until idle — a confirmed lease is not preempted
# ---------------------------------------------------------------------------


def test_confirmed_owner_is_not_preempted_by_a_later_earlier_timestamped_claim():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)
    rv.broadcast_playback_state(_view_state())  # confirms the lease with real traffic
    assert rv._leases[authority.CHANNEL_POSITION].confirmed is True

    # A straggler claim with an *earlier* claim_ts than rv's own must still
    # not preempt an actively-held, confirmed lease.
    rv._apply_claim(authority.CHANNEL_POSITION, 0.0, "late-claimant")

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "rv"
    assert rv._leases[authority.CHANNEL_POSITION].pending_claimant == (0.0, "late-claimant")


def test_pending_claim_does_not_grant_until_the_owner_goes_idle():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)
    rv.broadcast_playback_state(_view_state())

    rv._apply_claim(authority.CHANNEL_POSITION, 1.0, "waiting-peer")
    # Still owned by rv -- refreshing again (as continued broadcasting would)
    # must not hand it over.
    rv.broadcast_playback_state(_view_state())

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "rv"


# ---------------------------------------------------------------------------
# D3 — expiry and transfer to a pending claimant
# ---------------------------------------------------------------------------


def test_expired_lease_transfers_to_pending_claimant(monkeypatch):
    import time as time_module

    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)
    rv.broadcast_playback_state(_view_state())
    rv._apply_claim(authority.CHANNEL_POSITION, 1.0, "waiting-peer")

    future = time_module.monotonic() + authority.LEASE_DURATIONS[authority.CHANNEL_POSITION] + 1
    monkeypatch.setattr(time_module, "monotonic", lambda: future)

    assert rv._owns_channel(authority.CHANNEL_POSITION) is False
    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "waiting-peer"


def test_expired_lease_with_no_pending_claimant_becomes_free(monkeypatch):
    import time as time_module

    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)
    rv.broadcast_playback_state(_view_state())

    future = time_module.monotonic() + authority.LEASE_DURATIONS[authority.CHANNEL_POSITION] + 1
    monkeypatch.setattr(time_module, "monotonic", lambda: future)

    assert rv._owns_channel(authority.CHANNEL_POSITION) is False
    assert rv._leases[authority.CHANNEL_POSITION].owner_guid is None


def test_active_owner_keeps_the_lease_when_continuously_refreshed(monkeypatch):
    import time as time_module

    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)

    t = [time_module.monotonic()]
    monkeypatch.setattr(time_module, "monotonic", lambda: t[0])

    for _ in range(5):
        t[0] += authority.LEASE_DURATIONS[authority.CHANNEL_POSITION] * 0.5
        rv.broadcast_playback_state(_view_state())

    assert rv._owns_channel(authority.CHANNEL_POSITION) is True


# ---------------------------------------------------------------------------
# Explicit release
# ---------------------------------------------------------------------------


def test_release_frees_the_channel_without_waiting_for_expiry():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)
    rv.release_category(authority.CHANNEL_POSITION)

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid is None


def test_release_promotes_a_pending_claimant_immediately():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)
    rv.broadcast_playback_state(_view_state())
    rv._apply_claim(authority.CHANNEL_POSITION, 1.0, "waiting-peer")

    rv.release_category(authority.CHANNEL_POSITION)

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "waiting-peer"


def test_a_peer_that_does_not_hold_the_lease_cannot_release_it():
    rv = _manager("rv")
    xs = _manager("xs")
    xs.claim_category(authority.CHANNEL_POSITION)
    _deliver(xs, rv)
    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "xs"

    rv.release_category(authority.CHANNEL_POSITION)  # no-op: rv isn't the owner

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "xs"


def test_foreign_release_message_is_ignored():
    """A RELEASE_OWNERSHIP from a peer that isn't the recorded owner (e.g. a
    stale message arriving after the lease already moved on) changes nothing."""
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)  # rv owns it locally

    envelope = {
        "session": "s",
        "source_guid": "not-the-owner",
        "payload": {
            "command_schema": pm.ReleaseOwnership.SCHEMA,
            "command": {
                "event": pm.ReleaseOwnership.EVENT,
                "payload": {"category": authority.CHANNEL_POSITION, "peer_guid": "not-the-owner"},
            },
        },
    }
    rv.apply_patch(envelope)

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "rv"


# ---------------------------------------------------------------------------
# D6 — STATE_SNAPSHOT carries ownership; late joiners adopt it
# ---------------------------------------------------------------------------


def test_snapshot_reports_the_current_owner_and_remaining_time():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)

    section = rv._lease_wire_section()

    assert section["position"]["owner_guid"] == "rv"
    assert section["position"]["remaining_ms"] > 0
    assert "display" not in section
    assert "structure" not in section


def test_export_state_includes_broadcast_ownership():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_STRUCTURE)

    payload = rv.export_state()

    assert payload["broadcast_ownership"]["structure"]["owner_guid"] == "rv"


def test_adopt_ownership_learns_a_remote_owner():
    rv = _manager("rv")

    rv.adopt_ownership({"position": {"owner_guid": "xs", "remaining_ms": 800.0}})

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "xs"
    assert rv.broadcast_playback_state(_view_state()) == authority.SUPPRESSED


def test_adopt_ownership_ignores_absent_payload():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)

    rv.adopt_ownership(None)
    rv.adopt_ownership({})

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "rv"


def test_an_old_peers_snapshot_cannot_clear_a_lease_this_peer_holds():
    """A snapshot from a peer predating this field carries no
    broadcast_ownership section at all -- simulated here by an empty/absent
    payload, per StateSnapshot's own omit-when-unset convention."""
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_POSITION)

    rv.adopt_ownership(None)

    assert rv._leases[authority.CHANNEL_POSITION].owner_guid == "rv"


def test_joiner_does_not_claim_over_a_reported_live_owner():
    xs = _manager("xs")
    xs.claim_category(authority.CHANNEL_POSITION)

    joiner = _manager("joiner")
    joiner.adopt_ownership(xs._lease_wire_section())

    assert joiner.broadcast_playback_state(_view_state()) == authority.SUPPRESSED


def test_state_snapshot_round_trips_broadcast_ownership():
    payload = pm.StateSnapshot(
        target_guid="j",
        broadcast_ownership={"position": {"owner_guid": "g", "remaining_ms": 500.0}},
    ).to_payload()

    restored = pm.StateSnapshot.from_payload(payload)

    assert restored.broadcast_ownership == {"position": {"owner_guid": "g", "remaining_ms": 500.0}}


def test_state_snapshot_omits_ownership_when_every_channel_is_free():
    payload = pm.StateSnapshot(target_guid="j").to_payload()

    assert "broadcast_ownership" not in payload


def test_visibility_lease_lifecycle(monkeypatch):
    import time as time_module

    now_wall = [1000.0]
    now_mono = [100.0]
    monkeypatch.setattr(time_module, "time", lambda: now_wall[0])
    monkeypatch.setattr(time_module, "monotonic", lambda: now_mono[0])

    a = _manager("a")
    b = _manager("b")

    a.claim_category(authority.CHANNEL_VISIBILITY)
    assert a._leases[authority.CHANNEL_VISIBILITY].owner_guid == "a"
    _deliver(a, b)
    assert b._leases[authority.CHANNEL_VISIBILITY].owner_guid == "a"

    now_wall[0] += 1.0
    now_mono[0] += 1.0

    b.claim_category(authority.CHANNEL_VISIBILITY)
    assert b._leases[authority.CHANNEL_VISIBILITY].owner_guid == "a"
    _deliver(b, a)
    assert a._leases[authority.CHANNEL_VISIBILITY].owner_guid == "a"

    now_wall[0] += 1.0
    now_mono[0] += 1.0

    b.claim_category(authority.CHANNEL_VISIBILITY)
    assert b._leases[authority.CHANNEL_VISIBILITY].owner_guid == "b"
    _deliver(b, a)
    assert a._leases[authority.CHANNEL_VISIBILITY].owner_guid == "b"


def test_visibility_lease_expiry(monkeypatch):
    import time as time_module

    now_wall = [1000.0]
    now_mono = [100.0]
    monkeypatch.setattr(time_module, "time", lambda: now_wall[0])
    monkeypatch.setattr(time_module, "monotonic", lambda: now_mono[0])

    a = _manager("a")
    b = _manager("b")

    a.claim_category(authority.CHANNEL_VISIBILITY)
    assert a._leases[authority.CHANNEL_VISIBILITY].owner_guid == "a"
    _deliver(a, b)
    assert b._leases[authority.CHANNEL_VISIBILITY].owner_guid == "a"

    now_wall[0] += 3.1
    now_mono[0] += 3.1

    assert a._owns_channel(authority.CHANNEL_VISIBILITY) is False
    assert a._leases[authority.CHANNEL_VISIBILITY].owner_guid is None
    assert b._leases[authority.CHANNEL_VISIBILITY].owner_guid == "a"
    b._settle_lease_expiry(authority.CHANNEL_VISIBILITY)
    assert b._leases[authority.CHANNEL_VISIBILITY].owner_guid is None

    b.claim_category(authority.CHANNEL_VISIBILITY)
    assert b._leases[authority.CHANNEL_VISIBILITY].owner_guid == "b"


def test_snapshot_reports_visibility_owner_and_remaining_time():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_VISIBILITY)

    section = rv._lease_wire_section()

    assert section["visibility"]["owner_guid"] == "rv"
    assert section["visibility"]["remaining_ms"] > 0


def test_adopt_ownership_learns_remote_visibility_owner():
    rv = _manager("rv")

    rv.adopt_ownership({"visibility": {"owner_guid": "xs", "remaining_ms": 1500.0}})

    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid == "xs"


def test_old_peers_snapshot_omitting_visibility_does_not_clear_held_visibility_lease():
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_VISIBILITY)

    rv.adopt_ownership({"position": {"owner_guid": "xs", "remaining_ms": 500.0}})

    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid == "rv"


def test_foreign_claim_ownership_for_visibility_is_applied():
    rv = _manager("rv")

    envelope = _claim_envelope("xs", authority.CHANNEL_VISIBILITY, 100.0)
    rv.apply_patch(envelope)

    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid == "xs"


# ---------------------------------------------------------------------------
# Claim-site hold-off (the damping half of the visibility rule)
# ---------------------------------------------------------------------------


def test_holdoff_withholds_a_challenge_to_an_active_visibility_holder():
    """Inside the hold-off the claim is not made at all — not made and lost."""
    rv = _manager("rv")
    rv.apply_patch(_claim_envelope("xs", authority.CHANNEL_VISIBILITY, 100.0))
    sent_before = len(rv.network.sent)

    # 0.5s after xs claimed: inside VISIBILITY_HOLDOFF.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("time.time", lambda: 100.5)
        rv.claim_category(authority.CHANNEL_VISIBILITY)

    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid == "xs"
    assert len(rv.network.sent) == sent_before, "withheld claim must not go on the wire"


def test_holdoff_allows_a_challenge_once_the_holder_goes_idle():
    rv = _manager("rv")
    rv.apply_patch(_claim_envelope("xs", authority.CHANNEL_VISIBILITY, 100.0))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("time.time", lambda: 100.0 + authority.VISIBILITY_HOLDOFF + 0.01)
        rv.claim_category(authority.CHANNEL_VISIBILITY)

    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid == "rv"


def test_holdoff_never_withholds_the_incumbents_own_refresh():
    """Re-claiming is how an active holder stays protected."""
    rv = _manager("rv")
    rv.claim_category(authority.CHANNEL_VISIBILITY)
    first_ts = rv._leases[authority.CHANNEL_VISIBILITY].claim_ts

    rv.claim_category(authority.CHANNEL_VISIBILITY)

    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid == "rv"
    assert rv._leases[authority.CHANNEL_VISIBILITY].claim_ts >= first_ts


def test_holdoff_does_not_apply_to_the_earlier_wins_channels():
    """Only visibility damps its claims; position settles a burst by ordering."""
    for channel in (authority.CHANNEL_POSITION, authority.CHANNEL_DISPLAY,
                    authority.CHANNEL_STRUCTURE):
        assert channel not in authority.CLAIM_HOLDOFFS
        assert authority.claim_withheld_by_holdoff(channel, "xs", 100.0, "rv", 100.1) is False


# ---------------------------------------------------------------------------
# Departure
# ---------------------------------------------------------------------------


def test_dropping_a_peer_frees_the_lease_it_held():
    """A departed holder must not keep the view frozen until its lease expires."""
    rv = _manager("rv")
    rv._peers["xs"] = {"app": "xstudio", "capabilities": ["visibility"]}
    rv.apply_patch(_claim_envelope("xs", authority.CHANNEL_VISIBILITY, 100.0))
    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid == "xs"

    rv.drop_peer("xs")

    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid is None


def test_dropping_a_peer_frees_every_channel_it_held():
    rv = _manager("rv")
    rv._peers["xs"] = {"app": "xstudio", "capabilities": ["visibility"]}
    for channel in authority.LEASE_CHANNELS:
        rv.apply_patch(_claim_envelope("xs", channel, 100.0))

    rv.drop_peer("xs")

    for channel in authority.LEASE_CHANNELS:
        assert rv._leases[channel].owner_guid is None, channel


def test_dropping_a_peer_leaves_another_peers_lease_alone():
    rv = _manager("rv")
    rv._peers["xs"] = {"app": "xstudio", "capabilities": ["visibility"]}
    rv._peers["gone"] = {"app": "openrv", "capabilities": ["visibility"]}
    rv.apply_patch(_claim_envelope("xs", authority.CHANNEL_VISIBILITY, 100.0))

    rv.drop_peer("gone")

    assert rv._leases[authority.CHANNEL_VISIBILITY].owner_guid == "xs"
