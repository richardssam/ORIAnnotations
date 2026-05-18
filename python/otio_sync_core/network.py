"""UDP broadcast network backend and shared network Protocol for OTIO Sync."""

from __future__ import annotations

import json
import socket
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SyncNetworkProtocol(Protocol):
    """Structural interface that all network backends must satisfy.

    Both :class:`UDPNetwork` and :class:`~otio_sync_core.rabbitmq_network.RabbitMQNetwork`
    conform to this protocol, allowing :class:`~otio_sync_core.manager.SyncManager` to
    accept either without a concrete base class.
    """

    def send_payload(self, payload: dict[str, Any]) -> None:
        """Broadcast *payload* to all peers in the session."""
        ...

    def receive_payloads(self) -> list[dict[str, Any]]:
        """Return all payloads received since the last call, without blocking."""
        ...

    def stop(self) -> None:
        """Shut down the network connection and release resources."""
        ...


def get_local_broadcast() -> str:
    """Derive the LAN broadcast address from the default route interface.

    Falls back to ``255.255.255.255`` if the address cannot be determined.

    :returns: Broadcast IP address string, e.g. ``"192.168.1.255"``.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        parts = ip.split('.')
        parts[-1] = '255'
        return '.'.join(parts)
    except Exception:
        return '255.255.255.255'


class UDPNetwork:
    """LAN broadcast network backend using UDP.

    Opens a non-blocking receive socket bound to *port* and a separate send
    socket with ``SO_BROADCAST`` set.  All peers on the same LAN segment that
    bind to the same port will receive every message.

    Self-filtering is done via *self_guid*: any received payload whose
    ``source_guid`` matches is silently discarded.

    :param port: UDP port to bind and broadcast on.
    :param broadcast_ip: Explicit broadcast address; auto-detected when ``None``.
    :param self_guid: GUID of the local peer used to filter own messages.
    """

    def __init__(
        self,
        port: int = 9999,
        broadcast_ip: str | None = None,
        self_guid: str | None = None,
    ) -> None:
        self.port = port
        self.broadcast_ip = broadcast_ip or get_local_broadcast()
        self.self_guid = self_guid

        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        self.recv_sock.bind(('', self.port))
        self.recv_sock.setblocking(False)

    def send_payload(self, payload: dict[str, Any]) -> None:
        """Broadcast *payload* as JSON to the LAN.

        Injects ``source_guid`` into the payload if not already present.

        :param payload: Message envelope to broadcast.
        """
        try:
            if self.self_guid and "source_guid" not in payload:
                payload["source_guid"] = self.self_guid
            data = json.dumps(payload).encode('utf-8')
            self.send_sock.sendto(data, (self.broadcast_ip, self.port))
        except Exception as e:
            print(f"Failed to send payload: {e}")

    def receive_payloads(self) -> list[dict[str, Any]]:
        """Drain all available UDP datagrams and return them as parsed dicts.

        Non-blocking; returns an empty list when no data is waiting.  Own
        messages (matched by ``source_guid``) are silently dropped.

        :returns: List of received payload dicts.
        """
        payloads = []
        while True:
            try:
                data, _ = self.recv_sock.recvfrom(65535)
                try:
                    payload = json.loads(data.decode('utf-8'))
                    if self.self_guid and payload.get("source_guid") == self.self_guid:
                        continue
                    payloads.append(payload)
                except json.JSONDecodeError:
                    pass
            except BlockingIOError:
                break
            except Exception as e:
                print(f"Error receiving payload: {e}")
                break
        return payloads

    def close(self) -> None:
        """Close both sockets immediately."""
        self.send_sock.close()
        self.recv_sock.close()

    def stop(self) -> None:
        """Alias for :meth:`close`; satisfies :class:`SyncNetworkProtocol`."""
        self.close()
