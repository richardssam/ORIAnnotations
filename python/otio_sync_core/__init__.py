"""OTIO Sync core library.

Provides :class:`SyncManager` for coordinating OTIO timeline synchronisation
across a network session, along with UDP and RabbitMQ network backends and a
transparent proxy for intercepting attribute writes.
"""

from . import color
from .manager import SyncManager, sync_event_schema
from .network import SyncNetworkProtocol, UDPNetwork
from .rabbitmq_network import RabbitMQNetwork
from .proxy import OTIOSyncProxy

# State projection / inspection support the sync_test framework, not core sync.
# Import them best-effort so a deployment that omits these modules (e.g. an
# rvpkg whose file list is out of date) degrades the test feature rather than
# killing the whole plugin's import — and therefore all live sync.
try:
    from .state_projection import project_state, diff_states, normalize_clip_name
    from .inspection import register_manager, get_registered_manager
except ImportError:  # pragma: no cover - exercised only on incomplete installs
    pass
