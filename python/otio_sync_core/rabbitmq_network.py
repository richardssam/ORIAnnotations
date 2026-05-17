import pika
import json
import logging as _logging
import threading
import queue
import uuid
import opentimelineio as otio

_logger = _logging.getLogger("otio_sync")


def _log(msg):
    if _logger.handlers:
        _logger.debug(msg)

class RabbitMQNetwork:
    """
    RabbitMQ-based network layer for OTIO Sync.
    Uses a fanout exchange for session broadcasting.
    """
    def __init__(self, host='localhost', port=5672, session_id='otio-sync-default', self_guid=None):
        self.host = host
        self.port = port
        self.session_id = session_id
        self.self_guid = self_guid or str(uuid.uuid4())
        
        self.exchange_name = f"sync_session_{session_id}"
        self._incoming_queue = queue.Queue()
        
        # Connection for sending (lazy-init in main thread or on-demand)
        self._send_conn = None
        self._send_channel = None
        
        # Background consumer thread
        self._stop_event = threading.Event()
        self._consumer_thread = threading.Thread(target=self._run_consumer, daemon=True)
        self._consumer_thread.start()

    def _run_consumer(self):
        """Background thread to listen for incoming messages, with reconnect on failure."""
        while not self._stop_event.is_set():
            try:
                connection = pika.BlockingConnection(
                    pika.ConnectionParameters(host=self.host, port=self.port)
                )
                channel = connection.channel()

                channel.exchange_declare(exchange=self.exchange_name, exchange_type='fanout')

                result = channel.queue_declare(queue='', exclusive=True)
                queue_name = result.method.queue
                channel.queue_bind(exchange=self.exchange_name, queue=queue_name)

                def callback(ch, method, properties, body):
                    try:
                        payload = json.loads(body.decode('utf-8'))
                        if payload.get("source_guid") == self.self_guid:
                            return
                        _log(f"\n=== MQ RECV [{self.exchange_name}] ===\n{json.dumps(payload, indent=2)}\n")
                        self._incoming_queue.put(payload)
                    except Exception as e:
                        _log(f"Error processing message: {e}")

                channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=True)

                _log(f"Connected to {self.host}:{self.port}, listening on {self.exchange_name}")

                while not self._stop_event.is_set():
                    connection.process_data_events(time_limit=1)

                connection.close()
            except Exception as e:
                if not self._stop_event.is_set():
                    _log(f"Consumer error: {e}. Retrying in 5s...")
                    self._stop_event.wait(5)

    def _get_send_channel(self):
        """Lazy-init sending channel."""
        if self._send_channel is None or self._send_conn.is_closed:
            self._send_conn = pika.BlockingConnection(
                pika.ConnectionParameters(host=self.host, port=self.port)
            )
            self._send_channel = self._send_conn.channel()
            self._send_channel.exchange_declare(exchange=self.exchange_name, exchange_type='fanout')
        return self._send_channel

    def send_payload(self, payload):
        """Broadcast a payload to the session."""
        try:
            if "source_guid" not in payload:
                payload["source_guid"] = self.self_guid

            _log(f"\n=== MQ SEND [{self.exchange_name}] ===\n{json.dumps(payload, indent=2)}\n")
            data = json.dumps(payload).encode('utf-8')

            channel = self._get_send_channel()
            channel.basic_publish(
                exchange=self.exchange_name,
                routing_key='',
                body=data
            )
        except Exception as e:
            _log(f"Send error: {e}")

    def receive_payloads(self):
        """Fetch all queued payloads since last poll."""
        payloads = []
        while not self._incoming_queue.empty():
            try:
                payloads.append(self._incoming_queue.get_nowait())
            except queue.Empty:
                break
        return payloads

    def stop(self):
        """Cleanup."""
        self._stop_event.set()
        if self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=2)
        if self._send_conn and self._send_conn.is_open:
            self._send_conn.close()
