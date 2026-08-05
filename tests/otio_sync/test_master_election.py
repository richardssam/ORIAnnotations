"""Tests for ``SyncManager.elect_self_as_master`` — the single election operation.

Covers the state transitions it owns, their *order* (the ``on_synced`` callbacks
branch on ``is_master``, so they must observe a fully-elected manager), the
deferred-announce mode OpenRV relies on, and wire compatibility with the
pre-encapsulation call sites.
"""

import copy
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import (  # noqa: E402
    SyncManager,
    STATE_DISCOVERING,
    STATE_SYNCED,
)
from otio_sync_core import protocol_messages as pm  # noqa: E402


class FakeNetwork:
    """Captures sent envelopes (SyncNetworkProtocol)."""

    def __init__(self):
        self.sent = []

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payloads(self):
        return []

    def stop(self):
        pass


def _discovering_manager():
    net = FakeNetwork()
    mgr = SyncManager(session_id="s", self_guid="self-guid", network=net)
    mgr.status = STATE_DISCOVERING
    return mgr, net


def _i_am_master_envelopes(net):
    return [
        e for e in net.sent
        if e["payload"]["command"]["event"] == pm.IAmMaster.EVENT
    ]


# ---------------------------------------------------------------------------
# 4.1 — end state
# ---------------------------------------------------------------------------


def test_elect_sets_full_election_state():
    mgr, net = _discovering_manager()

    mgr.elect_self_as_master()

    assert mgr.is_master is True
    assert mgr.master_guid == mgr.self_guid == "self-guid"
    assert mgr.status == STATE_SYNCED

    sent = _i_am_master_envelopes(net)
    assert len(sent) == 1
    assert sent[0]["payload"]["command"]["payload"]["master_guid"] == "self-guid"


def test_elect_without_broadcast_applies_local_state_only():
    mgr, net = _discovering_manager()

    mgr.elect_self_as_master(broadcast=False)

    # Local election is complete...
    assert mgr.is_master is True
    assert mgr.master_guid == "self-guid"
    assert mgr.status == STATE_SYNCED
    # ...but nothing has been announced yet.
    assert _i_am_master_envelopes(net) == []

    # The caller announces when it is ready to serve a STATE_REQUEST.
    mgr.broadcast_master_response()
    assert len(_i_am_master_envelopes(net)) == 1


def test_reelecting_existing_master_does_not_refire_synced():
    mgr, _net = _discovering_manager()
    fired = []
    mgr.on_synced(lambda: fired.append(1))

    mgr.elect_self_as_master()
    assert fired == [1]

    # Master failover re-elects a peer that is already master and SYNCED.
    mgr.elect_self_as_master()
    assert fired == [1], "on_synced must not re-fire when the status is unchanged"
    assert mgr.is_master is True
    assert mgr.master_guid == "self-guid"


# ---------------------------------------------------------------------------
# 4.2 — ordering
# ---------------------------------------------------------------------------


def test_synced_callback_observes_completed_election():
    """is_master/master_guid must be set *before* the status transition.

    xStudio's ``_on_synced`` enqueues ``load_timelines`` only for a client and
    RV's rebuilds the session, so a callback that ran mid-election would take
    the client branch on a master.
    """
    mgr, net = _discovering_manager()
    observed = {}

    @mgr.on_synced
    def _capture():
        observed["is_master"] = mgr.is_master
        observed["master_guid"] = mgr.master_guid
        observed["announced"] = len(_i_am_master_envelopes(net))

    mgr.elect_self_as_master()

    assert observed["is_master"] is True
    assert observed["master_guid"] == "self-guid"
    # The announce also precedes the status change, preserving the wire order
    # the pre-encapsulation call sites produced.
    assert observed["announced"] == 1


def test_status_callback_observes_completed_election():
    mgr, _net = _discovering_manager()
    seen = []

    mgr.on_status_changed(lambda s: seen.append((s, mgr.is_master, mgr.master_guid)))

    mgr.elect_self_as_master()

    assert seen == [(STATE_SYNCED, True, "self-guid")]


# ---------------------------------------------------------------------------
# 4.3 — wire compatibility
# ---------------------------------------------------------------------------


def test_elected_announcement_matches_broadcast_master_response():
    """The election must not change what an older peer sees on the wire."""
    mgr_a, net_a = _discovering_manager()
    mgr_a.elect_self_as_master()
    elected = copy.deepcopy(_i_am_master_envelopes(net_a)[0])

    mgr_b, net_b = _discovering_manager()
    mgr_b.broadcast_master_response()
    direct = copy.deepcopy(_i_am_master_envelopes(net_b)[0])

    # Timestamps, if any, are the only permitted difference.
    for env in (elected, direct):
        env.pop("timestamp", None)
        env["payload"]["command"].pop("timestamp", None)

    assert elected == direct


def test_peer_applies_our_announcement_as_master_found():
    """Round-trip: a receiving peer resolves our election to its master GUID."""
    mgr, net = _discovering_manager()
    mgr.elect_self_as_master()
    envelope = _i_am_master_envelopes(net)[0]

    peer = SyncManager(session_id="s", self_guid="peer-guid", network=FakeNetwork())
    peer.status = STATE_DISCOVERING
    action = peer.apply_patch(envelope)

    assert action == ("master_found", "self-guid")
    assert peer.master_guid == "self-guid"
    assert peer.is_master is False
