"""Tests for granting a role to a participant during a session.

``set_peer_role`` is deliberately a thin issuer-side gate wrapped around the
existing ``adopt_role_policy`` merge (design.md D2): the target applies the
grant to itself and re-announces, every other peer merges it into its own
identity-keyed memory without writing the peer table, and the grant survives
the target's reconnection because it is addressed by identity rather than by
GUID.  These tests exercise that path end to end rather than re-testing
``adopt_role_policy``'s merge semantics, which ``test_role_policy.py`` already
covers.
"""

import os
import sys

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


def _manager(guid, user, app_name="openrv", **kwargs):
    mgr = SyncManager(
        session_id="s",
        self_guid=guid,
        network=FakeNetwork(),
        app_name=app_name,
        identity_override={"user": user},
        **kwargs,
    )
    mgr.status = STATE_SYNCED
    return mgr


def _deliver(source, *targets):
    """Hand every envelope *source* has sent to each of *targets*, matching
    ``test_host_election.py``'s helper."""
    for envelope in list(source.network.sent):
        for target in targets:
            target.apply_patch(envelope)


def _grants(mgr):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command"]["event"] == pm.SetPeerRole.EVENT
    ]


def _announces(mgr):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command"]["event"] == pm.PeerAnnounce.EVENT
    ]


# ---------------------------------------------------------------------------
# Who may issue a grant
# ---------------------------------------------------------------------------


def test_a_driver_may_grant():
    driver = _manager("g1", "alice")

    assert driver.set_peer_role("bob", authority.VIEWER) is True
    assert len(_grants(driver)) == 1
    assert _grants(driver)[0]["user"] == "bob"
    assert _grants(driver)[0]["role"] == authority.VIEWER


def test_a_reviewer_may_not_grant():
    reviewer = _manager("g1", "alice", peer_roles={"alice": authority.REVIEWER})

    assert reviewer.set_peer_role("bob", authority.DRIVER) is False
    assert _grants(reviewer) == []
    assert reviewer.role_policy() is None or "bob" not in reviewer.role_policy().get("peer_roles", {})


def test_a_viewer_may_not_grant():
    viewer = _manager("g1", "alice", peer_roles={"alice": authority.VIEWER})

    assert viewer.set_peer_role("bob", authority.DRIVER) is False
    assert _grants(viewer) == []


def test_a_driver_that_is_not_host_may_grant():
    """Administration is not restricted to the elected host."""
    driver = _manager("g1", "alice")
    driver.is_host = False

    assert driver.set_peer_role("bob", authority.REVIEWER) is True


# ---------------------------------------------------------------------------
# Applying the grant: target applies and announces, others merge silently
# ---------------------------------------------------------------------------


def test_the_target_applies_the_grant_and_announces():
    driver = _manager("g1", "alice")
    target = _manager("g2", "bob")
    before = len(_announces(target))

    driver.set_peer_role("bob", authority.VIEWER)
    _deliver(driver, target)

    assert target.self_role == authority.VIEWER
    assert len(_announces(target)) == before + 1
    assert _announces(target)[-1]["role"] == authority.VIEWER


def test_a_non_target_merges_without_writing_the_peer_table_or_announcing():
    driver = _manager("g1", "alice")
    bystander = _manager("g3", "carol")
    before = len(_announces(bystander))

    driver.set_peer_role("bob", authority.VIEWER)
    _deliver(driver, bystander)

    # Merged into this peer's own memory...
    assert bystander.role_policy()["peer_roles"]["bob"] == authority.VIEWER
    # ...but bob is not a known peer here, so nothing was written into the
    # peer table, and this peer's own role/announcement is untouched.
    assert "bob" not in bystander._peers
    assert bystander.self_role == authority.DRIVER
    assert len(_announces(bystander)) == before


def test_a_redundant_grant_does_not_re_announce():
    driver = _manager("g1", "alice")
    target = _manager("g2", "bob", peer_roles={"bob": authority.VIEWER})
    before = len(_announces(target))

    driver.set_peer_role("bob", authority.VIEWER)
    _deliver(driver, target)

    assert len(_announces(target)) == before


def test_the_issuer_applies_its_own_grant_locally():
    """The network layer discards a peer's own broadcasts, so the issuer must
    apply the grant to itself before/without relying on receiving it back."""
    driver = _manager("g1", "alice")

    driver.set_peer_role("alice", authority.REVIEWER)

    assert driver.self_role == authority.REVIEWER


# ---------------------------------------------------------------------------
# Reaching the master's memory, and surviving reconnection
# ---------------------------------------------------------------------------


