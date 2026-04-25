import pika
import json
import sys

def main(session_id='otio-sync-demo'):
    exchange_name = f"sync_session_{session_id}"
    
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host='localhost')
        )
        channel = connection.channel()

        # Ensure exchange exists
        channel.exchange_declare(exchange=exchange_name, exchange_type='fanout')

        # Temporary queue for this listener
        result = channel.queue_declare(queue='', exclusive=True)
        queue_name = result.method.queue
        channel.queue_bind(exchange=exchange_name, queue=queue_name)

        print(f"[*] Listening for sync events on session: {session_id}")
        print(f"[*] Exchange: {exchange_name}")
        print(" [!] To exit press CTRL+C")

        def callback(ch, method, properties, body):
            try:
                payload = json.loads(body.decode('utf-8'))
                print("-" * 50)
                print(f"EVENT: {payload.get('command')} {payload.get('event')}")
                print(f"FROM:  {payload.get('source_guid')}")
                print(f"DATA:  {json.dumps(payload.get('payload'), indent=2)}")
            except Exception as e:
                print(f"[Error] Failed to parse message: {e}")
                print(f"RAW BODY: {body}")

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
