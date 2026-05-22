import json
import sys
import uuid

import opentimelineio as otio
import pika


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

        print("[*] Requesting state snapshot from master...")

        channel.basic_publish(
            exchange=exchange_name,
            routing_key='',
            body=json.dumps({
                "command": "SESSION",
                "event": "WHO_IS_MASTER",
                "session_id": session_id,
                "source_guid": self_guid,
                "payload": {"requester_guid": self_guid},
            }).encode('utf-8'),
        )

        def callback(ch, method, properties, body):
            try:
                msg = json.loads(body.decode('utf-8'))
                if msg.get("source_guid") == self_guid:
                    return

                cmd = msg.get("command")
                event = msg.get("event")
                data = msg.get("payload", {})

                if cmd == "SESSION" and event == "I_AM_MASTER":
                    master_guid = data.get("master_guid")
                    print(f"[*] Found master: {master_guid}. Requesting state...")
                    channel.basic_publish(
                        exchange=exchange_name,
                        routing_key='',
                        body=json.dumps({
                            "command": "SESSION",
                            "event": "STATE_REQUEST",
                            "session_id": session_id,
                            "source_guid": self_guid,
                            "payload": {
                                "target_guid": master_guid,
                                "requester_guid": self_guid,
                            },
                        }).encode('utf-8'),
                    )

                elif cmd == "SESSION" and event == "STATE_SNAPSHOT":
                    if data.get("target_guid") != self_guid:
                        return

                    timelines = data.get("timelines", {})
                    print(f"[*] Received snapshot: {len(timelines)} timeline(s)")

                    saved = []
                    for tl_guid, tl_dict in timelines.items():
                        tl = otio.adapters.read_from_string(
                            json.dumps(tl_dict), "otio_json"
                        )
                        safe_name = (tl.name or tl_guid[:8]).replace("/", "_")
                        filename = f"snapshot_{safe_name}.otio"
                        otio.adapters.write_to_file(tl, filename)
                        saved.append((filename, tl))

                        tracks = list(tl.tracks)
                        print(f"    {tl_guid[:8]}  {tl.name!r}: {len(tracks)} track(s)")
                        for track in tracks:
                            children = list(track)
                            print(f"      {track.name!r}: {len(children)} child(ren)")

                    print(f"[*] Saved: {[f for f, _ in saved]}")
                    sys.exit(0)

            except Exception as e:
                print(f"[Error] {e}")

        channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=True)
        channel.start_consuming()

    except KeyboardInterrupt:
        print("\n[*] Stopping.")
        sys.exit(0)
    except Exception as e:
        print(f"[Error] Connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    session = sys.argv[1] if len(sys.argv) > 1 else 'otio-sync-demo'
    main(session)
