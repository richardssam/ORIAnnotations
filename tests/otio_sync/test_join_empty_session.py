"""Tests for joining a session whose master has nothing loaded yet.

A Join waits on the master's answer to its ``STATE_REQUEST``; with no answer it
times out and reports the session as not found (session-join-validation). A
master used to send nothing while it held no timelines, so a session that
existed but was empty could never be joined — the joiner was told it did not
exist. These pin that an empty master answers, and that the answer is enough
for the joiner to reach ``STATE_SYNCED`` knowing who is in the session.
"""

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import SyncManager, STATE_SYNCED  # noqa: E402
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


def _manager(guid, app):
    return SyncManager(session_id="s", self_guid=guid, network=FakeNetwork(), app_name=app)


def _events(net, event):
    return [e for e in net.sent if e["payload"]["command"]["event"] == event]


def _empty_master():
    master = _manager("guid-master", "xstudio")
    master.start_session()
    master.elect_self_as_master()
    assert master.is_master
    assert not master._timelines
    return master


def _deliver(source, target):
    for envelope in list(source.network.sent):
        target.apply_patch(envelope)


def _request_state(master, joiner):
    """Run discovery, then deliver the joiner's STATE_REQUEST to *master*.

    :returns: What the master's handler reports for that request.
    """
    # Discovery: the joiner's WHO_IS_MASTER, then the master's I_AM_MASTER.
    _deliver(joiner, master)
    _deliver(master, joiner)
    assert joiner.master_guid == "guid-master"
    joiner.request_state()
    request = _events(joiner.network, pm.StateRequest.EVENT)[-1]
    return master.apply_patch(request)


def test_an_empty_master_answers_a_state_request():
    master = _empty_master()
    joiner = _manager("guid-joiner", "openrv")
    joiner.start_session()

    assert _request_state(master, joiner) == ("state_request_received", "guid-joiner")
    master.send_state_snapshot("guid-joiner")

    snapshots = _events(master.network, pm.StateSnapshot.EVENT)
    assert len(snapshots) == 1
    payload = snapshots[0]["payload"]["command"]["payload"]
    assert payload["target_guid"] == "guid-joiner"
    assert not payload.get("timelines")


def test_a_joiner_into_an_empty_session_reaches_synced_and_learns_the_master():
    master = _empty_master()
    joiner = _manager("guid-joiner", "openrv")
    joiner.start_session()
    _request_state(master, joiner)
    master.send_state_snapshot("guid-joiner")
    snapshot = _events(master.network, pm.StateSnapshot.EVENT)[-1]

    joiner.apply_snapshot(snapshot["payload"]["command"]["payload"])

    assert joiner.status == STATE_SYNCED
    assert joiner._timelines == {}
    assert "guid-master" in joiner._peers


def test_a_non_master_still_sends_no_snapshot():
    peer = _manager("guid-peer", "xstudio")
    peer.start_session()
    assert not peer.is_master

    peer.send_state_snapshot("guid-joiner")

    assert _events(peer.network, pm.StateSnapshot.EVENT) == []
