import uuid
import time
import opentimelineio as otio
from .proxy import OTIOSyncProxy

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
        self.root_timeline = None
        self._is_syncing = False
        
        # Master/Session State
        self.status = STATE_NONE
        self.is_master = False
        self.master_guid = None
        self._delta_buffer = []
        self._last_snapshot_time = 0

    def register_timeline(self, timeline: otio.schema.Timeline):
        """Map all objects in the timeline and wrap it in a proxy for change tracking."""
        self.root_timeline = timeline
        self._object_map = {}
        
        def traverse(item):
            yield item
            if hasattr(item, "tracks"):
                stack = item.tracks
                yield stack
                for child in stack:
                    yield from traverse(child)
            elif hasattr(item, "children"):
                for child in item.children:
                    yield from traverse(child)

        for thing in traverse(timeline):
            self._ensure_guid_and_map(thing)
            
        return OTIOSyncProxy(timeline, self)
        
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

    def send_state_snapshot(self, target_guid):
        """Master sends full state to the requesting peer."""
        if not self.is_master or not self.root_timeline: return
        
        otio_json = otio.adapters.write_to_string(self.root_timeline, "otio_json")
        self._send_session_event("STATE_SNAPSHOT", {
            "target_guid": target_guid,
            "otio_json": otio_json,
            "snapshot_timestamp": time.time()
        })

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
            print(f"[SyncManager] Warning: object {target_uuid} not found")
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
            print(f"[SyncManager] Warning: parent {parent_uuid} not found")
            return

        parent = self._object_map[parent_uuid]
        self._ensure_guid_and_map(child_obj)

        if index == -1:
            parent.append(child_obj)
        else:
            parent.insert(index, child_obj)

        if not self._is_syncing and self.network:
            child_json = otio.adapters.write_to_string(child_obj, "otio_json")
            payload = {
                "command": "OTIO_SESSION",
                "event": "INSERT_CHILD",
                "session_id": self.session_id,
                "source_guid": self.self_guid,
                "payload": {
                    "parent_uuid": parent_uuid,
                    "index": index,
                    "child_json": child_json,
                    "sync_timestamp": time.time()
                }
            }
            self.network.send_payload(payload)

    def broadcast_playback_state(self, state_dict):
        if self._is_syncing or not self.network: return
        inner = dict(state_dict)
        inner["sync_timestamp"] = time.time()
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


    def _persist_annotation_to_timeline(self, data):
        if not self.root_timeline: return
        import opentimelineio as otio
        try:
            otio.schema.schemadef.module_from_name('SyncEvent')
        except Exception:
            pass
            
        # Extract native schema events from the dictionary
        events = []
        if "start_event_json" in data:
            events.append(otio.adapters.read_from_string(data["start_event_json"], "otio_json"))
        if "points_event_json" in data:
            events.append(otio.adapters.read_from_string(data["points_event_json"], "otio_json"))
        
        # Find the appropriate Annotations track
        annotations_tracks = []
        for track in self.root_timeline.tracks:
            if track.name and track.name.startswith("Annotations"):
                annotations_tracks.append(track)
                
        target_track = None
        if annotations_tracks:
            # Sort by suffix if it exists, or just pick the last one
            target_track = annotations_tracks[-1]
        else:
            target_track = otio.schema.Track("Annotations")
            self.root_timeline.tracks.append(target_track)
            
        frame = data.get("frame", 1)
        fps = data.get("fps", 24.0)
        media_path = data.get("media_path")
        
        target_time_offset = otio.opentime.RationalTime(0, fps)
        
        if media_path and self.root_timeline:
            for track in self.root_timeline.tracks:
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
                    return ("set_property", obj)

            elif event == "INSERT_CHILD":
                parent_uuid = data.get("parent_uuid")
                if parent_uuid in self._object_map:
                    parent = self._object_map[parent_uuid]
                    index = data.get("index", -1)
                    child_obj = otio.adapters.read_from_string(data.get("child_json"), "otio_json")
                    
                    if index == -1:
                        parent.append(child_obj)
                    else:
                        parent.insert(index, child_obj)
                        
                    self._ensure_guid_and_map(child_obj)
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
        otio_json = snapshot_data.get("otio_json")
        timestamp = snapshot_data.get("snapshot_timestamp", 0)
        
        self._is_syncing = True
        try:
            self.root_timeline = otio.adapters.read_from_string(otio_json, "otio_json")
            self.register_timeline(self.root_timeline)
            
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
