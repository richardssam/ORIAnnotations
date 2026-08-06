"""Tests for the sender-side check on structural broadcasts.

A receiving peer cannot tell an orphaned patch from one that merely arrived
early — both look like a parent GUID it does not hold. The sender can: it knows
exactly what it announced. These pin that asymmetry, which is why this check
exists in addition to the receive-side record in ``test_structure_identity``.

The check is report-only for now (tasks.md 5.1), so every test here asserts the
message still goes out.
"""

import os
import sys

import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

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


def _manager(guid="peer", master=False):
    mgr = SyncManager(session_id="s", self_guid=guid, network=FakeNetwork())
    mgr.status = STATE_SYNCED
    mgr.is_master = master
    return mgr


def _timeline(manager, name="Default Sequence"):
    tl = otio.schema.Timeline(name)
    tl.tracks = otio.schema.Stack("tracks")
    tl.tracks.append(otio.schema.Track("Media"))
    tl.metadata.setdefault("sync", {})["guid"] = manager._derive_guid(f"seq:{name}")
    for track in tl.tracks:
        track.metadata.setdefault("sync", {})["guid"] = manager._derive_guid(
            f"track:{name}:{track.name}"
        )
    manager.register_timeline(tl)
    return tl


def _media_track(tl):
    return next(t for t in tl.tracks if t.name == "Media")


def _guid(obj):
    return obj.metadata["sync"]["guid"]


def _inserts(manager):
    return [
        e for e in manager.network.sent
        if e["payload"]["command"]["event"] == pm.InsertChild.EVENT
    ]


def test_broadcasting_into_an_unannounced_parent_is_reported():
    """The defect this change exists for: the parent is in the local map and
    was simply never announced, so only a record of what was sent can catch it."""
    mgr = _manager()
    tl = _timeline(mgr)

    mgr.insert_child(_guid(_media_track(tl)), otio.schema.Clip(name="car.mov"))

    assert mgr.unpublished_parent_count == 1
    assert "INSERT_CHILD" in mgr.unpublished_parents[-1]


def test_the_patch_is_still_sent_while_the_guard_is_report_only():
    mgr = _manager()
    tl = _timeline(mgr)

    mgr.insert_child(_guid(_media_track(tl)), otio.schema.Clip(name="car.mov"))

    assert len(_inserts(mgr)) == 1, "report-only must not suppress the message"


def test_announcing_the_timeline_first_reports_nothing():
    mgr = _manager()
    tl = _timeline(mgr)
    mgr.broadcast_add_timeline(_guid(tl))

    mgr.insert_child(_guid(_media_track(tl)), otio.schema.Clip(name="car.mov"))

    assert mgr.unpublished_parents == []
    assert mgr.unpublished_parent_count == 0


def test_a_peer_may_address_structure_another_peer_published():
    """Receiving structure makes it common knowledge, so a follower inserting
    into a track it never announced itself is legitimate, not a fault."""
    publisher, follower = _manager("a"), _manager("b")
    tl = _timeline(publisher)
    publisher.broadcast_add_timeline(_guid(tl))
    announce = next(
        e for e in publisher.network.sent
        if e["payload"]["command"]["event"] == pm.AddTimeline.EVENT
    )
    follower.apply_patch(announce)

    follower.insert_child(_guid(_media_track(tl)), otio.schema.Clip(name="car.mov"))

    assert follower.unpublished_parents == []


def test_a_snapshot_publishes_every_timeline_it_carries():
    master = _manager("m", master=True)
    tl = _timeline(master)
    master.send_state_snapshot("joiner")

    master.insert_child(_guid(_media_track(tl)), otio.schema.Clip(name="car.mov"))

    assert master.unpublished_parents == []


def test_a_joiner_may_address_structure_the_snapshot_gave_it():
    master, joiner = _manager("m", master=True), _manager("j")
    tl = _timeline(master)
    master.send_state_snapshot("j")
    snapshot = next(
        e for e in master.network.sent
        if e["payload"]["command"]["event"] == pm.StateSnapshot.EVENT
    )
    joiner.apply_snapshot(snapshot["payload"]["command"]["payload"])

    joiner.insert_child(_guid(_media_track(tl)), otio.schema.Clip(name="car.mov"))

    assert joiner.unpublished_parents == []


def test_a_received_insert_makes_the_child_addressable():
    """Nesting: a clip a peer inserted may then be inserted into."""
    publisher, follower = _manager("a"), _manager("b")
    tl = _timeline(publisher)
    publisher.broadcast_add_timeline(_guid(tl))
    for env in list(publisher.network.sent):
        follower.apply_patch(env)
    stack = otio.schema.Stack(name="nested")
    publisher.insert_child(_guid(_media_track(tl)), stack)
    follower.apply_patch(_inserts(publisher)[-1])

    follower.insert_child(_guid(stack), otio.schema.Clip(name="car.mov"))

    assert follower.unpublished_parents == []


def test_the_report_is_bounded_but_counts_everything():
    mgr = _manager()
    tl = _timeline(mgr)
    track = _guid(_media_track(tl))
    for i in range(25):
        mgr.insert_child(track, otio.schema.Clip(name=f"clip{i}.mov"))

    assert mgr.unpublished_parent_count == 25
    assert len(mgr.unpublished_parents) == 10


def test_the_report_reaches_the_harness_via_export_state():
    mgr = _manager()
    tl = _timeline(mgr)
    mgr.insert_child(_guid(_media_track(tl)), otio.schema.Clip(name="car.mov"))

    payload = mgr.export_state()

    assert payload["unpublished_parent_count"] == 1
    assert len(payload["unpublished_parents"]) == 1


def test_rebuilding_local_state_does_not_unpublish_what_was_announced():
    """``reset_timelines`` rebuilds local state; it does not un-send messages.
    Clearing the record there would fire on exactly the master re-initialisation
    this change was chasing."""
    mgr = _manager()
    tl = _timeline(mgr)
    mgr.broadcast_add_timeline(_guid(tl))

    mgr.reset_timelines()
    _timeline(mgr)
    mgr.insert_child(_guid(_media_track(tl)), otio.schema.Clip(name="car.mov"))

    assert mgr.unpublished_parents == []
