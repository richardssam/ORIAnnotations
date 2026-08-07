"""Tests for host election — the visibility-authority role.

Covers who is elected from a given peer set, that every peer reaches the same
answer, that host stays distinct from master, the ordering the single election
operation guarantees, and the drain-time re-check that makes a request enqueued
from another thread safe.
"""

import os
import sys
import threading

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import authority  # noqa: E402
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


def _announcements(net):
    return [
        e for e in net.sent
        if e["payload"]["command"]["event"] == pm.PeerAnnounce.EVENT
    ]


# ---------------------------------------------------------------------------
# The election function itself — pure, so every peer agrees by construction
# ---------------------------------------------------------------------------


def test_preferred_application_hosts_when_present():
    peers = {
        "guid-rv": {"app": "openrv", "capabilities": ["visibility"]},
        "guid-xs": {"app": "xstudio", "capabilities": ["visibility"]},
    }

    assert authority.elect_host_guid(peers) == "guid-xs"


def test_session_without_a_preferred_peer_still_has_a_host():
    peers = {
        "guid-b": {"app": "openrv", "capabilities": ["visibility"]},
        "guid-a": {"app": "openrv", "capabilities": ["visibility"]},
    }

    # Not hostless, and tie-broken deterministically by GUID.
    assert authority.elect_host_guid(peers) == "guid-a"


def test_unranked_application_hosts_when_it_is_the_only_capable_peer():
    peers = {"guid-x": {"app": "some-future-player", "capabilities": ["visibility"]}}

    assert authority.elect_host_guid(peers) == "guid-x"


def test_ranked_peer_beats_unranked_peer():
    peers = {
        "guid-a": {"app": "some-future-player", "capabilities": ["visibility"]},
        "guid-z": {"app": "openrv", "capabilities": ["visibility"]},
    }

    # Preference wins over the GUID tie-break, which would have chosen guid-a.
    assert authority.elect_host_guid(peers) == "guid-z"


def test_peer_without_the_capability_is_never_elected():
    peers = {
        "guid-viewer": {"app": "sync_viewer", "capabilities": []},
        "guid-rv": {"app": "openrv", "capabilities": ["visibility"]},
    }

    assert authority.elect_host_guid(peers) == "guid-rv"


def test_no_capable_peer_yields_no_host():
    peers = {"guid-viewer": {"app": "sync_viewer", "capabilities": []}}

    assert authority.elect_host_guid(peers) is None


# ---------------------------------------------------------------------------
# Election across peers
# ---------------------------------------------------------------------------


def test_solo_peer_elects_itself():
    rv = _manager("guid-rv", "openrv")

    rv.start_session()

    assert rv.is_host is True
    assert rv.host_guid == "guid-rv"


def test_every_peer_reaches_the_same_host():
    xs = _manager("guid-xs", "xstudio")
    rv = _manager("guid-rv", "openrv")
    xs.start_session()
    rv.start_session()

    _deliver(rv, xs)
    _deliver(xs, rv)

    assert xs.host_guid == rv.host_guid == "guid-xs"
    assert xs.is_host is True
    assert rv.is_host is False


def test_rv_only_session_elects_an_rv_host():
    a = _manager("guid-a", "openrv")
    b = _manager("guid-b", "openrv")
    a.start_session()
    b.start_session()

    _deliver(a, b)
    _deliver(b, a)

    assert a.host_guid == b.host_guid == "guid-a"
    assert a.is_host is True
    assert b.is_host is False


def test_a_joining_preferred_peer_takes_the_role():
    rv = _manager("guid-rv", "openrv")
    rv.start_session()
    assert rv.is_host is True

    xs = _manager("guid-xs", "xstudio")
    xs.start_session()
    _deliver(xs, rv)

    assert rv.is_host is False
    assert rv.host_guid == "guid-xs"


