import sys
import os
import time
import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import SyncManager
from otio_sync_core.network import UDPNetwork

def run_test():
    sender = SyncManager(session_id="test", self_guid="sender_1", port=9997)
    receiver_net = UDPNetwork(port=9997, self_guid="receiver_1")

    # Create dummy timeline
    timeline = otio.schema.Timeline("My Timeline")
    clip = otio.schema.Clip("My Clip")
    track = otio.schema.Track()
    track.append(clip)
    timeline.tracks.append(track)
    
    # Register returns a proxy
    sync_timeline = sender.register_timeline(timeline)
    
    # Use proxy as normal OTIO object
    sync_timeline.name = "Synced Timeline"
    
    time.sleep(0.1)
    
    payloads = receiver_net.receive_payloads()
    print(f"Received {len(payloads)} payloads.")
    
    success = False
    if len(payloads) > 0:
        print(f"Payload data: {payloads[0]}")
        if payloads[0]["payload"]["value"] == "Synced Timeline":
            print("SUCCESS")
            success = True
            
    sender.close()
    receiver_net.close()
    
    if not success:
        print("FAILED")

if __name__ == "__main__":
    run_test()
