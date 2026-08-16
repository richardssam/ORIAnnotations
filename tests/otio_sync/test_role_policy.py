"""Tests for session role policy: the default, the memory, and adoption.

Role assignment is *identity-memory first, default second*.  The memory is
keyed on the participant's ``user`` rather than on the peer GUID, and every test
here that looks like it is about dictionaries is really about the one case that
motivated the whole mechanism: a driver who drops and rejoins their own session
comes back under a **new GUID**, and must not land on the session default and be
locked out of the screening they are running.
"""

import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import authority  # noqa: E402
from otio_sync_core.manager import SyncManager  # noqa: E402
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


def _manager(guid="self-guid", user="alice", **kwargs):
    return SyncManager(
        session_id="s",
        self_guid=guid,
        network=FakeNetwork(),
        app_name="openrv",
        identity_override={"user": user, "host": "ws1", "source": "override"},
        **kwargs,
    )


def _announces(mgr):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command"]["event"] == pm.PeerAnnounce.EVENT
    ]


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def test_a_session_with_no_policy_makes_everyone_a_driver():
    mgr = _manager()

    assert mgr.default_role == authority.DRIVER
    assert mgr.self_role == authority.DRIVER
    # Nothing to tell a joiner: the section is omitted, not sent empty.
    assert mgr.role_policy() is None


def test_an_unrecognised_participant_receives_the_default():
    mgr = _manager(user="stranger", default_role=authority.VIEWER)

    assert mgr.self_role == authority.VIEWER


def test_memory_is_consulted_before_the_default():
    mgr = _manager(
        user="alice",
        default_role=authority.VIEWER,
        peer_roles={"alice": authority.DRIVER},
    )

    assert mgr.self_role == authority.DRIVER


def test_a_reconnecting_driver_keeps_its_role_under_a_new_guid():
    """The case GUID-keyed memory cannot serve, and the reason the key is identity."""
    policy = {"default_role": authority.VIEWER, "peer_roles": {"alice": authority.DRIVER}}

    first = _manager(guid="guid-before", user="alice", **policy)
    reconnected = _manager(guid="guid-after-crash", user="alice", **policy)

    assert first.self_guid != reconnected.self_guid
    assert reconnected.self_role == authority.DRIVER


def test_one_participant_on_two_machines_holds_one_role():
    """The key is the account, not account-and-machine: a supervisor with a
    workstation and a laptop is one person with one role."""
    policy = {"default_role": authority.VIEWER, "peer_roles": {"alice": authority.REVIEWER}}

    workstation = SyncManager(
        session_id="s", self_guid="a", network=FakeNetwork(),
        identity_override={"user": "alice", "host": "ws1"}, **policy
    )
    laptop = SyncManager(
        session_id="s", self_guid="b", network=FakeNetwork(),
        identity_override={"user": "alice", "host": "laptop"}, **policy
    )

    assert workstation.self_role == laptop.self_role == authority.REVIEWER


def test_a_nonsense_role_in_a_policy_does_not_lock_a_participant_out():
    mgr = _manager(user="alice", peer_roles={"alice": "supervisor"})

    assert mgr.self_role == authority.DRIVER


def test_a_policy_can_be_declared_in_the_environment(monkeypatch):
    """Resolved in core, so both host applications get it from one place and a
    session can be started with a policy before any UI exists to edit one."""
    monkeypatch.setenv(authority.ROLE_DEFAULT_ENV, "viewer")
    monkeypatch.setenv(authority.ROLE_MEMORY_ENV, "alice=driver, bob = reviewer ")

    mgr = _manager(user="bob")

    assert mgr.default_role == authority.VIEWER
    assert mgr.self_role == authority.REVIEWER
    assert mgr.role_policy()["peer_roles"] == {
        "alice": authority.DRIVER,
        "bob": authority.REVIEWER,
    }


def test_an_explicit_argument_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv(authority.ROLE_DEFAULT_ENV, "viewer")

    mgr = _manager(default_role=authority.DRIVER)

    assert mgr.default_role == authority.DRIVER


# ---------------------------------------------------------------------------
# Create-time creator seeding (session-role-config "The create-time trap")
# ---------------------------------------------------------------------------


def test_declaring_a_restrictive_default_with_seed_creator_seeds_the_creator():
    mgr = _manager(user="organiser", default_role=authority.VIEWER, seed_creator=True)

    assert mgr.default_role == authority.VIEWER
    assert mgr.self_role == authority.DRIVER
    assert mgr.has_eligible_driver() is True


def test_a_restrictive_default_without_seed_creator_does_not_seed():
    """default_role= alone (no seed_creator) is also the established way to
    pin a constructed peer's own role for reasons other than starting a
    session — e.g. simulating a joiner, or a test fixture. It must not be
    silently treated as session creation."""
    mgr = _manager(user="participant", default_role=authority.VIEWER)

    assert mgr.default_role == authority.VIEWER
    assert mgr.self_role == authority.VIEWER


