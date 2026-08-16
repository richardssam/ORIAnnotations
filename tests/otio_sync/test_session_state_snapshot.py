"""Tests for the shared session-state projection.

``session_state_snapshot`` is the single source of truth behind every Session
State panel — OpenRV's Qt models today, an xStudio-native panel later — so the
semantics that could drift between hosts are asserted here, once, in plain
Python.  Nothing in this module imports Qt.
"""

import json
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core import authority  # noqa: E402
from otio_sync_core.manager import SyncManager, STATE_SYNCED  # noqa: E402
from otio_sync_core.session_state import (  # noqa: E402
    peer_role,
    session_state_snapshot,
)


class FakeNetwork:
    def __init__(self):
        self.sent = []

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payloads(self):
        return []

    def stop(self):
        pass


def _manager(guid="self-guid", app="openrv"):
    mgr = SyncManager(
        session_id="s", self_guid=guid, network=FakeNetwork(), app_name=app
    )
    mgr.status = STATE_SYNCED
    return mgr


def _add_peer(mgr, guid, app="xstudio", capabilities=("visibility",)):
    mgr._peers[guid] = {
        "app": app,
        "capabilities": list(capabilities),
        "last_seen": 0.0,
    }


def test_snapshot_reports_status_and_self():
    mgr = _manager()
    snap = session_state_snapshot(mgr)

    assert snap["status"] == STATE_SYNCED
    assert snap["self_guid"] == "self-guid"
    # The manager seeds its own peer entry, so a solo session still lists one.
    assert [p["guid"] for p in snap["peers"]] == ["self-guid"]
    assert snap["peers"][0]["is_self"] is True
    assert snap["peers"][0]["app"] == "openrv"


def test_self_sorts_first_then_guid_ascending():
    mgr = _manager(guid="zzz-self")
    _add_peer(mgr, "aaa-peer")
    _add_peer(mgr, "mmm-peer")

    guids = [p["guid"] for p in session_state_snapshot(mgr)["peers"]]

    # Self first even though its guid sorts last: a list view built from
    # successive snapshots must not reshuffle around the local peer.
    assert guids == ["zzz-self", "aaa-peer", "mmm-peer"]


def test_master_and_host_flags_track_the_manager():
    mgr = _manager()
    _add_peer(mgr, "peer-a")
    mgr.master_guid = "peer-a"
    mgr.host_guid = "self-guid"
    mgr.is_host = True

    snap = session_state_snapshot(mgr)
    by_guid = {p["guid"]: p for p in snap["peers"]}

    assert snap["master_guid"] == "peer-a"
    assert snap["master_app"] == "xstudio"
    assert snap["is_host"] is True
    assert by_guid["peer-a"]["is_master"] is True
    assert by_guid["peer-a"]["is_host"] is False
    assert by_guid["self-guid"]["is_host"] is True
    assert by_guid["self-guid"]["is_master"] is False


def test_master_app_is_empty_before_election():
    snap = session_state_snapshot(_manager())

    assert snap["master_guid"] is None
    assert snap["master_app"] == ""


@pytest.mark.parametrize(
    "channel,field",
    [
        (authority.CHANNEL_POSITION, "holds_position_lease"),
        (authority.CHANNEL_DISPLAY, "holds_display_lease"),
        (authority.CHANNEL_STRUCTURE, "holds_structure_lease"),
    ],
)
def test_lease_ownership_lands_on_exactly_one_peer(channel, field):
    mgr = _manager()
    _add_peer(mgr, "peer-a")
    mgr._leases[channel].owner_guid = "peer-a"

    by_guid = {p["guid"]: p for p in session_state_snapshot(mgr)["peers"]}

    assert by_guid["peer-a"][field] is True
    assert by_guid["self-guid"][field] is False


def test_free_lease_is_held_by_nobody():
    mgr = _manager()
    _add_peer(mgr, "peer-a")

    for peer in session_state_snapshot(mgr)["peers"]:
        assert peer["holds_position_lease"] is False
        assert peer["holds_display_lease"] is False
        assert peer["holds_structure_lease"] is False


def test_role_is_the_session_role_not_the_host_flag():
    """``role`` carries driver/reviewer/viewer; host stays its own flag."""
    assert peer_role({"app": "xstudio", "capabilities": ["visibility"], "role": "reviewer"}) == "reviewer"
    assert peer_role({"app": "openrv", "capabilities": [], "role": "viewer"}) == "viewer"


def test_absent_role_resolves_to_the_session_default_not_the_strictest():
    # Unknown is never the restrictive value: a peer running older code must
    # not read as the most restricted participant in the session.
    assert peer_role({}) == authority.DRIVER
    assert peer_role({}, authority.VIEWER) == authority.VIEWER
    assert peer_role({"role": "nonsense"}, authority.VIEWER) == authority.DRIVER


