"""OTIO Sync core library.

Provides :class:`SyncManager` for coordinating OTIO timeline synchronisation
across a network session, along with UDP and RabbitMQ network backends and a
transparent proxy for intercepting attribute writes.
"""

# Core sync API — imported best-effort so the leaf annotation utilities
# (``coords``, ``shapes``, ``rv_annotation_codec``, ``rv_paint_applier``) can be
# imported without the full network/pika stack. The offline testchart batch and
# the annotations-only rvpkg vendor neither pika nor the sync modules, so an
# eager import here would break ``from otio_sync_core import coords`` for them.
# When the sync deps ARE present (live sync plugins) these names resolve as before.
try:
    from . import color
    from .manager import SyncManager, sync_event_schema
    from .network import SyncNetworkProtocol, UDPNetwork
    from .rabbitmq_network import RabbitMQNetwork
    from .proxy import OTIOSyncProxy
except ImportError:  # pragma: no cover - exercised on annotations-only installs
    pass

# State projection / inspection support the sync_test framework, not core sync.
# Import them best-effort so a deployment that omits these modules (e.g. an
# rvpkg whose file list is out of date) degrades the test feature rather than
# killing the whole plugin's import — and therefore all live sync.
try:
    from .state_projection import project_state, diff_states, normalize_clip_name
    from .inspection import register_manager, get_registered_manager
except ImportError:  # pragma: no cover - exercised only on incomplete installs
    pass
