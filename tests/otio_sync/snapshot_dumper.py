import pika
import json
import sys
import uuid

def main(session_id='otio-sync-demo'):
    exchange_name = f"sync_session_{session_id}"
    self_guid = str(uuid.uuid4())
    
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host='localhost')
        )
        channel = connection.channel()
        channel.exchange_declare(exchange=exchange_name, exchange_type='fanout')
        result = channel.queue_declare(queue='', exclusive=True)
        queue_name = result.method.queue
        channel.queue_bind(exchange=exchange_name, queue=queue_name)

        print(f"[*] Requesting state snapshot from master...")
        
        # Send WHO_IS_MASTER
        payload = {
            "command": "SESSION",
            "event": "WHO_IS_MASTER",
            "session_id": session_id,
            "source_guid": self_guid,
            "payload": {"requester_guid": self_guid}
        }
        channel.basic_publish(exchange=exchange_name, routing_key='', body=json.dumps(payload).encode('utf-8'))

        def callback(ch, method, properties, body):
            try:
                payload = json.loads(body.decode('utf-8'))
                if payload.get("source_guid") == self_guid: return
                
                cmd = payload.get("command")
                event = payload.get("event")
                data = payload.get("payload", {})
                
                if cmd == "SESSION" and event == "I_AM_MASTER":
                    master_guid = data.get("master_guid")
                    print(f"[*] Found master: {master_guid}. Requesting state...")
                    req = {
                        "command": "SESSION",
                        "event": "STATE_REQUEST",
                        "session_id": session_id,
                        "source_guid": self_guid,
                        "payload": {"target_guid": master_guid, "requester_guid": self_guid}
                    }
                    channel.basic_publish(exchange=exchange_name, routing_key='', body=json.dumps(req).encode('utf-8'))
                
                elif cmd == "SESSION" and event == "STATE_SNAPSHOT":
                    if data.get("target_guid") == self_guid:
                        print("[*] Received snapshot! Saving to snapshot.otio...")
                        with open("snapshot.otio", "w") as f:
                            f.write(data.get("otio_json", "{}"))
                        print("[*] Done. Exiting.")
                        sys.exit(0)
                        
            except Exception as e:
                print(f"[Error] Failed to parse message: {e}")

        channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=True)
        channel.start_consuming()

    except KeyboardInterrupt:
        print("\n[*] Stopping listener...")
        sys.exit(0)
    except Exception as e:
        print(f"[Error] Connection failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    session = sys.argv[1] if len(sys.argv) > 1 else 'otio-sync-demo'
    main(session)
