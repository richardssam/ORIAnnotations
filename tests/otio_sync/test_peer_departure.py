"""Tests for peer departure — leaving the session, cleanly or otherwise.

The peer table feeds host election, so a peer that never leaves it keeps any
role elected from it.  That is not hypothetical: because only the host may
broadcast visibility, a departed host that stays elected leaves the session's
view frozen with no peer permitted to change it.

Two paths remove a peer, and these tests cover both plus the cases where they
must *not* fire: `PEER_DEPART` for a clean disconnect, and liveness aging for a
crash, where no message will ever arrive.
"""

import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import (  # noqa: E402
    SyncManager,
    PEER_HEARTBEAT_INTERVAL,
    PEER_LIVENESS_TIMEOUT,
)
from otio_sync_core import protocol_messages as pm  # noqa: E402


class FakeNetwork:
    """Captures sent envelopes (SyncNetworkProtocol)."""

    def __init__(self):
        self.sent = []
        self.stopped = False

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payloads(self):
        return []

    def stop(self):
        self.stopped = True


def _manager(guid, app, capabilities=None):
    return SyncManager(
        session_id="s",
        self_guid=guid,
        network=FakeNetwork(),
        app_name=app,
        capabilities=capabilities,
    )


def _deliver(source, *targets):
    """Hand every envelope *source* has sent to each of *targets*."""
    for envelope in list(source.network.sent):
        for target in targets:
            target.apply_patch(envelope)


def _events(net, event):
    return [
        e for e in net.sent
        if e["payload"]["command"]["event"] == event
    ]


def _joined_pair():
    """Two synced peers that know each other. xStudio outranks OpenRV for host."""
    xs = _manager("guid-xs", "xstudio")
    rv = _manager("guid-rv", "openrv")
    xs.start_session()
    rv.start_session()
    _deliver(xs, rv)
    _deliver(rv, xs)
    return xs, rv


# ---------------------------------------------------------------------------
# PEER_DEPART — the clean-disconnect path
# ---------------------------------------------------------------------------


def test_departure_removes_the_peer():
    xs, rv = _joined_pair()
    assert "guid-xs" in rv._peers

    xs.network.sent.clear()
    xs._send_message(pm.PeerDepart(peer_guid="guid-xs"))
    _deliver(xs, rv)

    assert "guid-xs" not in rv._peers


def test_a_departing_host_hands_over_visibility_authority():
    """The bug this change exists to fix.

    Only the host may broadcast visibility, so a host that departs while still
    counted as elected freezes the session's view for everyone left.
    """
    xs, rv = _joined_pair()
    assert rv.host_guid == "guid-xs"
    assert rv.is_host is False

    xs.network.sent.clear()
    xs._send_message(pm.PeerDepart(peer_guid="guid-xs"))
    _deliver(xs, rv)

    assert rv.host_guid == "guid-rv"
    assert rv.is_host is True


def test_a_departing_follower_does_not_change_the_host():
    xs, rv = _joined_pair()
    assert xs.is_host is True

    rv.network.sent.clear()
    rv._send_message(pm.PeerDepart(peer_guid="guid-rv"))
    _deliver(rv, xs)

    assert "guid-rv" not in xs._peers
    assert xs.host_guid == "guid-xs"
    assert xs.is_host is True


def test_close_announces_departure_once():
    xs, _ = _joined_pair()
    xs.network.sent.clear()

    xs.close()

    departures = _events(xs.network, pm.PeerDepart.EVENT)
    assert len(departures) == 1
    payload = departures[0]["payload"]["command"]["payload"]
    assert payload["peer_guid"] == "guid-xs"
    assert xs.network.stopped is True


def test_close_stops_the_network_even_if_the_notice_fails():
    """A courtesy message must never block teardown."""
    xs, _ = _joined_pair()

    def boom(_payload):
        raise RuntimeError("broker gone")

    xs.network.send_payload = boom

    xs.close()

    assert xs.network.stopped is True


# ---------------------------------------------------------------------------
# Liveness aging — the crash path, where no message ever arrives
# ---------------------------------------------------------------------------


def test_a_silent_peer_is_aged_out():
    xs, rv = _joined_pair()
    assert "guid-xs" in rv._peers

    rv._peers["guid-xs"]["last_seen"] = time.time() - PEER_LIVENESS_TIMEOUT - 1
    rv.tick()

    assert "guid-xs" not in rv._peers
    assert rv.is_host is True  # authority moved, same as an announced departure


def test_an_announcing_peer_is_never_aged_out():
    """The trap this design exists to avoid.

    Liveness cannot be inferred from a peer's *other* traffic: a viewer watching
    a screening emits nothing for the whole session and would be dropped while
    present.  Only announcements count, and every peer announces on a cadence.
    """
    xs, rv = _joined_pair()
    rv._peers["guid-xs"]["last_seen"] = time.time() - PEER_LIVENESS_TIMEOUT - 1

    # xStudio is idle — it sends no playback, annotation or structural traffic —
    # but it does heartbeat.
    xs.network.sent.clear()
    xs._last_announce_time = 0.0
    xs.tick()
    _deliver(xs, rv)

    rv.tick()

    assert "guid-xs" in rv._peers
    assert rv.host_guid == "guid-xs"


def test_this_peer_is_never_aged_out_of_its_own_table():
    rv = _manager("guid-rv", "openrv")
    rv.start_session()
    rv._peers["guid-rv"]["last_seen"] = time.time() - PEER_LIVENESS_TIMEOUT - 1

    rv.tick()

    assert "guid-rv" in rv._peers
    assert rv.is_host is True


