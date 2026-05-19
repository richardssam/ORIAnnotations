"""OTIO Sync core library.

Provides :class:`SyncManager` for coordinating OTIO timeline synchronisation
across a network session, along with UDP and RabbitMQ network backends and a
transparent proxy for intercepting attribute writes.
"""

from .manager import SyncManager
from .network import SyncNetworkProtocol, UDPNetwork
from .rabbitmq_network import RabbitMQNetwork
from .proxy import OTIOSyncProxy
