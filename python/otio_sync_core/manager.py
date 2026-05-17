import uuid
import time
import json
import logging as _logging
import opentimelineio as otio
from .proxy import OTIOSyncProxy

_logger = _logging.getLogger("otio_sync")


def _log(msg):
    if _logger.handlers:
        _logger.debug(msg)


def _otio_to_dict(obj):
    return json.loads(otio.adapters.write_to_string(obj, "otio_json", indent=-1))


def _dict_to_otio(d):
    return otio.adapters.read_from_string(json.dumps(d), "otio_json")

# Constants for Session State
STATE_NONE = "NONE"
STATE_DISCOVERING = "DISCOVERING"
STATE_JOINING = "JOINING"
STATE_SYNCED = "SYNCED"

class SyncManager:
    """
    SyncManager maintains a map of OTIO objects and coordinates their 
    synchronization across a network layer.
    """
    def __init__(self, session_id="default_session", self_guid=None, network=None):
        self.session_id = session_id
        self.self_guid = self_guid or str(uuid.uuid4())
        self.network = network
        
        self._object_map = {}
        self._timelines = {}          # guid -> otio.Timeline
        self.active_timeline_guid = None
        self._is_syncing = False

        self._property_callbacks = []
        self._hierarchy_callbacks = []

        # Master/Session State
        self.status = STATE_NONE
        self.is_master = False
        self.master_guid = None
        self._delta_buffer = []
        self._last_snapshot_time = 0

    @property
    def root_timeline(self):
        """The currently active timeline, or the first registered one."""
        if self.active_timeline_guid:
            tl = self._timelines.get(self.active_timeline_guid)
            if tl is not None:
                return tl
        return next(iter(self._timelines.values()), None)

    def register_timeline(self, timeline: otio.schema.Timeline):
        """Map all objects in the timeline and wrap it in a proxy for change tracking."""
        self._ensure_guid_and_map(timeline)
        guid = timeline.metadata["sync"]["guid"]
        self._timelines[guid] = timeline
        self._traverse_and_map(timeline)
        if self.active_timeline_guid is None:
            self.active_timeline_guid = guid
        return OTIOSyncProxy(timeline, self)

    def _traverse_and_map(self, item):
        """Recursively assign GUIDs and add all objects under item to _object_map."""
        def _walk(node):
            yield node
            if hasattr(node, "tracks"):
                stack = node.tracks
                yield stack
                for child in stack:
                    yield from _walk(child)
            elif hasattr(node, "__iter__") and not isinstance(node, str):
                # OTIO Track/Stack expose children via __iter__ (not .children property)
                for child in node:
                    yield from _walk(child)
        for obj in _walk(item):
            self._ensure_guid_and_map(obj)
        
    def _ensure_guid_and_map(self, obj):
        if not isinstance(obj, otio.core.SerializableObject):
            return
            
        if "sync" not in obj.metadata:
            obj.metadata["sync"] = {}
            
        if "guid" not in obj.metadata["sync"]:
            obj.metadata["sync"]["guid"] = str(uuid.uuid4())
            
        obj_uuid = obj.metadata["sync"]["guid"]
        self._object_map[obj_uuid] = obj

    # ------------------------------------------------------------------
    # Observer Registry
    # ------------------------------------------------------------------

    def on_property_changed(self, callback):
        """Register callback(target_uuid, path, new_value). Usable as a decorator.

        Fires for both locally-initiated and remotely-applied property changes.
        """
        self._property_callbacks.append(callback)
        return callback

    def on_hierarchy_changed(self, callback):
        """Register callback(parent_uuid, action, child_uuid). Usable as a decorator.

        action is one of: 'insert_child', 'remove_child'.
        Fires for both locally-initiated and remotely-applied structural changes.
        """
        self._hierarchy_callbacks.append(callback)
        return callback

    def _fire_property_changed(self, target_uuid, path, value):
        for cb in self._property_callbacks:
            try:
                cb(target_uuid, path, value)
            except Exception as e:
                _log(f"on_property_changed callback error: {e}")

    def _fire_hierarchy_changed(self, parent_uuid, action, child_uuid):
        for cb in self._hierarchy_callbacks:
            try:
                cb(parent_uuid, action, child_uuid)
            except Exception as e:
                _log(f"on_hierarchy_changed callback error: {e}")

    # ------------------------------------------------------------------
    # Master Election & Session State
    # ------------------------------------------------------------------

    def start_session(self):
        """Start the join process by looking for a master."""
        self.status = STATE_DISCOVERING
        self.broadcast_master_discovery()
        # The caller (RV Plugin) should check if we found a master after a timeout

    def broadcast_master_discovery(self):
        self._send_session_event("WHO_IS_MASTER", {"requester_guid": self.self_guid})

    def broadcast_master_response(self):
        self._send_session_event("I_AM_MASTER", {"master_guid": self.self_guid})

    def request_state(self):
        if self.master_guid:
            self.status = STATE_JOINING
            self._send_session_event("STATE_REQUEST", {
                "target_guid": self.master_guid,
                "requester_guid": self.self_guid
            })

    def send_state_snapshot(self, target_guid, playback_state=None):
        """Master sends full state to the requesting peer."""
        if not self.is_master or not self._timelines: return

        payload = {
            "target_guid": target_guid,
            "timelines": {guid: _otio_to_dict(tl) for guid, tl in self._timelines.items()},
            "active_timeline_guid": self.active_timeline_guid,
            "snapshot_timestamp": time.time()
        }
        if playback_state:
            payload["playback_state"] = playback_state
        self._send_session_event("STATE_SNAPSHOT", payload)

    def _send_session_event(self, event_name, payload_data):
        if not self.network: return
        payload = {
            "command": "SESSION",
            "event": event_name,
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": payload_data
        }
        self.network.send_payload(payload)

    # ------------------------------------------------------------------
    # Data Mutations
    # ------------------------------------------------------------------

    def set_property(self, target_uuid, path, value):
        """Apply a property change locally and broadcast it."""
        if target_uuid not in self._object_map:
            _log(f"set_property FAILED: object {target_uuid} not found")
            return
            
        obj = self._object_map[target_uuid]
        
        # Apply locally
        if path.startswith("metadata/"):
            parts = path.split("/")
            curr = obj.metadata
            for part in parts[1:-1]:
                if part not in curr: curr[part] = {}
                curr = curr[part]
            curr[parts[-1]] = value
        else:
            setattr(obj, path, value)
            
        self._fire_property_changed(target_uuid, path, value)

        if not self._is_syncing and self.network:
            payload = {
                "command": "OTIO_SESSION",
                "event": "SET_PROPERTY",
                "session_id": self.session_id,
                "source_guid": self.self_guid,
                "payload": {
                    "target_uuid": target_uuid,
                    "path": path,
                    "value": value,
                    "sync_timestamp": time.time()
                }
            }
            self.network.send_payload(payload)

    def insert_child(self, parent_uuid, child_obj, index=-1):
        """Insert child into parent and broadcast."""
        if parent_uuid not in self._object_map:
            _log(f"insert_child FAILED: parent {parent_uuid} not in object_map (known={list(self._object_map.keys())[:5]})")
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
            _log(f"insert_child broadcasting: parent={parent_uuid} index={index} child={getattr(child_obj, 'name', '?')}")
            payload = {
                "command": "OTIO_SESSION",
                "event": "INSERT_CHILD",
                "session_id": self.session_id,
                "source_guid": self.self_guid,
                "payload": {
                    "parent_uuid": parent_uuid,
                    "index": index,
                    "child_data": _otio_to_dict(child_obj),
                    "sync_timestamp": time.time()
                }
            }
            self.network.send_payload(payload)

    def broadcast_playback_state(self, state_dict, timeline_guid=None):
        if self._is_syncing or not self.network: return
        inner = dict(state_dict)
        inner["sync_timestamp"] = time.time()
        inner["timeline_guid"] = timeline_guid or self.active_timeline_guid
        payload = {
            "command": "PLAYBACK_SETTINGS",
            "event": "SET",
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": inner,
        }
        self.network.send_payload(payload)

    def broadcast_annotation(self, data):
        if self._is_syncing or not self.network: return
        
        if self.is_master:
            self._persist_annotation_to_timeline(data)
            
        payload = {
            "command": "ANNOTATION",
            "event": "STROKE_RELEASE",
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": data
        }
        self.network.send_payload(payload)

    def broadcast_selection(self, nodes):
        if self._is_syncing or not self.network or self.status != STATE_SYNCED: return
        payload = {
            "command": "SELECTION",
            "event": "SET",
            "session_id": self.session_id,
            "source_guid": self.self_guid,
            "payload": {"nodes": nodes, "sync_timestamp": time.time()},
        }
        self.network.send_payload(payload)

    def broadcast_move_child(self, parent_uuid, child_uuid, to_index):
        """Broadcast a MOVE_CHILD patch per the OTIO Sync Protocol."""
        if self._is_syncing:
            _log(f"broadcast_move_child: skipped (_is_syncing)")
            return
        if not self.network:
            _log(f"broadcast_move_child: skipped (no network)")
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
            _log(f"broadcast_move_child: skipped (child {child_uuid} not in object_map, known={list(self._object_map.keys())[:5]})")
            return

        # Apply locally first
        current_index = next(
            (i for i, item in enumerate(parent)
             if item.metadata.get("sync", {}).get("guid") == child_uuid),
            None
        )
        if current_index is None: return
        # del[i] + insert(i, x) is always a no-op — skip to avoid spurious messages.
        if current_index == to_index: return
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
            }
        })

    def broadcast_remove_child(self, parent_uuid, child_uuid):
        """Broadcast a REMOVE_CHILD patch per the OTIO Sync Protocol."""
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
            None
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
            }
        })

    def _persist_annotation_to_timeline(self, data):
        timeline_guid = data.get("timeline_guid") or self.active_timeline_guid
        timeline = self._timelines.get(timeline_guid) if timeline_guid else self.root_timeline
        if not timeline: return

        import opentimelineio as otio
        try:
            otio.schema.schemadef.module_from_name('SyncEvent')
        except Exception:
            pass

        # Extract native schema events from the dictionary
        events = []
        if "start_event_data" in data:
            events.append(_dict_to_otio(data["start_event_data"]))
        if "points_event_data" in data:
            events.append(_dict_to_otio(data["points_event_data"]))

        # Find the appropriate Annotations track
        annotations_tracks = []
        for track in timeline.tracks:
            if track.name and track.name.startswith("Annotations"):
                annotations_tracks.append(track)

        target_track = None
        if annotations_tracks:
            # Sort by suffix if it exists, or just pick the last one
            target_track = annotations_tracks[-1]
        else:
            target_track = otio.schema.Track("Annotations")
            timeline.tracks.append(target_track)

        frame = data.get("frame", 1)
        fps = data.get("fps", 24.0)
        media_path = data.get("media_path")

        target_time_offset = otio.opentime.RationalTime(0, fps)

        if media_path:
            for track in timeline.tracks:
                if track.name == "Media":
                    for c in track:
                        if isinstance(c, otio.schema.Clip):
                            ref = c.media_reference
                            if isinstance(ref, otio.schema.ExternalReference):
                                import os
                                if ref.target_url == media_path or os.path.basename(ref.target_url) == os.path.basename(media_path):
                                    break
                        target_time_offset += c.duration()
        
        # RV frames are 1-indexed, OTIO time starts at 0.0
        otio_frame = frame - 1 if frame > 0 else 0
        target_time = target_time_offset + otio.opentime.RationalTime(otio_frame, fps)
        current_time = otio.opentime.RationalTime(0, fps)
        clip_duration = otio.opentime.RationalTime(1, fps)
        
        # Find where to insert
        for i, child in enumerate(list(target_track)):
            child_duration = child.source_range.duration if child.source_range else child.duration()
            child_end = current_time + child_duration
            
            if current_time <= target_time < child_end:
                if isinstance(child, otio.schema.Clip):
                    if "annotation_commands" not in child.metadata:
                        child.metadata["annotation_commands"] = []
                    child.metadata["annotation_commands"].extend(events)
                    return
                elif isinstance(child, otio.schema.Gap):
                    gap_start_duration = target_time - current_time
                    gap_end_duration = child_end - (target_time + clip_duration)
                    
                    new_items = []
                    if gap_start_duration.value > 0:
                        new_items.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(
                            start_time=otio.opentime.RationalTime(0, fps),
                            duration=gap_start_duration
                        )))
                    
                    clip = otio.schema.Clip(name=f"Annotation_{frame}")
                    clip.source_range = otio.opentime.TimeRange(start_time=otio.opentime.RationalTime(0, fps), duration=clip_duration)
                    clip.metadata["annotation_commands"] = list(events)
                    clip.metadata["annotated_clip_name"] = data.get("node_name", "unknown")
                    clip.metadata["rv_frame"] = frame
                    clip.metadata["media_path"] = media_path
                    new_items.append(clip)
                    
                    if gap_end_duration.value > 0:
                        new_items.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(
                            start_time=otio.opentime.RationalTime(0, fps),
                            duration=gap_end_duration
                        )))
                    
                    del target_track[i]
                    for item in reversed(new_items):
                        target_track.insert(i, item)
                    return
            current_time = child_end

        if target_time > current_time:
            target_track.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, fps),
                duration=target_time - current_time
            )))
        
        clip = otio.schema.Clip(name=f"Annotation_{frame}")
        clip.source_range = otio.opentime.TimeRange(start_time=otio.opentime.RationalTime(0, fps), duration=clip_duration)
        clip.metadata["annotation_commands"] = list(events)
        clip.metadata["annotated_clip_name"] = data.get("node_name", "unknown")
        clip.metadata["rv_frame"] = frame
        clip.metadata["media_path"] = media_path
        target_track.append(clip)

    # ------------------------------------------------------------------
    # Message Handling
    # ------------------------------------------------------------------

    def apply_patch(self, payload):
        """Apply an incoming SyncEvent payload."""
        command = payload.get("command")
        event = payload.get("event")
        data = payload.get("payload", {})
        source = payload.get("source_guid", "unknown")

        # Ignore our own messages
        if source == self.self_guid: return None

        _log(f"apply_patch: command={command} event={event} source={source[:8]}")

        # If we are joining, buffer everything except session management
        if self.status == STATE_JOINING and command != "SESSION":
            self._delta_buffer.append(payload)
            return None

        self._is_syncing = True
        try:
            # 1. Session Management
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
                elif event == "STATE_SNAPSHOT" and data.get("target_guid") == self.self_guid:
                    return ("state_snapshot_received", data)
                return None

            # 2. Application Logic
            if command == "PLAYBACK_SETTINGS" and event == "SET":
                return ("playback_settings", data)

            if command == "SELECTION" and event == "SET":
                return ("selection_changed", data)

            if command == "ANNOTATION":
                if self.is_master:
                    self._persist_annotation_to_timeline(data)
                return (f"annotation_{event.lower()}", data)

            if command != "OTIO_SESSION":
                return None
                
            if event == "SET_PROPERTY":
                target_uuid = data.get("target_uuid")
                if target_uuid in self._object_map:
                    obj = self._object_map[target_uuid]
                    path = data.get("path")
                    value = data.get("value")
                    
                    if path.startswith("metadata/"):
                        parts = path.split("/")
                        curr = obj.metadata
                        for part in parts[1:-1]:
                            if part not in curr: curr[part] = {}
                            curr = curr[part]
                        curr[parts[-1]] = value
                    else:
                        setattr(obj, path, value)
                    self._fire_property_changed(target_uuid, path, value)
                    return ("set_property", obj)

            elif event == "MOVE_CHILD":
                parent_uuid = data.get("parent_uuid")
                child_uuid = data.get("child_uuid")
                to_index = data.get("to_index", 0)
                parent = self._object_map.get(parent_uuid)
                child = self._object_map.get(child_uuid)
                if parent is not None and child is not None:
                    current_index = next(
                        (i for i, item in enumerate(parent)
                         if item.metadata.get("sync", {}).get("guid") == child_uuid),
                        None
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
                        None
                    )
                    if current_index is not None:
                        del parent[current_index]
                        self._object_map.pop(child_uuid, None)
                        return ("remove_child", data)

            elif event == "INSERT_CHILD":
                parent_uuid = data.get("parent_uuid")
                if parent_uuid in self._object_map:
                    parent = self._object_map[parent_uuid]
                    index = data.get("index", -1)
                    child_obj = _dict_to_otio(data.get("child_data"))
                    
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

    def receive_and_apply_all(self):
        if not self.network: return []
        payloads = self.network.receive_payloads()
        results = []
        for p in payloads:
            res = self.apply_patch(p)
            if res: results.append(res)
        return results

    def apply_snapshot(self, snapshot_data):
        """Process a full state snapshot and then replay buffered deltas."""
        timestamp = snapshot_data.get("snapshot_timestamp", 0)

        self._is_syncing = True
        try:
            self._timelines = {}
            self._object_map = {}
            for guid, tl_dict in snapshot_data.get("timelines", {}).items():
                tl = _dict_to_otio(tl_dict)
                self._timelines[guid] = tl
                self._traverse_and_map(tl)
            self.active_timeline_guid = snapshot_data.get("active_timeline_guid")

            # Replay buffer for messages that came after the snapshot
            replay_results = []
            for payload in self._delta_buffer:
                p_data = payload.get("payload", {})
                p_time = p_data.get("sync_timestamp", 0)
                if p_time > timestamp:
                    res = self.apply_patch(payload)
                    if res: replay_results.append(res)

            self._delta_buffer = []
            self.status = STATE_SYNCED
            return replay_results
        finally:
            self._is_syncing = False

    def close(self):
        if self.network:
            self.network.stop()