def test_an_announcement_is_not_answered():
    """No answer cascade: join cost must not grow with the size of the session.

    Answering used to be how a joiner learned peers that had gone quiet. The
    snapshot roster and the periodic heartbeat cover that now, so the answer —
    the only step whose message count scaled with peer count — is gone.
    """
    xs = _manager("guid-xs", "xstudio")
    rv = _manager("guid-rv", "openrv")
    xs.start_session()
    rv.start_session()
    rv.network.sent.clear()

    _deliver(xs, rv)

    assert _announcements(rv.network) == []
    # The peer is still learned — it just costs nothing to learn.
    assert "guid-xs" in rv._peers


def test_host_change_fires_the_callback():
    rv = _manager("guid-rv", "openrv")
    seen = []
    rv.on_host_changed(lambda guid, is_host: seen.append((guid, is_host)))

    rv.start_session()

    assert seen == [("guid-rv", True)]


def test_re_electing_the_same_host_does_not_re_fire():
    rv = _manager("guid-rv", "openrv")
    rv.start_session()
    seen = []
    rv.on_host_changed(lambda guid, is_host: seen.append((guid, is_host)))

    rv.elect_host()

    assert seen == []


# ---------------------------------------------------------------------------
# Host is distinct from master (D2)
# ---------------------------------------------------------------------------


def test_master_re_election_does_not_change_the_host():
    xs = _manager("guid-xs", "xstudio")
    rv = _manager("guid-rv", "openrv")
    xs.start_session()
    rv.start_session()
    _deliver(rv, xs)
    _deliver(xs, rv)
    assert rv.is_host is False and rv.host_guid == "guid-xs"

    # RV becomes master — the snapshot authority — without becoming the host.
    rv.status = STATE_DISCOVERING
    rv.elect_self_as_master()

    assert rv.is_master is True
    assert rv.status == STATE_SYNCED
    assert rv.is_host is False
    assert rv.host_guid == "guid-xs"


def test_electing_master_never_touches_host_state_on_the_preferred_peer():
    xs = _manager("guid-xs", "xstudio")
    xs.start_session()
    assert xs.is_host is True

    xs.elect_self_as_master()

    assert xs.is_host is True
    assert xs.host_guid == "guid-xs"


# ---------------------------------------------------------------------------
# The single election operation: ordering and threading discipline
# ---------------------------------------------------------------------------


def test_callback_observes_a_fully_elected_manager():
    """Both fields are set before the callbacks run, not one of them.

    Ordering is the failure most likely to hide: a callback that branches on
    ``is_host`` while ``host_guid`` is still stale produces a peer that thinks
    it is following someone who no longer holds the role. Assert from inside the
    transition, not on the end state.
    """
    rv = _manager("guid-rv", "openrv")
    observed = {}

    @rv.on_host_changed
    def _record(guid, is_host):
        observed["host_guid_attr"] = rv.host_guid
        observed["is_host_attr"] = rv.is_host
        observed["args"] = (guid, is_host)

    rv.start_session()

    assert observed["host_guid_attr"] == "guid-rv"
    assert observed["is_host_attr"] is True
    assert observed["args"] == ("guid-rv", True)


def test_election_requested_from_another_thread_runs_on_the_tick_thread():
    rv = _manager("guid-rv", "openrv")
    rv._peers["guid-xs"] = {"app": "xstudio", "capabilities": ["visibility"]}
    rv.elect_host()
    assert rv.host_guid == "guid-xs"

    # A worker thread only enqueues; nothing about the role changes yet.
    def _worker():
        rv._peers.pop("guid-xs")
        rv.request_host_election("peer-left")

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    assert rv.host_guid == "guid-xs"

    rv.tick()

    assert rv.host_guid == "guid-rv"
    assert rv.is_host is True


def test_eligibility_is_re_checked_at_drain_time():
    """A host learned during queue latency cancels a self-election.

    The request is enqueued while this peer is the only capable one, then a
    preferred peer announces before the drain. Electing from the state at
    enqueue time would make this peer host; electing at drain time does not.
    """
    rv = _manager("guid-rv", "openrv")
    rv.start_session()
    assert rv.is_host is True

    rv.request_host_election("queued-while-alone")
    xs = _manager("guid-xs", "xstudio")
    xs.start_session()
    _deliver(xs, rv)

    rv.tick()

    assert rv.is_host is False
    assert rv.host_guid == "guid-xs"


