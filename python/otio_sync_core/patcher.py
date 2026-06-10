"""Transport-agnostic patching engine for OpenTimelineIO (OTIO) graphs."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable

import opentimelineio as otio

from .protocol_messages import (
    ProtocolMessage,
    InsertChild,
    MoveChild,
    RemoveChild,
    SetProperty,
    ReplaceAnnotationCommands,
)

_logger = logging.getLogger("otio_sync")


def _log(msg: str) -> None:
    if _logger.handlers:
        _logger.debug(msg)


def _otio_to_dict(obj: otio.core.SerializableObject) -> dict[str, Any]:
    return json.loads(otio.adapters.write_to_string(obj, "otio_json", indent=-1))


def _dict_to_otio(d: dict[str, Any]) -> otio.core.SerializableObject:
    return otio.adapters.read_from_string(json.dumps(d), "otio_json")


class OTIOPatcher:
    """Manages the lifecycle of OTIO graph patches.

    Tracks object GUIDs, observes mutations, and applies patch events (such as
    property changes or hierarchy insertions/moves/removals) to the local graph.
    """

    def __init__(self) -> None:
        self.object_map: dict[str, otio.core.SerializableObject] = {}
        self._is_syncing: bool = False
        self._property_callbacks: list[Callable[[str, str, Any], None]] = []
        self._hierarchy_callbacks: list[Callable[[str, str, str], None]] = []

    def on_property_changed(
        self, callback: Callable[[str, str, Any], None]
    ) -> Callable[[str, str, Any], None]:
        """Register a callback for property change events.

        :param callback: Callable receiving ``(target_uuid, path, new_value)``.
        :returns: The *callback* unchanged (decorator-compatible).
        :rtype: Callable
        """
        self._property_callbacks.append(callback)
        return callback

    def on_hierarchy_changed(
        self, callback: Callable[[str, str, str], None]
    ) -> Callable[[str, str, str], None]:
        """Register a callback for hierarchy change events.

        :param callback: Callable receiving ``(parent_uuid, action, child_uuid)``
            where *action* is one of ``"insert_child"``, ``"remove_child"``, or ``"move_child"``.
        :returns: The *callback* unchanged (decorator-compatible).
        :rtype: Callable
        """
        self._hierarchy_callbacks.append(callback)
        return callback

    def _fire_property_changed(self, target_uuid: str, path: str, value: Any) -> None:
        for cb in self._property_callbacks:
            try:
                cb(target_uuid, path, value)
            except Exception as e:
                _log(f"on_property_changed callback error: {e}")

    def _fire_hierarchy_changed(
        self, parent_uuid: str, action: str, child_uuid: str
    ) -> None:
        for cb in self._hierarchy_callbacks:
            try:
                cb(parent_uuid, action, child_uuid)
            except Exception as e:
                _log(f"on_hierarchy_changed callback error: {e}")

    def traverse_and_map(self, item: otio.core.SerializableObject) -> None:
        """Recursively assign GUIDs to all OTIO objects under *item* and index them.

        :param item: Root OTIO object to traverse.
        """
        def _walk(node: otio.core.SerializableObject):
            yield node
            if hasattr(node, "tracks"):
                stack = node.tracks
                yield stack
                for child in stack:
                    yield from _walk(child)
            elif hasattr(node, "__iter__") and not isinstance(node, str):
                for child in node:
                    yield from _walk(child)

        for obj in _walk(item):
            self.ensure_guid_and_map(obj)

    def traverse_and_map_preserve(self, item: otio.core.SerializableObject) -> None:
        """Recursively assign GUIDs to all OTIO objects under *item* without overwriting existing entries.

        :param item: Root OTIO object to traverse.
        """
        def _walk(node: otio.core.SerializableObject):
            yield node
            if hasattr(node, "tracks"):
                stack = node.tracks
                yield stack
                for child in stack:
                    yield from _walk(child)
            elif hasattr(node, "__iter__") and not isinstance(node, str):
                for child in node:
                    yield from _walk(child)

        for obj in _walk(item):
            if not isinstance(obj, otio.core.SerializableObject):
                continue
            if "sync" not in obj.metadata:
                obj.metadata["sync"] = {}
            if "guid" not in obj.metadata["sync"]:
                obj.metadata["sync"]["guid"] = str(uuid.uuid4())
            guid = obj.metadata["sync"]["guid"]
            self.object_map.setdefault(guid, obj)

    def ensure_guid_and_map(self, obj: Any) -> None:
        """Assign a sync GUID to *obj* if absent, then add it to ``object_map``.

        Non-:class:`~opentimelineio.core.SerializableObject` values are ignored.

        :param obj: Candidate OTIO object.
        """
        if not isinstance(obj, otio.core.SerializableObject):
            return
        if "sync" not in obj.metadata:
            obj.metadata["sync"] = {}
        if "guid" not in obj.metadata["sync"]:
            obj.metadata["sync"]["guid"] = str(uuid.uuid4())
        self.object_map[obj.metadata["sync"]["guid"]] = obj

    @staticmethod
    def _find_annotation_clip_at(
        track: otio.schema.Track,
        clip_guid: str,
        frame: int,
    ) -> otio.schema.Clip | None:
        for child in track:
            if not isinstance(child, otio.schema.Clip):
                continue
            if child.metadata.get("clip_guid") != clip_guid:
                continue
            sr = getattr(child, "source_range", None)
            if sr is not None and int(sr.start_time.value) == frame:
                return child
        return None

    @staticmethod
    def _try_merge_annotation(
        parent: otio.schema.Track,
        child_obj: otio.core.SerializableObject,
    ) -> otio.schema.Clip | None:
        if not isinstance(parent, otio.schema.Track):
            return None
        if not hasattr(child_obj, "metadata"):
            return None
        incoming_cmds = child_obj.metadata.get("annotation_commands")
        incoming_cg = child_obj.metadata.get("clip_guid")
        incoming_sr = getattr(child_obj, "source_range", None)
        if not incoming_cmds or not incoming_cg or incoming_sr is None:
            return None
        incoming_frame = int(incoming_sr.start_time.value)
        existing = OTIOPatcher._find_annotation_clip_at(
            parent, incoming_cg, incoming_frame
        )
        if existing is None:
            return None
        existing.metadata["annotation_commands"].extend(incoming_cmds)
        return existing

    def set_property(self, target_uuid: str, path: str, value: Any) -> "ProtocolMessage | None":
        """Set property *path* to *value* on object *target_uuid* locally.

        :param target_uuid: GUID of the target object.
        :param path: Target property or metadata sub-key path (e.g. ``"name"`` or ``"metadata/custom"``).
        :param value: New value; must be a primitive type.
        :returns: The generated patch payload, or ``None`` if *target_uuid* is not found.
        :rtype: dict or None
        """
        if target_uuid not in self.object_map:
            return None

        obj = self.object_map[target_uuid]

        if path.startswith("metadata/"):
            parts = path.split("/")
            curr = obj.metadata
            for part in parts[1:-1]:
                if part not in curr:
                    curr[part] = {}
                curr = curr[part]
            curr[parts[-1]] = value
        else:
            setattr(obj, path, value)

        self._fire_property_changed(target_uuid, path, value)

        return SetProperty(
            target_uuid=target_uuid,
            path=path,
            value=value,
            sync_timestamp=time.time(),
        )

    def insert_child(
        self,
        parent_uuid: str,
        child_obj: otio.core.SerializableObject,
        index: int = -1,
    ) -> "ProtocolMessage | None":
        """Insert *child_obj* into the parent container locally.

        :param parent_uuid: GUID of the parent container.
        :param child_obj: The OTIO object to insert.
        :param index: Position at which to insert; ``-1`` appends.
        :returns: The generated patch payload, or ``None`` if *parent_uuid* is not found.
        :rtype: dict or None
        """
        if parent_uuid not in self.object_map:
            return None

        parent = self.object_map[parent_uuid]
        self.ensure_guid_and_map(child_obj)

        if index == -1:
            parent.append(child_obj)
        else:
            parent.insert(index, child_obj)

        child_uuid = child_obj.metadata["sync"]["guid"]
        self._fire_hierarchy_changed(parent_uuid, "insert_child", child_uuid)

        return InsertChild(
            parent_uuid=parent_uuid,
            index=index,
            child_data=_otio_to_dict(child_obj),
            sync_timestamp=time.time(),
        )

    def remove_child(self, parent_uuid: str, child_uuid: str) -> "ProtocolMessage | None":
        """Remove *child_uuid* from its parent container locally.

        :param parent_uuid: GUID of the parent container.
        :param child_uuid: GUID of the child to remove.
        :returns: The generated patch payload, or ``None`` if parent or child is not found.
        :rtype: dict or None
        """
        if parent_uuid not in self.object_map:
            return None
        parent = self.object_map[parent_uuid]

        current_index = next(
            (i for i, item in enumerate(parent)
             if item.metadata.get("sync", {}).get("guid") == child_uuid),
            None,
        )
        if current_index is None:
            return None

        del parent[current_index]
        self.object_map.pop(child_uuid, None)

        self._fire_hierarchy_changed(parent_uuid, "remove_child", child_uuid)

        return RemoveChild(
            parent_uuid=parent_uuid,
            child_uuid=child_uuid,
            sync_timestamp=time.time(),
        )

    def move_child(self, parent_uuid: str, child_uuid: str, to_index: int) -> "ProtocolMessage | None":
        """Move *child_uuid* within its parent container locally.

        :param parent_uuid: GUID of the parent container.
        :param child_uuid: GUID of the child to move.
        :param to_index: Target position in the parent's child list.
        :returns: The generated patch payload, or ``None`` if parent/child is not found or index is unchanged.
        :rtype: dict or None
        """
        if parent_uuid not in self.object_map:
            return None
        parent = self.object_map[parent_uuid]

        current_index = next(
            (i for i, item in enumerate(parent)
             if item.metadata.get("sync", {}).get("guid") == child_uuid),
            None,
        )
        if current_index is None or current_index == to_index:
            return None

        child = parent[current_index]
        del parent[current_index]
        parent.insert(to_index, child)

        self._fire_hierarchy_changed(parent_uuid, "move_child", child_uuid)

        return MoveChild(
            parent_uuid=parent_uuid,
            child_uuid=child_uuid,
            to_index=to_index,
            sync_timestamp=time.time(),
        )

    def apply_patch(self, msg: "ProtocolMessage") -> tuple[str, Any] | None:
        """Apply an OTIO-session mutation message to the local graph.

        Dispatches on the concrete message type, so the same class that built
        the payload (in :meth:`set_property`, :meth:`insert_child`, etc.) is the
        one used to consume it.

        :param msg: A reconstructed OTIO-session :class:`ProtocolMessage`:
            :class:`SetProperty`, :class:`MoveChild`, :class:`RemoveChild`,
            :class:`ReplaceAnnotationCommands`, or :class:`InsertChild`.
        :returns: An ``(action_name, action_data)`` tuple when the caller needs
            to act, or ``None``.
        :rtype: tuple or None
        """
        self._is_syncing = True
        try:
            if isinstance(msg, SetProperty):
                target_uuid = msg.target_uuid
                if target_uuid in self.object_map:
                    obj = self.object_map[target_uuid]
                    path: str = msg.path
                    value: Any = msg.value
                    if path.startswith("metadata/"):
                        parts = path.split("/")
                        curr = obj.metadata
                        for part in parts[1:-1]:
                            if part not in curr:
                                curr[part] = {}
                            curr = curr[part]
                        curr[parts[-1]] = value
                    else:
                        setattr(obj, path, value)
                    self._fire_property_changed(target_uuid, path, value)
                    return ("set_property", obj)

            elif isinstance(msg, MoveChild):
                parent_uuid: str = msg.parent_uuid
                child_uuid: str = msg.child_uuid
                to_index: int = msg.to_index
                parent = self.object_map.get(parent_uuid)
                child = self.object_map.get(child_uuid)
                if parent is not None and child is not None:
                    current_index = next(
                        (i for i, item in enumerate(parent)
                         if item.metadata.get("sync", {}).get("guid") == child_uuid),
                        None,
                    )
                    if current_index is not None:
                        del parent[current_index]
                        parent.insert(to_index, child)
                        self._fire_hierarchy_changed(parent_uuid, "move_child", child_uuid)
                        return ("move_child", msg.to_payload())

            elif isinstance(msg, RemoveChild):
                parent_uuid = msg.parent_uuid
                child_uuid = msg.child_uuid
                parent = self.object_map.get(parent_uuid)
                if parent is not None:
                    current_index = next(
                        (i for i, item in enumerate(parent)
                         if item.metadata.get("sync", {}).get("guid") == child_uuid),
                        None,
                    )
                    if current_index is not None:
                        del parent[current_index]
                        self.object_map.pop(child_uuid, None)
                        self._fire_hierarchy_changed(parent_uuid, "remove_child", child_uuid)
                        return ("remove_child", msg.to_payload())

            elif isinstance(msg, ReplaceAnnotationCommands):
                ann_clip_guid = msg.annotation_clip_guid
                clip = self.object_map.get(ann_clip_guid)
                if clip is None:
                    _log(f"REPLACE_ANNOTATION_COMMANDS: clip {ann_clip_guid} not found")
                    return None
                commands: list[otio.core.SerializableObject] = []
                for cmd_dict in msg.commands:
                    try:
                        commands.append(
                            _dict_to_otio(cmd_dict) if isinstance(cmd_dict, dict) else cmd_dict
                        )
                    except Exception as exc:
                        _log(f"REPLACE_ANNOTATION_COMMANDS: failed to deserialise: {exc}")
                clip.metadata["annotation_commands"] = commands
                return ("annotation_commands_replaced", clip)

            elif isinstance(msg, InsertChild):
                parent_uuid = msg.parent_uuid
                if parent_uuid in self.object_map:
                    parent = self.object_map[parent_uuid]
                    index: int = msg.index
                    child_obj = _dict_to_otio(msg.child_data)
                    merged = self._try_merge_annotation(parent, child_obj)
                    if merged is not None:
                        self.ensure_guid_and_map(child_obj)
                        return ("annotation_commands_added", (merged, child_obj))
                    if index == -1:
                        parent.append(child_obj)
                    else:
                        parent.insert(index, child_obj)
                    self.ensure_guid_and_map(child_obj)
                    child_uuid = child_obj.metadata["sync"]["guid"]
                    self._fire_hierarchy_changed(parent_uuid, "insert_child", child_uuid)
                    return ("insert_child", child_obj)
        finally:
            self._is_syncing = False

        return None