def test_the_same_restrictive_default_via_the_environment_does_not_seed(monkeypatch):
    """The env var is read by every peer that has it set, joiners included —
    seeding on it would make every env-configured peer a driver even with
    seed_creator=True."""
    monkeypatch.setenv(authority.ROLE_DEFAULT_ENV, "viewer")

    mgr = _manager(user="joiner", seed_creator=True)

    assert mgr.default_role == authority.VIEWER
    assert mgr.self_role == authority.VIEWER


def test_the_argument_wins_the_seeding_decision_over_the_environment(monkeypatch):
    """Both declare a restrictive default; only the constructor argument (the
    create path, with seed_creator=True) seeds the creator as driver."""
    monkeypatch.setenv(authority.ROLE_DEFAULT_ENV, "viewer")

    mgr = _manager(user="organiser", default_role=authority.REVIEWER, seed_creator=True)

    assert mgr.default_role == authority.REVIEWER
    assert mgr.self_role == authority.DRIVER


def test_declaring_the_permissive_default_seeds_nobody():
    mgr = _manager(user="organiser", default_role=authority.DRIVER, seed_creator=True)

    assert mgr.role_policy() is None


def test_a_malformed_environment_policy_does_not_stop_a_session(monkeypatch):
    monkeypatch.setenv(authority.ROLE_MEMORY_ENV, "garbage,,=,alice=driver")

    mgr = _manager(user="alice")

    assert mgr.self_role == authority.DRIVER


# ---------------------------------------------------------------------------
# Adoption
# ---------------------------------------------------------------------------

def test_adopting_a_policy_reassigns_this_peer_and_re_announces():
    mgr = _manager(user="bob")
    before = len(_announces(mgr))

    mgr.adopt_role_policy({"default_role": authority.VIEWER, "peer_roles": {}})

    assert mgr.self_role == authority.VIEWER
    assert len(_announces(mgr)) == before + 1
    assert _announces(mgr)[-1]["role"] == authority.VIEWER


def test_an_absent_policy_leaves_a_declared_one_intact():
    """A peer predating roles cannot clear a screening's policy by relaying state."""
    mgr = _manager(default_role=authority.VIEWER)

    mgr.adopt_role_policy(None)
    mgr.adopt_role_policy({})

    assert mgr.default_role == authority.VIEWER
    assert mgr.self_role == authority.VIEWER


def test_adoption_merges_memory_rather_than_replacing_it():
    mgr = _manager(peer_roles={"carol": authority.REVIEWER})

    mgr.adopt_role_policy({"peer_roles": {"dave": authority.DRIVER}})

    assert mgr.role_policy()["peer_roles"] == {
        "carol": authority.REVIEWER,
        "dave": authority.DRIVER,
    }


def test_adopting_an_unchanged_policy_does_not_re_announce():
    mgr = _manager(default_role=authority.VIEWER)
    before = len(_announces(mgr))

    mgr.adopt_role_policy({"default_role": authority.VIEWER})

    assert len(_announces(mgr)) == before


def test_a_declared_policy_is_carried_in_the_snapshot_payload():
    mgr = _manager(default_role=authority.VIEWER, peer_roles={"alice": authority.DRIVER})

    payload = mgr.export_state()

    assert payload["session_roles"] == {
        "default_role": authority.VIEWER,
        "peer_roles": {"alice": authority.DRIVER},
    }


def test_no_declared_policy_omits_the_section_entirely():
    mgr = _manager()

    assert "session_roles" not in mgr.export_state()


def test_role_policy_is_a_copy_not_a_window_onto_manager_state():
    mgr = _manager(peer_roles={"alice": authority.DRIVER})

    mgr.role_policy()["peer_roles"]["alice"] = authority.VIEWER

    assert mgr.role_policy()["peer_roles"]["alice"] == authority.DRIVER


# ---------------------------------------------------------------------------
# Lifetime
# ---------------------------------------------------------------------------

def test_departure_does_not_erase_the_session_s_memory_of_a_role():
    """The peer table records presence; the role map records a decision about a
    participant.  Conflating them breaks reconnection."""
    mgr = _manager(default_role=authority.VIEWER, peer_roles={"alice": authority.DRIVER})
    mgr._peers["peer-b"] = {"app": "xstudio", "capabilities": ["visibility"], "last_seen": 0.0}

    mgr.drop_peer("peer-b")

    assert "peer-b" not in mgr._peers
    assert mgr.role_policy()["peer_roles"] == {"alice": authority.DRIVER}


def test_policy_does_not_outlive_the_session():
    """Nothing is persisted: a new manager for the same session name starts clean."""
    _manager(default_role=authority.VIEWER, peer_roles={"alice": authority.DRIVER})

    fresh = _manager(guid="another")

    assert fresh.default_role == authority.DRIVER
    assert fresh.role_policy() is None