def test_repeated_requests_collapse_into_one_election():
    rv = _manager("guid-rv", "openrv")
    rv.start_session()
    seen = []
    rv.on_host_changed(lambda guid, is_host: seen.append(guid))
    rv._peers["guid-xs"] = {"app": "xstudio", "capabilities": ["visibility"]}

    for _ in range(5):
        rv.request_host_election("spam")
    rv.tick()

    assert seen == ["guid-xs"]


def test_drop_peer_re_elects():
    rv = _manager("guid-rv", "openrv")
    rv._peers["guid-xs"] = {"app": "xstudio", "capabilities": ["visibility"]}
    rv.elect_host()
    assert rv.is_host is False

    rv.drop_peer("guid-xs")

    assert rv.is_host is True
    assert rv.host_guid == "guid-rv"


# ---------------------------------------------------------------------------
# Late joiners (2.3)
# ---------------------------------------------------------------------------


def test_snapshot_carries_the_elected_host():
    xs = _manager("guid-xs", "xstudio")
    xs.start_session()
    xs.elect_self_as_master()
    xs._timelines["tl"] = _timeline()

    xs.send_state_snapshot("guid-joiner")

    snapshots = [
        e for e in xs.network.sent
        if e["payload"]["command"]["event"] == pm.StateSnapshot.EVENT
    ]
    assert snapshots[-1]["payload"]["command"]["payload"]["host_guid"] == "guid-xs"


def test_joiner_adopts_the_host_from_the_snapshot():
    joiner = _manager("guid-joiner", "openrv")
    joiner.start_session()
    # Before the snapshot it is alone, so it elected itself.
    assert joiner.is_host is True

    joiner.apply_snapshot({
        "timelines": {},
        "active_timeline_guid": None,
        "snapshot_timestamp": 1.0,
        "host_guid": "guid-xs",
    })

    assert joiner.is_host is False
    assert joiner.host_guid == "guid-xs"


def test_snapshot_without_a_host_field_leaves_the_local_host_alone():
    """A peer predating the field must not clear an elected host."""
    joiner = _manager("guid-joiner", "openrv")
    joiner.start_session()

    joiner.apply_snapshot({
        "timelines": {},
        "active_timeline_guid": None,
        "snapshot_timestamp": 1.0,
    })

    assert joiner.host_guid == "guid-joiner"
    assert joiner.is_host is True


def test_export_state_reports_the_host_for_the_harness():
    xs = _manager("guid-xs", "xstudio")
    xs.start_session()

    payload = xs.export_state()

    assert payload["is_host"] is True
    assert payload["host_guid"] == "guid-xs"


def test_peer_announce_round_trips():
    msg = pm.PeerAnnounce(
        peer_guid="guid-xs",
        app="xstudio",
        capabilities=["visibility"],
    )

    back = pm.PeerAnnounce.from_payload(msg.to_payload())

    assert back == msg


def test_peer_announce_tolerates_a_bare_payload():
    """A peer that sends only its guid must not break the receiver."""
    back = pm.PeerAnnounce.from_payload({"peer_guid": "guid-x"})

    assert back.app == ""
    assert back.capabilities == []


def test_state_snapshot_round_trips_the_host():
    msg = pm.StateSnapshot(target_guid="j", host_guid="guid-xs")

    back = pm.StateSnapshot.from_payload(msg.to_payload())

    assert back.host_guid == "guid-xs"


def test_state_snapshot_omits_an_unset_host():
    """Keeps the wire identical for a peer that has not elected one."""
    assert "host_guid" not in pm.StateSnapshot(target_guid="j").to_payload()


def _timeline():
    import opentimelineio as otio
    tl = otio.schema.Timeline(name="seq")
    tl.metadata["sync"] = {"guid": "tl"}
    return tl
