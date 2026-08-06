"""Tests that a rebuilt timeline keeps the identity peers already hold.

OpenRV re-initialises a sequence's tracks while the session is live — it re-runs
the init after the first media add — constructing fresh ``Track`` objects each
time. If those get new sync GUIDs, every ``INSERT_CHILD`` broadcast afterwards
names a track no peer holds, and the patches are dropped on arrival without a
word. These pin the identity, and the consequence of losing it.
"""

import os
import sys

import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../rvplugin/ori_sync')))

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


def _derive_track_guids(manager, seq_name, timeline):
    """Mirror of ``sequence_sync._derive_track_guids``.

    Duplicated rather than imported: ``sequence_sync`` imports ``rv.commands``
    at module scope and cannot be loaded outside OpenRV. The helper is four
    lines, and what these tests protect is the *rule* — a rebuild produces the
    same GUIDs — which is checked here and enforced in the plugin by the same
    derivation key.
    """
    for track in timeline.tracks:
        track.metadata.setdefault("sync", {})["guid"] = manager._derive_guid(
            f"rv_track:{seq_name}:{track.name}"
        )


def _build_timeline(manager, seq_name, derive=True):
    """Build a native RV-style timeline the way the plugin's init does."""
    timeline = otio.schema.Timeline(seq_name)
    timeline.tracks = otio.schema.Stack("tracks")
    timeline.tracks.append(otio.schema.Track("Media"))
    timeline.tracks.append(otio.schema.Track("Annotations"))
    if derive:
        _derive_track_guids(manager, seq_name, timeline)
    timeline.metadata.setdefault("sync", {})["guid"] = manager._derive_guid(
        f"rv_sequence:{seq_name}"
    )
    manager.register_timeline(timeline)
    return timeline


def _media_track_guid(timeline):
    for track in timeline.tracks:
        if track.name == "Media":
            return track.metadata["sync"]["guid"]
    return None


def _manager(guid="peer"):
    mgr = SyncManager(session_id="s", self_guid=guid, network=FakeNetwork())
    mgr.status = STATE_SYNCED
    return mgr


def test_rebuild_keeps_the_media_track_guid():
    """The defect, directly: re-init used to mint a new track GUID."""
    mgr = _manager()

    first = _media_track_guid(_build_timeline(mgr, "Default Sequence"))
    rebuilt = _media_track_guid(_build_timeline(mgr, "Default Sequence"))

    assert first == rebuilt


def test_rebuild_keeps_every_track_guid():
    mgr = _manager()

    before = [t.metadata["sync"]["guid"] for t in _build_timeline(mgr, "Seq").tracks]
    after = [t.metadata["sync"]["guid"] for t in _build_timeline(mgr, "Seq").tracks]

    assert before == after


def test_two_peers_agree_on_track_identity():
    """Same reason the timeline GUID is derived: peers that each auto-create the
    same sequence must converge on one identity, not hold two random ones."""
    a, b = _manager("peer-a"), _manager("peer-b")

    ga = _media_track_guid(_build_timeline(a, "Default Sequence"))
    gb = _media_track_guid(_build_timeline(b, "Default Sequence"))

    assert ga == gb


def test_different_sequences_get_different_tracks():
    mgr = _manager()

    one = _media_track_guid(_build_timeline(mgr, "Sequence 1"))
    two = _media_track_guid(_build_timeline(mgr, "Sequence 2"))

    assert one != two


def test_media_and_annotation_tracks_do_not_collide():
    mgr = _manager()
    tl = _build_timeline(mgr, "Seq")

    guids = [t.metadata["sync"]["guid"] for t in tl.tracks]

    assert len(set(guids)) == len(guids)


