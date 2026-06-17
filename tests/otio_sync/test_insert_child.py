"""
Integration test for insert_child sync flow.

Simulates two SyncManager instances on the same machine:
  - sender calls insert_child → broadcasts delta
  - receiver polls and applies the patch → clip inserted into its local timeline
"""
import sys
import os
import time
import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import SyncManager

SYNC_DEMO_TRACK_UUID = "otio-sync-demo-track-0"

def make_timeline_with_known_track():
    timeline = otio.schema.Timeline("Sync Demo")
    track = otio.schema.Track("Main")
    if "sync" not in track.metadata:
        track.metadata["sync"] = {}
    track.metadata["sync"]["guid"] = SYNC_DEMO_TRACK_UUID
    timeline.tracks.append(track)
    return timeline, track

def run_test():
    from otio_sync_core.network import UDPNetwork
    sender_net = UDPNetwork(port=9996, self_guid="sender")
    receiver_net = UDPNetwork(port=9996, self_guid="receiver")
    sender   = SyncManager(session_id="test-session", self_guid="sender", network=sender_net)
    receiver = SyncManager(session_id="test-session", self_guid="receiver", network=receiver_net)

    # Both instances bootstrap identical scaffold
    sender_timeline,   sender_track   = make_timeline_with_known_track()
    receiver_timeline, receiver_track = make_timeline_with_known_track()
    sender.register_timeline(sender_timeline)
    receiver.register_timeline(receiver_timeline)

    assert SYNC_DEMO_TRACK_UUID in sender._object_map,   "sender track not registered"
    assert SYNC_DEMO_TRACK_UUID in receiver._object_map, "receiver track not registered"
    print("✓ Both tracks registered with well-known UUID")

    # Sender adds a clip
    clip = otio.schema.Clip(
        name="test_clip.mov",
        media_reference=otio.schema.ExternalReference(target_url="/path/to/test_clip.mov")
    )
    sender.insert_child(SYNC_DEMO_TRACK_UUID, clip)
    print(f"  Sender track children: {len(sender_track)}")
    assert len(sender_track) == 1, "clip not inserted on sender side"
    print("✓ Clip inserted into sender's timeline")

    time.sleep(0.15)

    # Receiver polls
    results = receiver.receive_and_apply_all()
    print(f"  Results received: {results}")
    assert len(results) == 1, f"expected 1 result, got {len(results)}"
    action, received_clip = results[0]
    assert action == "insert_child", f"expected insert_child, got {action}"
    assert len(receiver_track) == 1, "clip not inserted on receiver side"
    print("✓ Clip received and inserted into receiver's timeline")

    ref = received_clip.media_reference
    assert isinstance(ref, otio.schema.ExternalReference), "wrong reference type"
    assert ref.target_url == "/path/to/test_clip.mov", f"wrong URL: {ref.target_url}"
    print(f"✓ Media reference preserved: {ref.target_url}")

    sender.close()
    receiver.close()
    print("\nSUCCESS — insert_child round-trip works!")

if __name__ == "__main__":
    run_test()