def test_snapshot_reports_role_and_driverless_state():
    mgr = _manager()
    snap = session_state_snapshot(mgr)

    assert snap["self_role"] == authority.DRIVER
    assert snap["default_role"] == authority.DRIVER
    assert snap["driverless"] is False
    assert snap["peers"][0]["role"] == authority.DRIVER
    # Role is not host: a driver that is not the elected host is still a driver.
    assert snap["peers"][0]["is_host"] is False


def test_snapshot_reports_a_driverless_session():
    """The condition is reported, not merely inferable from a disabled menu."""
    mgr = _manager()
    mgr._default_role = authority.VIEWER
    mgr.resolve_own_role()

    snap = session_state_snapshot(mgr)

    assert snap["self_role"] == authority.VIEWER
    assert snap["driverless"] is True


def test_snapshot_role_agrees_with_host_eligibility():
    """One derivation: the panel cannot claim a driver the election cannot find."""
    mgr = _manager()
    mgr._default_role = authority.VIEWER
    mgr.resolve_own_role()
    _add_peer(mgr, "peer-a")
    mgr._peers["peer-a"]["role"] = authority.DRIVER

    assert session_state_snapshot(mgr)["driverless"] is False
    assert authority.elect_host_guid(mgr._peers, mgr.default_role) == "peer-a"


def test_peer_without_app_name_is_labelled_unknown():
    mgr = _manager()
    mgr._peers["peer-a"] = {"capabilities": [], "last_seen": 0.0}

    by_guid = {p["guid"]: p for p in session_state_snapshot(mgr)["peers"]}

    assert by_guid["peer-a"]["app"] == "Unknown"


def test_snapshot_does_not_alias_manager_state():
    """The snapshot is a projection, not a window onto live internals."""
    mgr = _manager()
    snap = session_state_snapshot(mgr)

    snap["peers"][0]["app"] = "mutated"
    snap["status"] = "mutated"

    assert mgr._peers["self-guid"]["app"] == "openrv"
    assert mgr.status == STATE_SYNCED
    assert session_state_snapshot(mgr)["peers"][0]["app"] == "openrv"


def test_snapshot_survives_a_json_round_trip():
    """xStudio ships this projection to QML as a JSON attribute value."""
    mgr = _manager()
    _add_peer(mgr, "peer-a")
    mgr.master_guid = "peer-a"
    mgr._leases[authority.CHANNEL_POSITION].owner_guid = "peer-a"

    snap = session_state_snapshot(mgr)

    assert json.loads(json.dumps(snap)) == snap


def test_snapshot_reports_no_join_confirmation_before_ever_joining():
    """A peer that never joined shows no outcome (session-state-ui)."""
    snap = session_state_snapshot(_manager())
    assert snap["join_confirmation"] is None


@pytest.mark.parametrize(
    "outcome,differences",
    [
        ("confirmed", []),
        ("mismatched", ["frame mismatch: expected ~10, got 999 (tolerance 5)"]),
        ("not_confirmed", []),
    ],
)
def test_snapshot_reports_the_join_confirmation_outcome(outcome, differences):
    mgr = _manager()
    mgr.join_confirmation = {"outcome": outcome, "differences": differences}

    snap = session_state_snapshot(mgr)

    assert snap["join_confirmation"] == {"outcome": outcome, "differences": differences}


def test_snapshot_reports_no_structure_divergence_when_synchronised():
    snap = session_state_snapshot(_manager())
    assert snap["structure_divergence"] is None


def test_snapshot_reports_recovering():
    mgr = _manager()
    mgr.structure_diverged = True
    mgr._recovery_unreachable = False

    snap = session_state_snapshot(mgr)

    assert snap["structure_divergence"] == "recovering"


def test_snapshot_reports_unrecoverable():
    mgr = _manager()
    mgr.structure_diverged = True
    mgr._recovery_unreachable = True

    snap = session_state_snapshot(mgr)

    assert snap["structure_divergence"] == "unrecoverable"


def test_snapshot_carries_the_shared_view():
    mgr = _manager()
    mgr.active_timeline_guid = "tl-1"
    mgr.selected_clip_guid = "clip-1"

    snap = session_state_snapshot(mgr)

    assert snap["active_timeline_guid"] == "tl-1"
    assert snap["selected_clip_guid"] == "clip-1"