def test_a_non_master_driver_s_grant_reaches_the_master_s_memory():
    master = _manager("master", "host_user")
    master.is_master = True
    other_driver = _manager("g2", "alice")

    other_driver.set_peer_role("bob", authority.DRIVER)
    _deliver(other_driver, master)

    assert master.export_state()["session_roles"]["peer_roles"]["bob"] == authority.DRIVER


def test_a_grant_survives_the_target_s_reconnection():
    # alice is explicitly seeded as driver here, standing in for the
    # constructor-time creator-seeding rule (session-role-config) this test
    # does not depend on — it is exercising set_peer_role in isolation.
    driver = _manager(
        "g1", "alice", default_role=authority.VIEWER, peer_roles={"alice": authority.DRIVER}
    )
    target_before = _manager("g2", "bob", default_role=authority.VIEWER)

    driver.set_peer_role("bob", authority.DRIVER)
    _deliver(driver, target_before)
    assert target_before.self_role == authority.DRIVER

    # Reconnects under a new GUID, carrying the same remembered policy forward
    # (as STATE_SNAPSHOT would deliver it) — the case identity-keying exists for.
    policy = target_before.role_policy()
    target_after = _manager(
        "g2-after-reconnect", "bob",
        default_role=authority.VIEWER,
        peer_roles=policy["peer_roles"],
    )

    assert target_after.self_guid != target_before.self_guid
    assert target_after.self_role == authority.DRIVER


# ---------------------------------------------------------------------------
# Effect on enforcement and host eligibility
# ---------------------------------------------------------------------------


def test_demoting_the_host_re_elects_onto_another_eligible_driver():
    host = _manager("host-g", "alice", app_name="xstudio")
    other_driver = _manager("other-g", "bob", app_name="openrv")
    # No visibility capability: an administrator who is a driver but not
    # itself host-eligible, so the remaining candidate is unambiguous.
    admin = _manager("admin-g", "carol", app_name="openrv", capabilities=[])
    for m in (host, other_driver, admin):
        m.start_session()

    _deliver(host, other_driver, admin)
    _deliver(other_driver, host, admin)
    _deliver(admin, host, other_driver)
    for m in (host, other_driver, admin):
        m.elect_host()
    assert host.host_guid == "host-g"

    admin.set_peer_role("alice", authority.VIEWER)
    _deliver(admin, host, other_driver)
    # Adopting the grant makes host re-announce its own demotion (design D2);
    # that re-announcement, not the grant itself, is what updates the peer
    # table other_driver reads (session-role-administration: "A grant is
    # applied by its target, and other peers learn it by announcement").
    _deliver(host, other_driver)
    host.elect_host()
    other_driver.elect_host()

    assert host.host_guid == "other-g"
    assert other_driver.host_guid == "other-g"


def test_demoting_the_last_driver_leaves_the_session_driverless_and_recoverable():
    driver = _manager("g1", "alice")

    driver.set_peer_role("alice", authority.VIEWER)

    assert driver.self_role == authority.VIEWER
    assert driver.has_eligible_driver() is False
    assert driver.elect_role_to_driver() is True
    assert driver.self_role == authority.DRIVER


# ---------------------------------------------------------------------------
# A grant is not self-elevation, and does not touch the default
# ---------------------------------------------------------------------------


def test_a_grant_does_not_change_the_session_default():
    driver = _manager(
        "g1", "alice", default_role=authority.VIEWER, peer_roles={"alice": authority.DRIVER}
    )

    assert driver.set_peer_role("bob", authority.DRIVER) is True
    assert driver.default_role == authority.VIEWER


def test_self_elevation_stays_refused_while_an_eligible_driver_exists():
    driver = _manager("g1", "alice")  # default role: driver
    viewer = _manager("g2", "bob", default_role=authority.VIEWER)

    assert viewer.has_eligible_driver() is False  # bob alone knows of no driver yet
    driver.start_session()
    _deliver(driver, viewer)
    assert viewer.has_eligible_driver() is True
    assert viewer.elect_role_to_driver() is False


# ---------------------------------------------------------------------------
# Compatibility: an unregistered / malformed grant does not raise
# ---------------------------------------------------------------------------


def test_a_grant_with_no_user_or_role_is_ignored():
    mgr = _manager("g1", "alice")
    before_policy = mgr.role_policy()

    result = mgr.apply_patch({
        "payload": {
            "command_schema": "LiveSession.1",
            "command": {"event": "SET_PEER_ROLE", "payload": {}},
        }
    })

    assert result is None
    assert mgr.role_policy() == before_policy


def test_an_unregistered_pair_is_ignored_without_raising():
    mgr = _manager("g1", "alice")

    result = mgr.apply_patch({
        "payload": {
            "command_schema": "NOPE",
            "command": {"event": "NOPE", "payload": {}},
        }
    })

    assert result is None
