"""Transparent proxy that intercepts attribute writes and forwards them to SyncManager."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import opentimelineio as otio

if TYPE_CHECKING:
    from .manager import SyncManager


class OTIOSyncProxy:
    """Transparent proxy around an OTIO :class:`~opentimelineio.core.SerializableObject`.

    Attribute reads are passed through to the wrapped object unchanged.  Attribute
    writes are applied to the wrapped object **and** forwarded to the
    :class:`~otio_sync_core.manager.SyncManager` as a ``set_property`` broadcast,
    so that remote peers stay in sync without the caller needing to be aware of the
    sync layer.

    Child OTIO objects returned by ``__getattr__`` are themselves wrapped in a new
    :class:`OTIOSyncProxy` so that nested attribute writes are also captured.

    :param obj: The OTIO object to wrap.
    :param manager: The :class:`~otio_sync_core.manager.SyncManager` that owns this
        object.
    :param parent_path: Dot-separated path prefix used when constructing the property
        path for nested objects.
    """

    def __init__(
        self,
        obj: otio.core.SerializableObject,
        manager: SyncManager,
        parent_path: str = "",
    ) -> None:
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_manager", manager)
        object.__setattr__(self, "_path", parent_path)

    @property
    def __class__(self) -> type:  # type: ignore[override]
        return self._obj.__class__

    def __getattr__(self, name: str) -> Any:
        """Return the attribute from the wrapped object.

        If the value is itself a :class:`~opentimelineio.core.SerializableObject`
        it is wrapped in a new :class:`OTIOSyncProxy` so that nested writes are
        also intercepted.

        :param name: Attribute name.
        :returns: Attribute value, proxied if it is an OTIO object.
        """
        val = getattr(self._obj, name)
        if isinstance(val, otio.core.SerializableObject):
            return OTIOSyncProxy(val, self._manager, "")
        return val

    def __setattr__(self, name: str, value: Any) -> None:
        """Write *value* to the wrapped object and broadcast the change.

        Private proxy attributes (``_obj``, ``_manager``, ``_path``) are stored
        directly on the proxy instance and are never forwarded.

        :param name: Attribute name.
        :param value: New value.
        """
        if name in ("_obj", "_manager", "_path"):
            object.__setattr__(self, name, value)
            return

        setattr(self._obj, name, value)

        guid: str | None = None
        if isinstance(self._obj, otio.core.SerializableObject):
            if "sync" in self._obj.metadata and "guid" in self._obj.metadata["sync"]:
                guid = self._obj.metadata["sync"]["guid"]

        if guid:
            path = name if not self._path else f"{self._path}/{name}"
            self._manager.set_property(guid, path, value)

    def __repr__(self) -> str:
        return repr(self._obj)

    def __str__(self) -> str:
        return str(self._obj)
