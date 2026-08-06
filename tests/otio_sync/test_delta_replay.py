"""Tests for delta-buffer replay after a snapshot.

Messages that arrive while a peer is joining are buffered and replayed once the
snapshot lands, but only those newer than the snapshot — the older ones are
already contained in it. That comparison read ``sync_timestamp`` one level too
high in the envelope, so it was always ``0`` and *every* buffered delta was
discarded: a peer joining mid-edit silently lost every change made while it was
joining.
"""

import os
import sys
import time

import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import (  # noqa: E402
    SyncManager,
    STATE_JOINING,
    STATE_SYNCED,
)
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


SNAPSHOT_TIME = 1000.0


def _joining_manager():
    mgr = SyncManager(session_id="s", self_guid="joiner", network=FakeNetwork())
    mgr.status = STATE_JOINING
    return mgr


def _timeline_with_track(tl_guid="tl-1", track_guid="track-1"):
    tl = otio.schema.Timeline("Seq")
    tl.metadata["sync"] = {"guid": tl_guid}
    tl.tracks = otio.schema.Stack("tracks")
    track = otio.schema.Track("Media")
    track.metadata["sync"] = {"guid": track_guid}
    tl.tracks.append(track)
    return tl


def _snapshot(tl):
    return pm.StateSnapshot(
        target_guid="joiner",
        timelines={tl.metadata["sync"]["guid"]: tl},
        active_timeline_guid=tl.metadata["sync"]["guid"],
        snapshot_timestamp=SNAPSHOT_TIME,
    ).to_payload()


def _insert_envelope(sync_timestamp, name="late.mov"):
    clip = otio.schema.Clip(name=name)
    msg = pm.InsertChild(
        parent_uuid="track-1",
        index=-1,
        child_data=clip,
        sync_timestamp=sync_timestamp,
    )
    return {
        "session": "s",
        "source_guid": "other-peer",
        "payload": {
            "command_schema": msg.SCHEMA,
            "command": {"event": msg.EVENT, "payload": msg.to_payload()},
        },
    }


def test_a_delta_newer_than_the_snapshot_is_replayed():
    mgr = _joining_manager()
    mgr.apply_patch(_insert_envelope(SNAPSHOT_TIME + 5))
    assert len(mgr._delta_buffer) == 1, "message should have been buffered"

    results = mgr.apply_snapshot(_snapshot(_timeline_with_track()))

    assert any(action == "insert_child" for action, _ in results)
    assert mgr.status == STATE_SYNCED


def test_a_delta_older_than_the_snapshot_is_discarded():
    """The snapshot already contains it; replaying would duplicate the child."""
    mgr = _joining_manager()
    mgr.apply_patch(_insert_envelope(SNAPSHOT_TIME - 5))

    results = mgr.apply_snapshot(_snapshot(_timeline_with_track()))

    assert not any(action == "insert_child" for action, _ in results)


def test_the_replayed_child_actually_lands_in_the_timeline():
    """Asserted on the model, not just the returned action."""
    mgr = _joining_manager()
    mgr.apply_patch(_insert_envelope(SNAPSHOT_TIME + 5, name="late.mov"))

    mgr.apply_snapshot(_snapshot(_timeline_with_track()))

    track = mgr._object_map["track-1"]
    assert [c.name for c in track] == ["late.mov"]


def test_a_message_without_a_timestamp_is_treated_as_older():
    """Absent is not 'newer than everything' — the old bug's failure direction."""
    mgr = _joining_manager()
    envelope = _insert_envelope(SNAPSHOT_TIME + 5)
    del envelope["payload"]["command"]["payload"]["sync_timestamp"]
    mgr.apply_patch(envelope)

    results = mgr.apply_snapshot(_snapshot(_timeline_with_track()))

    assert not any(action == "insert_child" for action, _ in results)


def test_replay_terminates_with_several_buffered_deltas():
    """Replay must not re-buffer onto the list it is iterating.

    ``apply_patch`` buffers every non-session message while the status is
    ``STATE_JOINING``, and the status is still ``STATE_JOINING`` during replay.
    Without a guard, each replayed message is appended back onto the list being
    iterated and the loop never terminates. This was unreachable while the
    timestamp comparison always failed, so it appeared the moment that was
    fixed — a hang on any peer joining mid-edit, strictly worse than the
    silent discard it replaced.
    """
    mgr = _joining_manager()
    for i in range(5):
        mgr.apply_patch(_insert_envelope(SNAPSHOT_TIME + i + 1, name=f"c{i}.mov"))
    assert len(mgr._delta_buffer) == 5

    results = mgr.apply_snapshot(_snapshot(_timeline_with_track()))

    assert len([a for a, _ in results if a == "insert_child"]) == 5
    assert mgr._delta_buffer == []
    assert [c.name for c in mgr._object_map["track-1"]] == [
        "c0.mov", "c1.mov", "c2.mov", "c3.mov", "c4.mov"
    ]


def test_buffering_resumes_after_a_replay():
    """The guard is scoped to the replay, not left latched on."""
    mgr = _joining_manager()
    mgr.apply_snapshot(_snapshot(_timeline_with_track()))
    assert mgr.status == STATE_SYNCED

    mgr.status = STATE_JOINING
    mgr.apply_patch(_insert_envelope(SNAPSHOT_TIME + 1))

    assert len(mgr._delta_buffer) == 1


def test_timestamp_is_read_from_the_level_it_is_written_to():
    """Pins the bug directly: the field lives in command.payload."""
    envelope = _insert_envelope(1234.5)

    assert SyncManager._payload_sync_timestamp(envelope) == 1234.5
    assert "sync_timestamp" not in envelope["payload"], (
        "if this ever becomes true, the old top-level read would have worked "
        "and this test no longer pins anything"
    )


def test_real_broadcast_envelopes_carry_a_readable_timestamp():
    """Guards the envelope shape end-to-end, via an actual broadcast."""
    sender = SyncManager(session_id="s", self_guid="sender", network=FakeNetwork())
    sender.status = STATE_SYNCED
    tl = _timeline_with_track()
    sender.register_timeline(tl)

    sender.insert_child("track-1", otio.schema.Clip(name="car.mov"))

    envelope = next(
        e for e in sender.network.sent
        if e["payload"]["command"]["event"] == pm.InsertChild.EVENT
    )
    assert SyncManager._payload_sync_timestamp(envelope) > 0
    assert SyncManager._payload_sync_timestamp(envelope) <= time.time()