def test_a_wrongly_aged_peer_restores_itself():
    """Self-healing: a stalled peer that is dropped comes back on its next
    heartbeat rather than needing a rejoin."""
    xs, rv = _joined_pair()
    rv._peers["guid-xs"]["last_seen"] = time.time() - PEER_LIVENESS_TIMEOUT - 1
    rv.tick()
    assert "guid-xs" not in rv._peers
    assert rv.is_host is True

    xs.network.sent.clear()
    xs._last_announce_time = 0.0
    xs.tick()
    _deliver(xs, rv)

    assert "guid-xs" in rv._peers
    assert rv.host_guid == "guid-xs"  # election re-ran against the restored table
    assert rv.is_host is False


def test_heartbeat_is_rate_limited():
    rv = _manager("guid-rv", "openrv")
    rv.start_session()
    rv.network.sent.clear()

    for _ in range(5):
        rv.tick()

    assert _events(rv.network, pm.PeerAnnounce.EVENT) == []

    rv._last_announce_time = time.time() - PEER_HEARTBEAT_INTERVAL - 0.1
    rv.tick()

    assert len(_events(rv.network, pm.PeerAnnounce.EVENT)) == 1


def test_a_repeat_announcement_refreshes_liveness():
    """The failure mode that would age every peer out while they announce.

    The handler skips the table write when nothing about the peer's identity
    changed, to keep the log quiet. If the liveness stamp rode along inside that
    comparison, a heartbeat carrying identical identity would never refresh it.
    """
    xs, rv = _joined_pair()
    rv._peers["guid-xs"]["last_seen"] = time.time() - 100

    xs.network.sent.clear()
    xs._last_announce_time = 0.0
    xs.tick()
    _deliver(xs, rv)

    assert time.time() - rv._peers["guid-xs"]["last_seen"] < 1.0


# ---------------------------------------------------------------------------
# Master election is a separate axis and must stay untouched
# ---------------------------------------------------------------------------


def test_departure_does_not_trigger_master_failover():
    xs, rv = _joined_pair()
    master_before = rv.master_guid
    was_master = rv.is_master

    xs.network.sent.clear()
    xs._send_message(pm.PeerDepart(peer_guid="guid-xs"))
    _deliver(xs, rv)

    assert rv.master_guid == master_before
    assert rv.is_master == was_master


# ---------------------------------------------------------------------------
# Snapshot roster — learning peers without an answer cascade
# ---------------------------------------------------------------------------


def test_a_joiner_adopts_the_roster_and_agrees_on_the_host():
    xs, rv = _joined_pair()

    joiner = _manager("guid-new", "openrv")
    joiner.start_session()
    joiner.apply_snapshot(
        pm.StateSnapshot(
            target_guid="guid-new",
            host_guid=xs.host_guid,
            peers=xs._peer_roster(),
        ).to_payload()
    )

    assert set(joiner._peers) == {"guid-xs", "guid-rv", "guid-new"}
    assert joiner.host_guid == "guid-xs"
    assert joiner.is_host is False


def test_the_roster_carries_no_liveness_stamp():
    """`last_seen` is the receiver's own clock reading. Putting one machine's
    clock on the wire would need skew handling to interpret, for no gain."""
    xs, _ = _joined_pair()

    roster = xs._peer_roster()

    assert roster
    for entry in roster.values():
        assert set(entry).issuperset({"app", "capabilities"})
        assert "last_seen" not in entry


def test_an_adopted_peer_is_stamped_locally_and_not_immediately_aged():
    joiner = _manager("guid-new", "openrv")
    joiner.start_session()

    joiner.adopt_peers({"guid-xs": {"app": "xstudio", "capabilities": ["visibility"]}})
    joiner.tick()

    assert "guid-xs" in joiner._peers


def test_a_missing_roster_does_not_blank_the_table():
    """A peer predating the roster field sends no roster. Treating that as
    "no peers" would erase a table already built from announcements."""
    xs, rv = _joined_pair()
    assert "guid-xs" in rv._peers

    rv.adopt_peers(None)
    rv.adopt_peers({})

    assert "guid-xs" in rv._peers


def test_a_joiner_with_no_snapshot_still_learns_peers_from_heartbeats():
    """The case that makes retiring the answer cascade safe.

    `send_state_snapshot` returns early when the master holds no timelines, so a
    joiner into an empty session gets no roster at all. It must still converge —
    bounded by the heartbeat interval rather than never.
    """
    xs = _manager("guid-xs", "xstudio")
    xs.start_session()

    joiner = _manager("guid-new", "openrv")
    joiner.start_session()
    assert "guid-xs" not in joiner._peers

    # No snapshot is ever delivered. Only the heartbeat.
    xs.network.sent.clear()
    xs._last_announce_time = 0.0
    xs.tick()
    _deliver(xs, joiner)

    assert "guid-xs" in joiner._peers
    assert joiner.host_guid == "guid-xs"


def test_roster_round_trips_through_the_snapshot_payload():
    msg = pm.StateSnapshot(
        target_guid="j",
        peers={"guid-xs": {"app": "xstudio", "capabilities": ["visibility"]}},
    )

    back = pm.StateSnapshot.from_payload(msg.to_payload())

    assert back.peers == msg.peers


def test_an_empty_roster_is_omitted_from_the_payload():
    """Same convention as `host_guid`: absent rather than empty, so a peer that
    predates the field is indistinguishable from one with nothing to say."""
    payload = pm.StateSnapshot(target_guid="j").to_payload()

    assert "peers" not in payload
    assert pm.StateSnapshot.from_payload(payload).peers == {}
