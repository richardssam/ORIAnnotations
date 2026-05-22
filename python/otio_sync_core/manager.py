"""Core synchronisation manager for the OTIO Sync protocol.

:class:`SyncManager` maintains a GUID-indexed map of every OTIO object in the shared
session and coordinates mutations across a pluggable network layer.  It implements the
master-election handshake, delta buffering during join, and all broadcast helpers
defined in the OTIO Sync Protocol v1 proposal.
"""

from __future__ import annotations

import json
import logging as _logging
import time
import uuid
from typing import Any, Callable

import opentimelineio as otio

from .network import SyncNetworkProtocol
from .proxy import OTIOSyncProxy

_logger = _logging.getLogger("otio_sync")


def _log(msg: str) -> None:
    if _logger.handlers:
        _logger.debug(msg)


def _otio_to_dict(obj: otio.core.SerializableObject) -> dict[str, Any]:
    return json.loads(otio.adapters.write_to_string(obj, "otio_json", indent=-1))


def _dict_to_otio(d: dict[str, Any]) -> otio.core.SerializableObject:
    return otio.adapters.read_from_string(json.dumps(d), "otio_json")


#: Session has not yet started.
STATE_NONE = "NONE"
#: Broadcasting ``WHO_IS_MASTER``; waiting for a response.
STATE_DISCOVERING = "DISCOVERING"
#: Master found; waiting for a full state snapshot.
STATE_JOINING = "JOINING"
#: Snapshot received and applied; fully participating in the session.
STATE_SYNCED = "SYNCED"


