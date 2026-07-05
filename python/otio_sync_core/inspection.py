"""Process-global registry so an in-process inspector can reach the live manager.

The sync_test OpenRV inspector runs **inside** the RV process alongside the sync
plugin, but as a separate module with no direct reference to the plugin's
:class:`~otio_sync_core.manager.SyncManager`.  The plugin registers its manager
here on startup; the inspector hook fetches it to expose the client's reduced
state (``manager.export_state()``) for structural validation.

This is intentionally a simple module-global: both the plugin and the inspector
import the same ``otio_sync_core`` within one process, so they share it.  It is
**not** a cross-process mechanism (remote inspectors must use their own bridge).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager import SyncManager

_REGISTERED_MANAGER: "SyncManager | None" = None
_REGISTERED_ANNOTATION_CONTROLLER: Any = None


def register_manager(manager: "SyncManager") -> None:
    """Register the live manager so an in-process inspector can read its state.

    :param manager: The active :class:`SyncManager` for this peer.
    """
    global _REGISTERED_MANAGER
    _REGISTERED_MANAGER = manager


def get_registered_manager() -> "SyncManager | None":
    """Return the registered manager, or ``None`` if none has registered yet."""
    return _REGISTERED_MANAGER


def register_annotation_controller(controller: Any) -> None:
    """Register the live ``AnnotationSyncController`` so an in-process caller
    (the ``sync_test`` OpenRV hook) can trigger the real annotation send path.

    :param controller: The active ``AnnotationSyncController`` for this peer.
    """
    global _REGISTERED_ANNOTATION_CONTROLLER
    _REGISTERED_ANNOTATION_CONTROLLER = controller


def get_registered_annotation_controller() -> Any:
    """Return the registered annotation controller, or ``None`` if unregistered."""
    return _REGISTERED_ANNOTATION_CONTROLLER
