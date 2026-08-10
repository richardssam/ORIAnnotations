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


def test_role_follows_visibility_capability():
    assert peer_role({"app": "xstudio", "capabilities": ["visibility"]}) == "Host"
    assert peer_role({"app": "openrv", "capabilities": []}) == "Client"
    assert peer_role({}) == "Client"


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


def test_snapshot_carries_the_shared_view():
    mgr = _manager()
    mgr.active_timeline_guid = "tl-1"
    mgr.selected_clip_guid = "clip-1"

    snap = session_state_snapshot(mgr)

    assert snap["active_timeline_guid"] == "tl-1"
    assert snap["selected_clip_guid"] == "clip-1"