class SyncManager:
    """Coordinates OTIO object synchronisation across a network session.

    The manager maintains two complementary data structures:

    * ``_object_map`` — a flat ``{guid: otio_object}`` index for O(1) lookup by GUID.
    * ``_timelines`` — a ``{guid: Timeline}`` map of every registered top-level timeline.

    All mutations (inserts, removals, property changes) are applied locally **and**
    broadcast to peers via the injected *network* backend.  Incoming messages are
    applied through :meth:`apply_patch`, which also fires registered observer callbacks
    so that the host application (e.g. the RV plugin) can react to remote changes.

    **Session lifecycle**

    1. Call :meth:`start_session` — status transitions to ``STATE_DISCOVERING``.
    2. The caller polls :meth:`receive_and_apply_all` until a ``master_found`` action
       is returned, then calls :meth:`request_state`.
    3. Status transitions to ``STATE_JOINING``; incoming non-session messages are
       buffered in ``_delta_buffer``.
    4. When a ``state_snapshot_received`` action is returned, the caller invokes
       :meth:`apply_snapshot` which applies the full state and replays buffered deltas
       before transitioning to ``STATE_SYNCED``.

    If no master responds within the discovery timeout (implemented in the caller),
    the caller elects itself master and calls :meth:`broadcast_master_response`.

    :param session_id: Logical session identifier; scopes all network messages.
    :param self_guid: Stable GUID for this peer; auto-generated when not provided.
    :param network: Network backend satisfying :class:`~otio_sync_core.network.SyncNetworkProtocol`.
        May be set or replaced after construction.
    """

    def __init__(
        self,
        session_id: str = "default_session",
        self_guid: str | None = None,
        network: SyncNetworkProtocol | None = None,
    ) -> None:
        self.session_id = session_id
        self.self_guid: str = self_guid or str(uuid.uuid4())
        self.network: SyncNetworkProtocol | None = network

        self._object_map: dict[str, otio.core.SerializableObject] = {}
        self._timelines: dict[str, otio.schema.Timeline] = {}
        self.active_timeline_guid: str | None = None
        self._is_syncing: bool = False

        self._property_callbacks: list[Callable[[str, str, Any], None]] = []
        self._hierarchy_callbacks: list[Callable[[str, str, str], None]] = []
        self._status_callbacks: list[Callable[[str], None]] = []
        self._playback_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._synced_callbacks: list[Callable[[], None]] = []

        self.status: str = STATE_NONE
        self.is_master: bool = False
        self.master_guid: str | None = None
        self._delta_buffer: list[dict[str, Any]] = []
        self._last_snapshot_time: float = 0

        #: Last received playback state dict; empty until the first playback message.
        self.playback_state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def root_timeline(self) -> otio.schema.Timeline | None:
        """The active timeline, or the first registered timeline when none is active.

        :returns: Active :class:`~opentimelineio.schema.Timeline`, or ``None`` if no
            timelines have been registered.
        """
        if self.active_timeline_guid:
            tl = self._timelines.get(self.active_timeline_guid)
            if tl is not None:
                return tl
        return next(iter(self._timelines.values()), None)

    @property
    def timelines(self) -> dict[str, otio.schema.Timeline]:
        """Read-only view of all registered timelines, keyed by sync GUID."""
        return self._timelines

    @property
    def object_map(self) -> dict[str, otio.core.SerializableObject]:
        """Read-only view of the flat GUID → OTIO object index."""
        return self._object_map

    # ------------------------------------------------------------------
    # Timeline Registration
    # ------------------------------------------------------------------

    def register_timeline(self, timeline: otio.schema.Timeline) -> OTIOSyncProxy:
        """Register a timeline, assign GUIDs to all its objects, and index them.

        Sets :attr:`active_timeline_guid` to the new timeline's GUID if no active
        timeline exists yet.

        :param timeline: The :class:`~opentimelineio.schema.Timeline` to register.
        :returns: An :class:`~otio_sync_core.proxy.OTIOSyncProxy` wrapping *timeline*
            so that attribute writes are automatically broadcast.
        """
        self._ensure_guid_and_map(timeline)
        guid = timeline.metadata["sync"]["guid"]
        self._timelines[guid] = timeline
        self._traverse_and_map(timeline)
        if self.active_timeline_guid is None:
            self.active_timeline_guid = guid
        return OTIOSyncProxy(timeline, self)

    def _traverse_and_map(self, item: otio.core.SerializableObject) -> None:
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
            self._ensure_guid_and_map(obj)

    def _ensure_guid_and_map(self, obj: Any) -> None:
        """Assign a sync GUID to *obj* if absent, then add it to ``_object_map``.

        Non-:class:`~opentimelineio.core.SerializableObject` values are ignored.

        :param obj: Candidate OTIO object.
        """
        if not isinstance(obj, otio.core.SerializableObject):
            return
        if "sync" not in obj.metadata:
            obj.metadata["sync"] = {}
        if "guid" not in obj.metadata["sync"]:
            obj.metadata["sync"]["guid"] = str(uuid.uuid4())
        self._object_map[obj.metadata["sync"]["guid"]] = obj

    # ------------------------------------------------------------------
    # Observer Registry
    # ------------------------------------------------------------------

    def on_property_changed(
        self, callback: Callable[[str, str, Any], None]
    ) -> Callable[[str, str, Any], None]:
        """Register a callback for property change events.

        Fires for both locally-initiated and remotely-applied property changes.
        May be used as a decorator.

        :param callback: Callable receiving ``(target_uuid, path, new_value)``.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._property_callbacks.append(callback)
        return callback

    def on_hierarchy_changed(
        self, callback: Callable[[str, str, str], None]
    ) -> Callable[[str, str, str], None]:
        """Register a callback for hierarchy change events.

        Fires for both locally-initiated and remotely-applied structural changes.
        May be used as a decorator.

        :param callback: Callable receiving ``(parent_uuid, action, child_uuid)``
            where *action* is one of ``"insert_child"`` or ``"remove_child"``.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._hierarchy_callbacks.append(callback)
        return callback

    def on_status_changed(
        self, callback: Callable[[str], None]
    ) -> Callable[[str], None]:
        """Register a callback fired whenever :attr:`status` transitions.

        :param callback: Callable receiving the new status string.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._status_callbacks.append(callback)
        return callback

    def on_playback_changed(
        self, callback: Callable[[dict[str, Any]], None]
    ) -> Callable[[dict[str, Any]], None]:
        """Register a callback fired whenever a playback-state message arrives.

        The callback receives the raw playback state dict (same structure as
        :attr:`playback_state`).  Also usable as a decorator.

        :param callback: Callable receiving the playback state dict.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._playback_callbacks.append(callback)
        return callback

    def on_synced(
        self, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register a callback fired once when the session reaches ``STATE_SYNCED``.

        Fires both when this peer self-elects as master and when it finishes
        joining an existing master.  Also usable as a decorator.

        :param callback: Zero-argument callable.
        :returns: The *callback* unchanged (decorator-compatible).
        """
        self._synced_callbacks.append(callback)
        return callback

    def _set_status(self, new_status: str) -> None:
        """Update :attr:`status` and fire registered status-change callbacks."""
        if new_status == self.status:
            return
        self.status = new_status
        for cb in self._status_callbacks:
            try:
                cb(new_status)
            except Exception as e:
                _log(f"on_status_changed callback error: {e}")
        if new_status == STATE_SYNCED:
            for cb in self._synced_callbacks:
                try:
                    cb()
                except Exception as e:
                    _log(f"on_synced callback error: {e}")

    def _fire_property_changed(
        self, target_uuid: str, path: str, value: Any
    ) -> None:
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

    # ------------------------------------------------------------------
    # Master Election & Session State
    # ------------------------------------------------------------------

    def start_session(self) -> None:
        """Begin the join process by broadcasting a master-discovery message.

        Transitions :attr:`status` to ``STATE_DISCOVERING``.  The caller is
        responsible for timing out and calling the appropriate method if no master
        responds (see class docstring for the full lifecycle).
        """
        self._set_status(STATE_DISCOVERING)
        self.broadcast_master_discovery()

    def broadcast_master_discovery(self) -> None:
        """Broadcast a ``WHO_IS_MASTER`` session message."""
        self._send_session_event("WHO_IS_MASTER", {"requester_guid": self.self_guid})

    def broadcast_master_response(self) -> None:
        """Broadcast an ``I_AM_MASTER`` session message.

        Called after self-election (discovery timeout) or when an existing master
        receives a ``WHO_IS_MASTER`` it should answer.
        """
        self._send_session_event("I_AM_MASTER", {"master_guid": self.self_guid})

    def request_state(self) -> None:
        """Send a ``STATE_REQUEST`` to the master and enter ``STATE_JOINING``.

        Non-session messages received while joining are buffered in
        ``_delta_buffer`` and replayed by :meth:`apply_snapshot`.
        """
        if self.master_guid:
            self._set_status(STATE_JOINING)
            self._send_session_event("STATE_REQUEST", {
                "target_guid": self.master_guid,
                "requester_guid": self.self_guid,
            })

    def send_state_snapshot(
        self,
        target_guid: str,
        playback_state: dict[str, Any] | None = None,
    ) -> None:
        """Serialise all registered timelines and send a full snapshot to a joiner.

        Only the master should call this method.  The snapshot is broadcast to the
        whole session (not unicast), but only the peer whose GUID matches *target_guid*
        will act on it.

        :param target_guid: GUID of the requesting peer.
        :param playback_state: Optional current playback state dict to include so the
            joiner can immediately seek to the right position.
        """
        if not self.is_master or not self._timelines:
            return
        payload = {
            "target_guid": target_guid,
            "timelines": {
                guid: _otio_to_dict(tl) for guid, tl in self._timelines.items()
            },
            "active_timeline_guid": self.active_timeline_guid,
            "snapshot_timestamp": time.time(),
        }
        if playback_state:
            payload["playback_state"] = playback_state
        self._send_session_event("STATE_SNAPSHOT", payload)

    def _send_session_event(
        self, event_name: str, payload_data: dict[str, Any]
    ) -> None:
        """Wrap *payload_data* in a session envelope and send it.

        :param event_name: Session event type (e.g. ``"WHO_IS_MASTER"``).
        :param payload_data: Event-specific payload dict.
        """
        if not self.network:
            return
        self.network.send_payload({
            "command": "SESSION",
            "event": event_name,
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": payload_data,
        })

    # ------------------------------------------------------------------
    # Data Mutations
    # ------------------------------------------------------------------

    def set_property(self, target_uuid: str, path: str, value: Any) -> None:
        """Apply a property change locally and broadcast it to peers.

        *path* may be a plain attribute name (e.g. ``"name"``) or a
        ``metadata/``-prefixed slash-separated key path (e.g.
        ``"metadata/custom/key"``).

        :param target_uuid: GUID of the target OTIO object.
        :param path: Attribute or metadata key path.
        :param value: New value to set.
        """
        if target_uuid not in self._object_map:
            _log(f"set_property FAILED: object {target_uuid} not found")
            return

        obj = self._object_map[target_uuid]

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

        if not self._is_syncing and self.network:
            self.network.send_payload({
                "command": "OTIO_SESSION",
                "event": "SET_PROPERTY",
                "session_id": self.session_id,
                "source_guid": self.self_guid,
                "payload": {
                    "target_uuid": target_uuid,
                    "path": path,
                    "value": value,
                    "sync_timestamp": time.time(),
                },
            })

    def insert_child(
        self,
        parent_uuid: str,
        child_obj: otio.core.SerializableObject,
        index: int = -1,
    ) -> None:
        """Insert *child_obj* into the parent container and broadcast the change.

        A GUID is assigned to *child_obj* if it does not already have one.
        Use ``index=-1`` to append.

        :param parent_uuid: GUID of the parent container (Track or Stack).
        :param child_obj: OTIO object to insert.
        :param index: Position at which to insert; ``-1`` appends.
        """
        if parent_uuid not in self._object_map:
            _log(
                f"insert_child FAILED: parent {parent_uuid} not in object_map "
                f"(known={list(self._object_map.keys())[:5]})"
            )
            return

        parent = self._object_map[parent_uuid]
        self._ensure_guid_and_map(child_obj)

        if index == -1:
            parent.append(child_obj)
        else:
            parent.insert(index, child_obj)

        child_uuid = child_obj.metadata["sync"]["guid"]
        self._fire_hierarchy_changed(parent_uuid, "insert_child", child_uuid)

        if not self._is_syncing and self.network:
            _log(
                f"insert_child broadcasting: parent={parent_uuid} index={index} "
                f"child={getattr(child_obj, 'name', '?')}"
            )
            self.network.send_payload({
                "command": "OTIO_SESSION",
                "event": "INSERT_CHILD",
                "session_id": self.session_id,
                "source_guid": self.self_guid,
                "payload": {
                    "parent_uuid": parent_uuid,
                    "index": index,
                    "child_data": _otio_to_dict(child_obj),
                    "sync_timestamp": time.time(),
                },
            })

    def broadcast_playback_state(
        self,
        state_dict: dict[str, Any],
        timeline_guid: str | None = None,
    ) -> None:
        """Broadcast the current playback state to all peers.

        :param state_dict: Playback state fields (``playing``, ``current_time``,
            ``looping``, etc.) as defined by the protocol.
        :param timeline_guid: GUID of the timeline being played; falls back to
            :attr:`active_timeline_guid`.
        """
        if self._is_syncing or not self.network:
            return
        inner = dict(state_dict)
        inner["sync_timestamp"] = time.time()
        inner["timeline_guid"] = timeline_guid or self.active_timeline_guid
        self.network.send_payload({
            "command": "PLAYBACK_SETTINGS",
            "event": "SET",
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": inner,
        })

    @staticmethod
    def _annotation_track_end(track: otio.schema.Track) -> int:
        """Return the total duration (in frames) of all children in *track*.

        This is the track position at which the next appended child would start,
        analogous to ``lastframe`` in ``ORIAnnotations._export_otio_media``.

        :param track: An OTIO :class:`~opentimelineio.schema.Track`.
        :returns: Sum of ``source_range.duration.value`` for all children.
        :rtype: int
        """
        total = 0
        for child in track:
            sr = getattr(child, "source_range", None)
            if sr is not None:
                total += int(sr.duration.value)
        return total

    @staticmethod
    def _find_annotation_clip_at(
        track: otio.schema.Track,
        clip_guid: str,
        frame: int,
    ) -> "otio.schema.Clip | None":
        """Find an existing annotation clip for *(clip_guid, frame)* in *track*.

        :param track: The Annotations :class:`~opentimelineio.schema.Track`.
        :param clip_guid: GUID of the media clip being annotated.
        :param frame: 0-indexed clip-local frame number.
        :returns: The matching :class:`~opentimelineio.schema.Clip`, or ``None``.
        """
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
    ) -> "otio.schema.Clip | None":
        """Check whether *child_obj* is an annotation-merge delta and apply it.

        If *parent* already contains a clip for the same ``(clip_guid, frame)``
        as *child_obj*, the incoming ``annotation_commands`` are appended to
        that existing clip and the existing clip is returned (so the caller can
        raise an ``annotation_commands_added`` event without inserting a
        structural duplicate).  Returns ``None`` when no merge applies.

        :param parent: The parent track that would receive *child_obj*.
        :param child_obj: The incoming OTIO object from an ``INSERT_CHILD`` message.
        :returns: The existing clip if a merge occurred, otherwise ``None``.
        """
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
        existing = SyncManager._find_annotation_clip_at(
            parent, incoming_cg, incoming_frame
        )
        if existing is None:
            return None
        existing.metadata["annotation_commands"].extend(incoming_cmds)
        return existing

    @staticmethod
    def _make_annotation_clip(
        clip_guid: str,
        clip_local_time: otio.opentime.RationalTime,
        otio_events: list,
    ) -> otio.schema.Clip:
        """Build a 1-frame annotation clip for *clip_guid* at *clip_local_time*.

        :param clip_guid: GUID of the media clip being annotated.
        :param clip_local_time: 0-indexed time within the clip source range.
        :param otio_events: Deserialised SyncEvent objects to embed.
        :returns: A new :class:`~opentimelineio.schema.Clip`.
        """
        frame = int(clip_local_time.value)
        fps = clip_local_time.rate
        clip = otio.schema.Clip(name=f"Annotation_{frame}")
        clip.source_range = otio.opentime.TimeRange(
            clip_local_time,
            otio.opentime.RationalTime(1, fps),
        )
        clip.metadata["annotation_commands"] = otio_events
        clip.metadata["clip_guid"] = clip_guid
        return clip

    def broadcast_add_annotation(
        self,
        annotation_track_guid: str,
        clip_guid: str,
        clip_local_time: otio.opentime.RationalTime,
        events: list[dict[str, Any]],
    ) -> None:
        """Build an annotation clip and insert it via the standard patch path.

        Annotations are expressed as ``insert_child`` patches so that all peers
        apply them through the same code path as any other timeline mutation.

        The annotation track mirrors the structure produced by
        :meth:`ORIAnnotations.ReviewItem._export_otio_media`: each annotated
        frame is a 1-frame :class:`~opentimelineio.schema.Clip` and the gaps
        between annotated frames are :class:`~opentimelineio.schema.Gap` objects
        whose duration is ``frame − track_end`` frames.  A second stroke on an
        already-annotated frame merges its commands into the existing clip rather
        than inserting a duplicate.

        :param annotation_track_guid: GUID of the target Annotations track.
        :param clip_guid: GUID of the media clip being annotated.
        :param clip_local_time: 0-indexed time within the clip's source range.
        :param events: Serialised OTIO SyncEvent dicts (``PaintStart.1``,
            ``PaintPoints.1``) as produced by ``otio.adapters.write_to_string``.
        """
        if not self.network or self.status != STATE_SYNCED:
            return
        if annotation_track_guid not in self._object_map:
            _log(f"broadcast_add_annotation: annotation track {annotation_track_guid} not found")
            return

        otio_events: list[otio.core.SerializableObject] = []
        for e in events:
            try:
                otio_events.append(_dict_to_otio(e) if isinstance(e, dict) else e)
            except Exception as exc:
                _log(f"broadcast_add_annotation: failed to deserialise event: {exc}")

        annotation_track = self._object_map[annotation_track_guid]
        frame = int(clip_local_time.value)
        fps = clip_local_time.rate

        existing = self._find_annotation_clip_at(annotation_track, clip_guid, frame)
        if existing is not None:
            # A clip already exists at this frame — merge the new commands in locally
            # and broadcast a delta clip so peers can apply the same merge.
            existing.metadata["annotation_commands"].extend(otio_events)
            delta_clip = self._make_annotation_clip(clip_guid, clip_local_time, otio_events)
            self._ensure_guid_and_map(delta_clip)
            self.network.send_payload({
                "command": "OTIO_SESSION",
                "event": "INSERT_CHILD",
                "session_id": self.session_id,
                "source_guid": self.self_guid,
                "payload": {
                    "parent_uuid": annotation_track_guid,
                    "index": -1,
                    "child_data": _otio_to_dict(delta_clip),
                    "sync_timestamp": time.time(),
                },
            })
        else:
            # New frame — insert a Gap to reach it (if needed) then the clip.
            track_end = self._annotation_track_end(annotation_track)
            if frame > track_end:
                gap = otio.schema.Gap(
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(track_end, fps),
                        duration=otio.opentime.RationalTime(frame - track_end, fps),
                    )
                )
                self.insert_child(annotation_track_guid, gap)
            ann_clip = self._make_annotation_clip(clip_guid, clip_local_time, otio_events)
            self.insert_child(annotation_track_guid, ann_clip)

    def broadcast_selection(self, nodes: list[str]) -> None:
        """Broadcast the current UI selection to all peers.

        :param nodes: List of selected node identifiers (currently RV node names;
            see open question in the protocol spec about migrating to OTIO GUIDs).
        """
        if self._is_syncing or not self.network or self.status != STATE_SYNCED:
            return
        self.network.send_payload({
            "command": "SELECTION",
            "event": "SET",
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": {"nodes": nodes, "sync_timestamp": time.time()},
        })

    def broadcast_move_child(
        self, parent_uuid: str, child_uuid: str, to_index: int
    ) -> None:
        """Move *child_uuid* to *to_index* within its parent and broadcast the change.

        Applies the reorder locally before broadcasting so the local OTIO model
        stays consistent regardless of network round-trip time.

        :param parent_uuid: GUID of the parent container.
        :param child_uuid: GUID of the child to move.
        :param to_index: Target position in the parent's child list.
        """
        if self._is_syncing:
            _log("broadcast_move_child: skipped (_is_syncing)")
            return
        if not self.network:
            _log("broadcast_move_child: skipped (no network)")
            return
        if self.status != STATE_SYNCED:
            _log(f"broadcast_move_child: skipped (status={self.status})")
            return

        parent = self._object_map.get(parent_uuid)
        child = self._object_map.get(child_uuid)
        if parent is None:
            _log(f"broadcast_move_child: skipped (parent {parent_uuid} not in object_map)")
            return
        if child is None:
            _log(
                f"broadcast_move_child: skipped (child {child_uuid} not in object_map, "
                f"known={list(self._object_map.keys())[:5]})"
            )
            return

        current_index = next(
            (i for i, item in enumerate(parent)
             if item.metadata.get("sync", {}).get("guid") == child_uuid),
            None,
        )
        if current_index is None:
            return
        if current_index == to_index:
            return

        del parent[current_index]
        parent.insert(to_index, child)

        self.network.send_payload({
            "command": "OTIO_SESSION",
            "event": "MOVE_CHILD",
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": {
                "parent_uuid": parent_uuid,
                "child_uuid": child_uuid,
                "to_index": to_index,
                "sync_timestamp": time.time(),
            },
        })

    def broadcast_remove_child(self, parent_uuid: str, child_uuid: str) -> None:
        """Remove *child_uuid* from its parent and broadcast the change.

        The child is removed from both the parent container and ``_object_map``.

        :param parent_uuid: GUID of the parent container.
        :param child_uuid: GUID of the child to remove.
        """
        if self._is_syncing or not self.network or self.status != STATE_SYNCED:
            return

        parent = self._object_map.get(parent_uuid)
        child = self._object_map.get(child_uuid)
        if parent is None:
            _log(f"broadcast_remove_child: skipped (parent {parent_uuid} not in object_map)")
            return
        if child is None:
            _log(f"broadcast_remove_child: skipped (child {child_uuid} not in object_map)")
            return

        current_index = next(
            (i for i, item in enumerate(parent)
             if item.metadata.get("sync", {}).get("guid") == child_uuid),
            None,
        )
        if current_index is None:
            _log(f"broadcast_remove_child: child {child_uuid} not found in parent track")
            return

        del parent[current_index]
        del self._object_map[child_uuid]

        _log(f"broadcast_remove_child: removed {child_uuid} from {parent_uuid}")
        self.network.send_payload({
            "command": "OTIO_SESSION",
            "event": "REMOVE_CHILD",
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": {
                "parent_uuid": parent_uuid,
                "child_uuid": child_uuid,
                "sync_timestamp": time.time(),
            },
        })

    # ------------------------------------------------------------------
    # Message Handling
    # ------------------------------------------------------------------

    def apply_patch(self, payload: dict[str, Any]) -> tuple[str, Any] | None:
        """Apply a single incoming message from the network.

        Dispatches on ``command`` and ``event`` fields.  Returns an
        ``(action, data)`` tuple when the caller needs to act (e.g. to update RV
        state), or ``None`` when the message was fully handled internally.

        Messages from :attr:`self_guid` are silently discarded.  Messages arriving
        during ``STATE_JOINING`` are buffered (except session messages) and replayed
        by :meth:`apply_snapshot`.

        :param payload: Parsed message envelope received from the network.
        :returns: ``(action_name, action_data)`` or ``None``.
        """
        command = payload.get("command")
        event = payload.get("event")
        data = payload.get("payload", {})
        source = payload.get("source_guid", "unknown")

        if source == self.self_guid:
            return None

        _log(f"apply_patch: command={command} event={event} source={source[:8]}")

        if self.status == STATE_JOINING and command != "SESSION":
            self._delta_buffer.append(payload)
            return None

        self._is_syncing = True
        try:
            if command == "SESSION":
                if event == "WHO_IS_MASTER" and self.is_master:
                    self.broadcast_master_response()
                elif event == "I_AM_MASTER":
                    self.master_guid = data.get("master_guid")
                    if self.status == STATE_DISCOVERING:
                        return ("master_found", self.master_guid)
                elif event == "STATE_REQUEST" and self.is_master:
                    requester = data.get("requester_guid") or source
                    return ("state_request_received", requester)
                elif (event == "STATE_SNAPSHOT"
                        and data.get("target_guid") == self.self_guid):
                    return ("state_snapshot_received", data)
                return None

            if command == "PLAYBACK_SETTINGS" and event == "SET":
                self.playback_state = data
                for cb in self._playback_callbacks:
                    try:
                        cb(data)
                    except Exception as e:
                        _log(f"on_playback_changed callback error: {e}")
                return ("playback_settings", data)

            if command == "SELECTION" and event == "SET":
                return ("selection_changed", data)

            if command != "OTIO_SESSION":
                return None

            if event == "SET_PROPERTY":
                target_uuid = data.get("target_uuid")
                if target_uuid in self._object_map:
                    obj = self._object_map[target_uuid]
                    path: str = data.get("path")
                    value: Any = data.get("value")
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

            elif event == "MOVE_CHILD":
                parent_uuid: str = data.get("parent_uuid")
                child_uuid: str = data.get("child_uuid")
                to_index: int = data.get("to_index", 0)
                parent = self._object_map.get(parent_uuid)
                child = self._object_map.get(child_uuid)
                if parent is not None and child is not None:
                    current_index = next(
                        (i for i, item in enumerate(parent)
                         if item.metadata.get("sync", {}).get("guid") == child_uuid),
                        None,
                    )
                    if current_index is not None:
                        del parent[current_index]
                        parent.insert(to_index, child)
                        return ("move_child", data)

            elif event == "REMOVE_CHILD":
                parent_uuid = data.get("parent_uuid")
                child_uuid = data.get("child_uuid")
                parent = self._object_map.get(parent_uuid)
                if parent is not None:
                    current_index = next(
                        (i for i, item in enumerate(parent)
                         if item.metadata.get("sync", {}).get("guid") == child_uuid),
                        None,
                    )
                    if current_index is not None:
                        del parent[current_index]
                        self._object_map.pop(child_uuid, None)
                        return ("remove_child", data)

            elif event == "INSERT_CHILD":
                parent_uuid = data.get("parent_uuid")
                if parent_uuid in self._object_map:
                    parent = self._object_map[parent_uuid]
                    index: int = data.get("index", -1)
                    child_obj = _dict_to_otio(data.get("child_data"))
                    merged = self._try_merge_annotation(parent, child_obj)
                    if merged is not None:
                        self._ensure_guid_and_map(child_obj)
                        # Return (merged_clip, delta_clip): merged has all commands
                        # for full re-render; delta has only the new commands for
                        # additive renderers (e.g. RV paint).
                        return ("annotation_commands_added", (merged, child_obj))
                    if index == -1:
                        parent.append(child_obj)
                    else:
                        parent.insert(index, child_obj)
                    self._ensure_guid_and_map(child_obj)
                    child_uuid = child_obj.metadata["sync"]["guid"]
                    self._fire_hierarchy_changed(parent_uuid, "insert_child", child_uuid)
                    return ("insert_child", child_obj)
        finally:
            self._is_syncing = False

        return None

    def tick(self) -> list[tuple[str, Any]]:
        """Poll the network and auto-advance the session handshake.

        This is the recommended entry point for new client integrations.
        It wraps :meth:`receive_and_apply_all` and handles the session
        state machine automatically:

        * ``master_found``          → calls :meth:`request_state` internally.
        * ``state_snapshot_received`` → calls :meth:`apply_snapshot` internally.
        * ``state_request_received`` → **returned to caller**; the master must
          respond by calling :meth:`send_state_snapshot`.

        Application-level events (``playback_settings``, ``selection_changed``,
        ``annotation_*``, ``insert_child``, …) are returned so the caller can
        react to them.  Playback updates are also delivered through the
        :meth:`on_playback_changed` callback if one is registered.

        Compare with :meth:`receive_and_apply_all`, which returns every raw
        action tuple and leaves the handshake entirely to the caller.

        :returns: List of ``(action, data)`` tuples requiring application
            action (subset of what :meth:`receive_and_apply_all` would return).
        """
        app_events: list[tuple[str, Any]] = []
        for action, data in self.receive_and_apply_all():
            if action == "master_found":
                self.request_state()
            elif action == "state_snapshot_received":
                self.apply_snapshot(data)
                if "playback_state" in data:
                    self.playback_state = data["playback_state"]
            else:
                app_events.append((action, data))
        return app_events

    def receive_and_apply_all(self) -> list[tuple[str, Any]]:
        """Drain the network and apply every pending message.

        :returns: List of ``(action, data)`` tuples for messages that require a
            response from the caller (e.g. to update RV state).  Empty when all
            messages were handled internally or no messages were waiting.
        """
        if not self.network:
            return []
        results: list[tuple[str, Any]] = []
        for p in self.network.receive_payloads():
            res = self.apply_patch(p)
            if res:
                results.append(res)
        return results

    def apply_snapshot(self, snapshot_data: dict[str, Any]) -> list[tuple[str, Any]]:
        """Replace local state with a full snapshot and replay buffered deltas.

        Clears ``_object_map`` and ``_timelines``, deserialises the timelines from
        *snapshot_data*, then replays any buffered messages whose ``sync_timestamp``
        is newer than the snapshot.  Transitions :attr:`status` to ``STATE_SYNCED``.

        :param snapshot_data: ``payload`` dict from a ``STATE_SNAPSHOT`` message.
        :returns: List of ``(action, data)`` tuples produced by replaying buffered
            deltas; to be handled by the caller in the same way as the return value
            of :meth:`receive_and_apply_all`.
        """
        timestamp: float = snapshot_data.get("snapshot_timestamp", 0)

        self._is_syncing = True
        try:
            self._timelines = {}
            self._object_map = {}
            for guid, tl_dict in snapshot_data.get("timelines", {}).items():
                tl = _dict_to_otio(tl_dict)
                self._timelines[guid] = tl
                self._traverse_and_map(tl)
            self.active_timeline_guid = snapshot_data.get("active_timeline_guid")

            replay_results: list[tuple[str, Any]] = []
            for payload in self._delta_buffer:
                p_data = payload.get("payload", {})
                p_time: float = p_data.get("sync_timestamp", 0)
                if p_time > timestamp:
                    res = self.apply_patch(payload)
                    if res:
                        replay_results.append(res)

            self._delta_buffer = []
            self._set_status(STATE_SYNCED)
            return replay_results
        finally:
            self._is_syncing = False

    def close(self) -> None:
        """Stop the network backend and release all resources."""
        if self.network:
            self.network.stop()
