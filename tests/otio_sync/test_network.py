import sys
import os
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.network import UDPNetwork

def run_test():
    sender = UDPNetwork(port=9998, self_guid="sender_1")
    receiver = UDPNetwork(port=9998, self_guid="receiver_1")

    test_payload = {
        "message_type": "otio_delta",
        "action": "set_property",
        "target_uuid": "1234",
        "path": "name",
        "value": "Test Name"
    }

    sender.send_payload(test_payload)
    
    time.sleep(0.1)
    
    payloads = receiver.receive_payloads()
    print(f"Received {len(payloads)} payloads.")
    if len(payloads) > 0:
        print(f"Payload data: {payloads[0]}")
        if payloads[0]["value"] == "Test Name":
            print("SUCCESS")
            return
    print("FAILED")

if __name__ == "__main__":
    run_test()
