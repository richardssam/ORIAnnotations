"""Tests that role reaches every peer by every path that carries a peer.

There are two paths into the peer table — a peer's own ``PEER_ANNOUNCE``, and
the roster inside a ``STATE_SNAPSHOT`` — and host eligibility is evaluated
against that table.  The answer-to-announce cascade was deliberately removed, so
the roster is not a redundant copy: for a peer that has gone quiet it is the
*only* source until its next heartbeat.  Role on the announcement alone would
leave adopted peers role-less, and an eligibility filter reading that table
would conclude the session has no drivers — a false driverless report, not
merely a late election.
"""

import os
import sys

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


def _manager(guid="self-guid", user="alice", app="openrv", **kwargs):
    mgr = SyncManager(
        session_id="s",
        self_guid=guid,
        network=FakeNetwork(),
        app_name=app,
        identity_override={"user": user},
        **kwargs,
    )
    mgr.status = STATE_SYNCED
    return mgr


def _announce_envelope(guid, role=None, app="xstudio", caps=("visibility",)):
    payload = {"peer_guid": guid, "app": app, "capabilities": list(caps)}
    if role is not None:
        payload["role"] = role
    return {
        "session": "s",
        "source_guid": guid,
        "payload": {
            "command_schema": pm.PeerAnnounce.SCHEMA,
            "command": {"event": pm.PeerAnnounce.EVENT, "payload": payload},
        },
    }


# ---------------------------------------------------------------------------
# The announcement path
# ---------------------------------------------------------------------------

def test_an_announcement_carries_this_peer_s_role():
    mgr = _manager(default_role=authority.REVIEWER)

    mgr.announce_peer()

    sent = [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command"]["event"] == pm.PeerAnnounce.EVENT
    ][-1]
    assert sent["role"] == authority.REVIEWER
    assert sent["app"] == "openrv"
    assert sent["capabilities"] == ["visibility"]


def test_a_received_announcement_records_the_sender_s_role():
    mgr = _manager()

    mgr.apply_patch(_announce_envelope("peer-a", role=authority.VIEWER))

    assert mgr.role_for_peer("peer-a") == authority.VIEWER


def test_an_announcement_with_no_role_leaves_the_entry_role_less():
    """Absence is carried through, not filled in, so the peer's own next
    announcement is not shadowed by someone's idea of the default."""
    mgr = _manager()

    mgr.apply_patch(_announce_envelope("peer-a"))

    assert "role" not in mgr._peers["peer-a"]
    assert mgr.role_for_peer("peer-a") == authority.DRIVER


def test_a_later_announcement_updates_a_role():
    mgr = _manager()
    mgr.apply_patch(_announce_envelope("peer-a", role=authority.VIEWER))

    mgr.apply_patch(_announce_envelope("peer-a", role=authority.DRIVER))

    assert mgr.role_for_peer("peer-a") == authority.DRIVER


# ---------------------------------------------------------------------------
# The roster path
# ---------------------------------------------------------------------------

def test_the_roster_carries_role():
    mgr = _manager(default_role=authority.VIEWER)
    mgr.apply_patch(_announce_envelope("peer-a", role=authority.DRIVER))

    roster = mgr._peer_roster()

    assert roster["peer-a"]["role"] == authority.DRIVER
    assert roster[mgr.self_guid]["role"] == authority.VIEWER


def test_a_peer_learned_only_from_a_roster_is_as_role_identifiable():
    """A quiet peer is known to a joiner only through the roster."""
    joiner = _manager(guid="joiner")

    joiner.adopt_peers({
        "quiet-peer": {"app": "xstudio", "capabilities": ["visibility"],
                       "role": authority.VIEWER},
    })

    assert joiner.role_for_peer("quiet-peer") == authority.VIEWER


def test_both_paths_agree():
    joiner = _manager(guid="joiner")
    joiner.adopt_peers({
        "peer-a": {"app": "xstudio", "capabilities": ["visibility"],
                   "role": authority.REVIEWER},
    })

    joiner.apply_patch(_announce_envelope("peer-a", role=authority.REVIEWER))

    assert joiner.role_for_peer("peer-a") == authority.REVIEWER


def test_a_roster_written_by_older_code_does_not_empty_the_driver_set():
    """One old peer must not make a session with drivers in it look driverless."""
    joiner = _manager(guid="joiner")

    joiner.adopt_peers({
        "old-peer": {"app": "xstudio", "capabilities": ["visibility"]},
    })

    assert joiner.role_for_peer("old-peer") == authority.DRIVER
    assert joiner.has_eligible_driver() is True
    assert authority.elect_host_guid(joiner._peers, joiner.default_role) == "old-peer"


def test_an_adopted_role_does_not_overwrite_one_heard_first_hand():
    """The announcer's own word outranks a third party's recollection of it."""
    mgr = _manager()
    mgr.apply_patch(_announce_envelope("peer-a", role=authority.DRIVER))

    mgr.adopt_peers({
        "peer-a": {"app": "xstudio", "capabilities": ["visibility"],
                   "role": authority.VIEWER},
    })

    assert mgr.role_for_peer("peer-a") == authority.DRIVER


# ---------------------------------------------------------------------------
# Policy in the snapshot
# ---------------------------------------------------------------------------

def test_a_snapshot_carries_the_role_policy_to_a_joiner():
    master = _manager(guid="master", default_role=authority.VIEWER,
                      peer_roles={"alice": authority.DRIVER})
    joiner = _manager(guid="joiner", user="bob")

    joiner.apply_snapshot(master.export_state())

    assert joiner.default_role == authority.VIEWER
    assert joiner.self_role == authority.VIEWER
    assert joiner.role_policy()["peer_roles"] == {"alice": authority.DRIVER}


def test_a_joiner_the_session_remembers_is_restored_from_the_snapshot():
    master = _manager(guid="master", default_role=authority.VIEWER,
                      peer_roles={"alice": authority.DRIVER})
    joiner = _manager(guid="joiner-new-guid", user="alice")

    joiner.apply_snapshot(master.export_state())

    assert joiner.self_role == authority.DRIVER


def test_a_snapshot_with_no_policy_cannot_clear_a_declared_one():
    peer = _manager(default_role=authority.VIEWER)
    old_peer_snapshot = dict(_manager(guid="old").export_state())
    assert "session_roles" not in old_peer_snapshot

    peer.apply_snapshot(old_peer_snapshot)

    assert peer.default_role == authority.VIEWER
    assert peer.self_role == authority.VIEWER


def test_the_roster_and_the_policy_travel_in_the_same_snapshot():
    master = _manager(guid="master", default_role=authority.VIEWER)
    master.apply_patch(_announce_envelope("quiet-driver", role=authority.DRIVER))
    joiner = _manager(guid="joiner", user="bob")

    joiner.apply_snapshot(master.export_state())

    # Both halves arrived together: the joiner can evaluate host eligibility
    # without waiting for the quiet peer's next heartbeat.
    assert joiner.default_role == authority.VIEWER
    assert joiner.role_for_peer("quiet-driver") == authority.DRIVER
    assert joiner.has_eligible_driver() is True