def test_a_peer_applies_inserts_addressed_to_a_rebuilt_track():
    """The end-to-end consequence, asserted on a real peer.

    A sender rebuilds its timeline and then broadcasts an insertion. With a
    stable track GUID the receiver resolves the parent and applies it; this is
    the exact step that failed seven times in a row in openrv_hosts_selection.
    """
    sender, receiver = _manager("sender"), _manager("receiver")
    _build_timeline(receiver, "Default Sequence")

    _build_timeline(sender, "Default Sequence")
    rebuilt = _build_timeline(sender, "Default Sequence")
    track_guid = _media_track_guid(rebuilt)

    clip = otio.schema.Clip(name="car.mov")
    clip.media_reference = otio.schema.ExternalReference(target_url="/tmp/car.mov")
    sender.insert_child(track_guid, clip)

    inserts = [
        e for e in sender.network.sent
        if e["payload"]["command"]["event"] == pm.InsertChild.EVENT
    ]
    assert len(inserts) == 1
    assert receiver.apply_patch(inserts[0]) is not None, (
        "receiver could not resolve the parent track — the orphaned-patch defect"
    )


def test_an_unresolvable_insert_is_recorded():
    """The drop used to be silent, which is what let eight of them pass unnoticed."""
    receiver = _manager("receiver")
    sender = _manager("sender")
    rebuilt = _build_timeline(sender, "Default Sequence", derive=False)
    sender.insert_child(_media_track_guid(rebuilt), otio.schema.Clip(name="car.mov"))
    envelope = next(
        e for e in sender.network.sent
        if e["payload"]["command"]["event"] == pm.InsertChild.EVENT
    )

    receiver.apply_patch(envelope)

    assert receiver.unresolved_patch_count == 1
    assert "INSERT_CHILD" in receiver.unresolved_patches[-1]


def test_a_healthy_apply_records_nothing():
    sender, receiver = _manager("sender"), _manager("receiver")
    _build_timeline(receiver, "Default Sequence")
    tl = _build_timeline(sender, "Default Sequence")
    sender.insert_child(_media_track_guid(tl), otio.schema.Clip(name="car.mov"))
    envelope = next(
        e for e in sender.network.sent
        if e["payload"]["command"]["event"] == pm.InsertChild.EVENT
    )

    receiver.apply_patch(envelope)

    assert receiver.unresolved_patches == []
    assert receiver.unresolved_patch_count == 0


def test_unresolved_record_is_bounded_but_counts_everything():
    """A record, not a replay queue — it must not grow without limit."""
    receiver = _manager("receiver")
    sender = _manager("sender")
    tl = _build_timeline(sender, "Default Sequence", derive=False)
    track = _media_track_guid(tl)
    for i in range(25):
        sender.network.sent.clear()
        sender.insert_child(track, otio.schema.Clip(name=f"clip{i}.mov"))
        envelope = next(
            e for e in sender.network.sent
            if e["payload"]["command"]["event"] == pm.InsertChild.EVENT
        )
        receiver.apply_patch(envelope)

    assert receiver.unresolved_patch_count == 25
    assert len(receiver.unresolved_patches) == 10


def test_unresolved_patches_reach_the_harness_via_export_state():
    receiver = _manager("receiver")
    sender = _manager("sender")
    tl = _build_timeline(sender, "Default Sequence", derive=False)
    sender.insert_child(_media_track_guid(tl), otio.schema.Clip(name="car.mov"))
    envelope = next(
        e for e in sender.network.sent
        if e["payload"]["command"]["event"] == pm.InsertChild.EVENT
    )
    receiver.apply_patch(envelope)

    payload = receiver.export_state()

    assert payload["unresolved_patch_count"] == 1
    assert len(payload["unresolved_patches"]) == 1


def test_without_derivation_the_insert_is_orphaned():
    """Pins the failure mode, so a regression reads as this defect returning."""
    sender, receiver = _manager("sender"), _manager("receiver")
    _build_timeline(receiver, "Default Sequence", derive=False)

    rebuilt = _build_timeline(sender, "Default Sequence", derive=False)
    clip = otio.schema.Clip(name="car.mov")
    sender.insert_child(_media_track_guid(rebuilt), clip)

    inserts = [
        e for e in sender.network.sent
        if e["payload"]["command"]["event"] == pm.InsertChild.EVENT
    ]
    assert receiver.apply_patch(inserts[0]) is None
