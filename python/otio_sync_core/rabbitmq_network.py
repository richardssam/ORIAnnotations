"""RabbitMQ fanout-exchange network backend for OTIO Sync."""

from __future__ import annotations

import json
import logging as _logging
import queue
import threading
import uuid
from typing import Any

import pika
import pika.adapters.blocking_connection

_logger = _logging.getLogger("otio_sync")


def _log(msg: str) -> None:
    if _logger.handlers:
        _logger.debug(msg)


class RabbitMQNetwork:
    """RabbitMQ network backend for OTIO Sync.

    Uses a **fanout exchange** so that every peer bound to the same exchange
    receives every published message.  The exchange name is derived from
    *session_id*, which implicitly scopes peers to a session without any
    server-side configuration.

    A dedicated background thread runs the blocking pika consumer with
    automatic reconnection on failure.  A separate lazy-initialised send
    channel is used from the calling (main) thread.

    Self-filtering is applied in the consumer callback: any message whose
    ``source_guid`` matches *self_guid* is silently discarded before being
    enqueued.

    :param host: RabbitMQ broker hostname or IP.
    :param port: RabbitMQ broker AMQP port.
    :param session_id: Logical session name; used to derive the exchange name.
    :param self_guid: GUID of the local peer used to filter own messages.
        Auto-generated if not provided.
    """

    def __init__(
        self,
        host: str = 'localhost',
        port: int = 5672,
        session_id: str = 'otio-sync-default',
        self_guid: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.session_id = session_id
        self.self_guid = self_guid or str(uuid.uuid4())

        self.exchange_name = f"sync_session_{session_id}"
        self._incoming_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        self._send_conn: pika.BlockingConnection | None = None
        self._send_channel: pika.adapters.blocking_connection.BlockingChannel | None = None

        self._stop_event = threading.Event()
        self._consumer_thread = threading.Thread(
            target=self._run_consumer, daemon=True
        )
        self._consumer_thread.start()

    def _run_consumer(self) -> None:
        """Background consumer loop with automatic reconnection.

        Blocks on ``process_data_events`` in a tight loop, reconnecting with a
        5-second delay whenever the broker connection drops.  Exits cleanly when
        :attr:`_stop_event` is set.
        """
        while not self._stop_event.is_set():
            try:
                connection = pika.BlockingConnection(
                    pika.ConnectionParameters(host=self.host, port=self.port)
                )
                channel = connection.channel()
                channel.exchange_declare(
                    exchange=self.exchange_name, exchange_type='fanout'
                )

                result = channel.queue_declare(queue='', exclusive=True)
                queue_name = result.method.queue
                channel.queue_bind(exchange=self.exchange_name, queue=queue_name)

                def callback(
                    ch: Any,
                    method: Any,
                    properties: Any,
                    body: bytes,
                ) -> None:
                    try:
                        payload = json.loads(body.decode('utf-8'))
                        if payload.get("source_guid") == self.self_guid:
                            return
                        _log(
                            f"\n=== MQ RECV [{self.exchange_name}] ===\n"
                            f"{json.dumps(payload, indent=2)}\n"
                        )
                        self._incoming_queue.put(payload)
                    except Exception as e:
                        _log(f"Error processing message: {e}")

                channel.basic_consume(
                    queue=queue_name,
                    on_message_callback=callback,
                    auto_ack=True,
                )
                _log(
                    f"Connected to {self.host}:{self.port}, "
                    f"listening on {self.exchange_name}"
                )

                while not self._stop_event.is_set():
                    connection.process_data_events(time_limit=1)

                connection.close()
            except Exception as e:
                if not self._stop_event.is_set():
                    _log(f"Consumer error: {e}. Retrying in 5s...")
                    self._stop_event.wait(5)

    def _get_send_channel(
        self,
    ) -> pika.adapters.blocking_connection.BlockingChannel:
        """Return the send channel, (re-)creating the connection if needed.

        :returns: A ready-to-use blocking channel connected to the fanout exchange.
        """
        if self._send_channel is None or self._send_conn.is_closed:
            self._send_conn = pika.BlockingConnection(
                pika.ConnectionParameters(host=self.host, port=self.port)
            )
            self._send_channel = self._send_conn.channel()
            self._send_channel.exchange_declare(
                exchange=self.exchange_name, exchange_type='fanout'
            )
        return self._send_channel

    def send_payload(self, payload: dict[str, Any]) -> None:
        """Publish *payload* as JSON to the fanout exchange.

        Injects ``source_guid`` into the payload if not already present.

        :param payload: Message envelope to broadcast.
        """
        try:
            if "source_guid" not in payload:
                payload["source_guid"] = self.self_guid
            _log(
                f"\n=== MQ SEND [{self.exchange_name}] ===\n"
                f"{json.dumps(payload, indent=2)}\n"
            )
            data = json.dumps(payload).encode('utf-8')
            channel = self._get_send_channel()
            channel.basic_publish(
                exchange=self.exchange_name,
                routing_key='',
                body=data,
            )
        except Exception as e:
            _log(f"Send error: {e}")

    def receive_payloads(self) -> list[dict[str, Any]]:
        """Drain the internal queue and return all pending payloads.

        Non-blocking; returns an empty list when nothing is waiting.  Messages
        are populated by the background consumer thread.

        :returns: List of received payload dicts.
        """
        payloads: list[dict[str, Any]] = []
        while not self._incoming_queue.empty():
            try:
                payloads.append(self._incoming_queue.get_nowait())
            except queue.Empty:
                break
        return payloads

    def stop(self) -> None:
        """Signal the consumer thread to exit and close all connections.

        Blocks for up to 2 seconds waiting for the consumer thread to finish.
        """
        self._stop_event.set()
        if self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=2)
        if self._send_conn and self._send_conn.is_open:
            self._send_conn.close()
