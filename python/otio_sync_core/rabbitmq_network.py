"""RabbitMQ fanout-exchange network backend for OTIO Sync."""

from __future__ import annotations

import json
import logging as _logging
import queue
import threading
import uuid
from typing import Any

import pika

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

    Two dedicated background threads handle I/O so callers are never blocked
    by pika:

    * ``_consumer_thread`` — owns a ``BlockingConnection`` for receiving;
      pushes decoded payloads onto ``_incoming_queue``.
    * ``_publisher_thread`` — owns a separate ``BlockingConnection`` for
      sending; drains ``_send_queue`` in a tight loop with automatic
      reconnection on failure.

    ``send_payload`` therefore never touches a socket directly; it is always
    non-blocking for the caller.

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
        host: str = '127.0.0.1',
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
        self._send_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        self._stop_event = threading.Event()
        # Set once the consumer queue is bound and basic_consume is registered.
        # Callers should wait on this before broadcasting WHO_IS_MASTER so that
        # the I_AM_MASTER response is not published before the queue exists.
        self._consumer_ready = threading.Event()
        self._consumer_thread = threading.Thread(
            target=self._run_consumer, daemon=True
        )
        self._consumer_thread.start()
        self._publisher_thread = threading.Thread(
            target=self._run_publisher, daemon=True, name="rmq_publisher"
        )
        self._publisher_thread.start()

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
                self._consumer_ready.set()

                while not self._stop_event.is_set():
                    connection.process_data_events(time_limit=1)

                connection.close()
            except Exception as e:
                if not self._stop_event.is_set():
                    _log(f"Consumer error: {e}. Retrying in 5s...")
                    self._stop_event.wait(5)

    def _run_publisher(self) -> None:
        """Background publisher loop with automatic reconnection.

        Owns its own ``BlockingConnection`` so publishing never blocks the
        poll thread.  Drains ``_send_queue`` as fast as the broker accepts
        messages, reconnecting with a 5-second delay on failure.

        Exits cleanly when :attr:`_stop_event` is set and the queue is empty.
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
                _log(f"Publisher connected to {self.host}:{self.port}")

                while not self._stop_event.is_set():
                    try:
                        data = self._send_queue.get(timeout=0.1)
                    except queue.Empty:
                        # Keep the connection alive while idle.
                        connection.process_data_events(time_limit=0)
                        continue
                    channel.basic_publish(
                        exchange=self.exchange_name,
                        routing_key='',
                        body=data,
                    )

                connection.close()
            except Exception as e:
                if not self._stop_event.is_set():
                    _log(f"Publisher error: {e}. Retrying in 5s...")
                    self._stop_event.wait(5)

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Block until the consumer queue is bound and ready to receive messages.

        :param timeout: Maximum seconds to wait before returning False.
        :returns: True if the consumer became ready within *timeout*, else False.
        :rtype: bool
        """
        return self._consumer_ready.wait(timeout=timeout)

    def send_payload(self, payload: dict[str, Any]) -> None:
        """Enqueue *payload* for publishing to the fanout exchange.

        Non-blocking: the actual socket write happens on the publisher thread.
        Injects ``source_guid`` into the payload if not already present.

        :param payload: Message envelope to broadcast.
        """
        if "source_guid" not in payload:
            payload["source_guid"] = self.self_guid
        _log(
            f"\n=== MQ SEND [{self.exchange_name}] ===\n"
            f"{json.dumps(payload, indent=2)}\n"
        )
        self._send_queue.put(json.dumps(payload).encode('utf-8'))

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
        """Signal background threads to exit and wait for them to finish.

        Blocks for up to 2 seconds per thread.
        """
        self._stop_event.set()
        if self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=2)
        if self._publisher_thread.is_alive():
            self._publisher_thread.join(timeout=2)