def test_snapshot_derives_display_name_with_fallback():
    mgr = _manager()
    
    # 1. Full name
    mgr._peers["peer-name"] = {
        "app": "xstudio",
        "identity": {"user": "alice", "first_name": "Alice", "last_name": "Smith", "host": "host1", "source": "local"}
    }
    # 2. First name only
    mgr._peers["peer-first"] = {
        "app": "openrv",
        "identity": {"user": "bob", "first_name": "Bob", "host": "host2", "source": "local"}
    }
    # 3. Account only
    mgr._peers["peer-account"] = {
        "app": "openrv",
        "identity": {"user": "charlie", "host": "host3", "source": "local"}
    }
    # 4. Neither (no identity at all)
    mgr._peers["peer-none"] = {
        "app": "xstudio",
    }
    # 5. Empty identity (should act like no identity)
    mgr._peers["peer-empty"] = {
        "app": "openrv",
        "identity": {}
    }
    
    snap = session_state_snapshot(mgr)
    by_guid = {p["guid"]: p for p in snap["peers"]}
    
    # Verify fallback chain
    assert by_guid["peer-name"]["display_name"] == "Alice Smith"
    assert by_guid["peer-first"]["display_name"] == "Bob"
    assert by_guid["peer-account"]["display_name"] == "charlie"
    assert by_guid["peer-none"]["display_name"] == "xstudio"
    assert by_guid["peer-empty"]["display_name"] == "openrv"
    
    # Verify fields are exposed
    assert by_guid["peer-name"]["user"] == "alice"
    assert by_guid["peer-name"]["host"] == "host1"
    assert by_guid["peer-name"]["source"] == "local"
    
    assert by_guid["peer-none"]["user"] == ""
    assert by_guid["peer-none"]["host"] == ""
    assert by_guid["peer-none"]["source"] == ""


def test_snapshot_reports_visibility_holder():
    mgr = _manager()
    mgr._self_role = authority.DRIVER
    _add_peer(mgr, "peer-driver", capabilities=("visibility",))
    mgr._peers["peer-driver"]["role"] = authority.DRIVER
    _add_peer(mgr, "peer-viewer", capabilities=("visibility",))
    mgr._peers["peer-viewer"]["role"] = authority.VIEWER

    # 1. No lease claimed: elected host is the effective holder
    mgr.host_guid = "peer-driver"
    mgr.is_host = False

    snap = session_state_snapshot(mgr)
    by_guid = {p["guid"]: p for p in snap["peers"]}

    assert by_guid["peer-driver"]["holds_visibility"] is True
    assert by_guid["self-guid"]["holds_visibility"] is False
    assert by_guid["peer-viewer"]["holds_visibility"] is False
    assert snap["self_holds_visibility"] is False
    assert snap["may_hold_visibility"] is True

    # 2. Lease claimed by self
    mgr._leases[authority.CHANNEL_VISIBILITY].owner_guid = "self-guid"
    snap = session_state_snapshot(mgr)
    by_guid = {p["guid"]: p for p in snap["peers"]}

    assert by_guid["self-guid"]["holds_visibility"] is True
    assert by_guid["peer-driver"]["holds_visibility"] is False
    assert snap["self_holds_visibility"] is True

    # 3. Role validation check
    assert by_guid["peer-driver"]["role"] == authority.DRIVER
    assert by_guid["peer-viewer"]["role"] == authority.VIEWER


def test_snapshot_reports_a_role_that_may_never_hold_the_view():
    """A viewer is shown as unable to take the view, not merely as not holding it."""
    mgr = _manager()
    mgr._self_role = authority.VIEWER

    snap = session_state_snapshot(mgr)

    assert snap["may_hold_visibility"] is False
    assert snap["self_holds_visibility"] is False


def test_snapshot_does_not_report_an_expired_lease_as_held(monkeypatch):
    """Expiry is lazy, so the projection must settle it like every other reader.

    The panel is the surface whose whole job is to say who holds the view; a
    stale holder there is the failure this capability exists to remove, wearing
    a different hat.
    """
    import time as time_module

    now_mono = [100.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now_mono[0])

    mgr = _manager()
    _add_peer(mgr, "peer-driver")
    mgr.host_guid = "peer-driver"
    mgr.claim_category(authority.CHANNEL_VISIBILITY)
    assert session_state_snapshot(mgr)["self_holds_visibility"] is True

    now_mono[0] += authority.LEASE_DURATIONS[authority.CHANNEL_VISIBILITY] + 0.1
    snap = session_state_snapshot(mgr)

    assert snap["self_holds_visibility"] is False
    # Unclaimed falls back to the elected host, not to nobody.
    by_guid = {p["guid"]: p for p in snap["peers"]}
    assert by_guid["peer-driver"]["holds_visibility"] is True
