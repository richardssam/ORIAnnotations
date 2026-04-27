import rv.commands
import rv.rvtypes
import os
import time
import re

try:
    from otio_sync_core import SyncManager, RabbitMQNetwork
    from otio_sync_core.manager import STATE_DISCOVERING, STATE_SYNCED, STATE_JOINING
    import opentimelineio as otio
except ImportError as e:
    SyncManager = None
    RabbitMQNetwork = None
    print(f"[OTIOSync] Import error: {e}")

try:
    from PySide2 import QtCore
except ImportError:
    try:
        from PySide6 import QtCore
    except ImportError:
        QtCore = None

SYNC_DEMO_TRACK_UUID = "otio-sync-demo-track-0"
SYNC_SESSION_ID = "otio-sync-demo"

class OpenRVSyncPlugin(rv.rvtypes.MinorMode):
    def __init__(self):
        rv.rvtypes.MinorMode.__init__(self)

        menus = [
            ("OTIO Sync", [
                ("Add Clip to Timeline...", self.do_add_clip, None, lambda: rv.commands.NeutralMenuState),
                ("_", None),
                ("Sync Status", self.do_show_status, None, lambda: rv.commands.NeutralMenuState),
            ])
        ]

        self.init("openrv_sync_plugin", [
            ("play-start", self.on_rv_play_start, "Broadcast Play"),
            ("play-stop", self.on_rv_play_stop, "Broadcast Stop"),
            ("frame-changed", self.on_rv_frame_changed, "Broadcast Frame"),
            ("selection-changed", self.on_rv_selection_changed, "Broadcast Selection"),
            ("graph-state-change", self.on_rv_graph_state_change, "Broadcast Annotation"),
        ], None, menus)

        self.sync_manager = None
        self._rv_updating = False
        self._track = None
        self._timer = None
        self._last_broadcast_frame = -1
        self._last_selection = []
        self._discovery_start_time = 0
        self._pending_stroke = None  # (node_name, pen_component)
        self._debounce_timer = None

        if SyncManager and RabbitMQNetwork:
            self._setup_sync()
        else:
            print("[OTIOSync] SyncManager/RabbitMQNetwork not available")

    def _setup_sync(self):
        # Create manager first to get a GUID
        self.sync_manager = SyncManager(session_id=SYNC_SESSION_ID)
        
        # Pass that GUID to the network
        network = RabbitMQNetwork(host='localhost', session_id=SYNC_SESSION_ID, self_guid=self.sync_manager.self_guid)
        self.sync_manager.network = network

        # Start Discovery Handshake
        print(f"[OTIOSync] Starting Master Discovery (ID: {self.sync_manager.self_guid})...")
        self.sync_manager.start_session()
        self._discovery_start_time = time.time()

        if QtCore:
            self._timer = QtCore.QTimer()
            self._timer.timeout.connect(self.poll_network)
            self._timer.start(100)

    def _init_as_master(self):
        """Initialise the session as the first participant (Master)."""
        self.sync_manager.is_master = True
        self.sync_manager.status = STATE_SYNCED
        
        timeline = otio.schema.Timeline("Sync Demo Timeline")
        
        stack = otio.schema.Stack("tracks")
        timeline.tracks = stack
        
        # Media Track
        media_track = otio.schema.Track("Media")
        if "sync" not in media_track.metadata: media_track.metadata["sync"] = {}
        media_track.metadata["sync"]["guid"] = SYNC_DEMO_TRACK_UUID
        stack.append(media_track)
        
        # Annotations Track
        annotations_track = otio.schema.Track("Annotations")
        if "sync" not in annotations_track.metadata: annotations_track.metadata["sync"] = {}
        annotations_track.metadata["sync"]["guid"] = "otio-sync-demo-annotations-track"
        stack.append(annotations_track)

        self.sync_manager.register_timeline(timeline)
        self._track = media_track
        
        self.sync_manager.broadcast_master_response()
        print("[OTIOSync] Session Initialized as MASTER")

    # ------------------------------------------------------------------
    # Network Polling & State Handshake
    # ------------------------------------------------------------------

    def poll_network(self):
        if not self.sync_manager: return

        # 1. Process Network First (important!)
        results = self.sync_manager.receive_and_apply_all()

        # 2. Check for Master Discovery Timeout
        if self.sync_manager.status == STATE_DISCOVERING:
            self.sync_manager.broadcast_master_discovery()
            if time.time() - self._discovery_start_time > 2.0:
                self._init_as_master()

        for action, data in results:
            self._rv_updating = True 
            try:
                # 1. Session Handshake Logic
                if action == "master_found":
                    self.sync_manager.request_state()

                elif action == "state_request_received":
                    self.sync_manager.send_state_snapshot(data)

                elif action == "state_snapshot_received":
                    replay_results = self.sync_manager.apply_snapshot(data)
                    self._rebuild_rv_session()
                    # Process any results that were buffered
                    for r_action, r_data in replay_results:
                        self._handle_action(r_action, r_data)

                # 2. Normal Sync Actions
                else:
                    self._handle_action(action, data)
            finally:
                self._rv_updating = False

    def _handle_action(self, action, data):
        """Common dispatcher for sync actions."""
        if action == "playback_settings":
            self._apply_playback(data)
        elif action == "selection_changed":
            self._apply_selection(data)
        elif action == "annotation_stroke_release":
            self._apply_annotation(data)
        elif action == "insert_child":
            self._apply_insert(data)
        elif action == "set_property":
            rv.commands.redraw()

    def _rebuild_rv_session(self):
        """Clear and rebuild the RV session based on the current OTIO timeline."""
        print("[OTIOSync] Rebuilding RV session from OTIO snapshot...")
        # For this POC, we just add missing sources
        if not self.sync_manager.root_timeline: return
        
        sources = rv.commands.nodesOfType("RVFileSource")
        loaded_paths = set()
        for s in sources:
            try:
                loaded_paths.add(rv.commands.getStringProperty(f"{s}.media.movie")[0])
            except: pass

        for item in self.sync_manager.root_timeline.tracks:
            if item.name == "Media":
                for child in item:
                    if isinstance(child, otio.schema.Clip):
                        ref = child.media_reference
                        if isinstance(ref, otio.schema.ExternalReference) and ref.target_url:
                            if ref.target_url not in loaded_paths:
                                print(f"[OTIOSync] Loading source: {ref.target_url}")
                                rv.commands.addSource(ref.target_url)
            
            elif item.name and item.name.startswith("Annotations"):
                for child in item:
                    if isinstance(child, otio.schema.Clip):
                        if "annotation_commands" in child.metadata:
                            frame = child.metadata.get("rv_frame", 1)
                            node_name = child.metadata.get("annotated_clip_name", "unknown")
                            
                            # Group events by UUID
                            event_groups = {}
                            for event in child.metadata["annotation_commands"]:
                                if hasattr(event, "uuid"):
                                    if event.uuid not in event_groups:
                                        event_groups[event.uuid] = {"start": None, "points": None}
                                    if isinstance(event, otio.schemadef.SyncEvent.PaintStart):
                                        event_groups[event.uuid]["start"] = event
                                    elif isinstance(event, otio.schemadef.SyncEvent.PaintPoints):
                                        event_groups[event.uuid]["points"] = event
                            
                            for uuid, grp in event_groups.items():
                                start_event = grp["start"]
                                points_event = grp["points"]
                                if not start_event or not points_event: continue
                                
                                # Reconstruct the original 'data' dict format that _apply_annotation expects
                                data = {
                                    "frame": frame,
                                    "node_name": node_name,
                                    "color": list(start_event.rgba),
                                    "brush": start_event.brush,
                                    "width": list(points_event.points.size),
                                    "points": [val for pair in zip(points_event.points.x, points_event.points.y) for val in pair],
                                    "join": 3,
                                    "cap": 1
                                }
                                self._apply_annotation(data)
        
        rv.commands.redraw()

    # ------------------------------------------------------------------
    # RV Event Callbacks (Outgoing)
    # ------------------------------------------------------------------

    def _broadcast_playback(self):
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED: return
        fps = rv.commands.fps()
        current_frame = rv.commands.frame()
        try:
            looping = rv.commands.playMode() == 0
        except AttributeError:
            looping = True

        state = {
            "playing": rv.commands.isPlaying(),
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": float(current_frame),
                "rate": float(fps)
            },
            "looping": looping,
            "muted": False,
            "scrubbing": False
        }
        self.sync_manager.broadcast_playback_state(state)

    def on_rv_play_start(self, event):
        self._broadcast_playback()
        event.reject()

    def on_rv_play_stop(self, event):
        self._broadcast_playback()
        event.reject()

    def on_rv_frame_changed(self, event):
        if self._rv_updating: event.reject(); return
        current_frame = rv.commands.frame()
        if not rv.commands.isPlaying() and current_frame != self._last_broadcast_frame:
            self._broadcast_playback()
            self._last_broadcast_frame = current_frame
        event.reject()

    def on_rv_selection_changed(self, event):
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED: return
        selection = rv.commands.selection()
        if selection != self._last_selection:
            self.sync_manager.broadcast_selection(selection)
            self._last_selection = selection
        event.reject()

    def on_rv_graph_state_change(self, event):
        contents = event.contents()
        if self._rv_updating or not self.sync_manager or self.sync_manager.status != STATE_SYNCED:
            event.reject()
            return
        # Trigger on pen point changes: node.pen:N:F:user.points
        if ".pen:" in contents and contents.endswith(".points"):
            parts = contents.split(".")
            if len(parts) == 3:
                node_name, pen_component = parts[0], parts[1]
                if self._pending_stroke and self._pending_stroke[1] != pen_component:
                    if self._debounce_timer:
                        self._debounce_timer.stop()
                    self._flush_pending_stroke()
                self._pending_stroke = (node_name, pen_component)
                if self._debounce_timer is None:
                    self._debounce_timer = QtCore.QTimer()
                    self._debounce_timer.setSingleShot(True)
                    self._debounce_timer.timeout.connect(self._flush_pending_stroke)
                self._debounce_timer.start(150)
        event.reject()

    def _flush_pending_stroke(self):
        if not self._pending_stroke:
            return
        node_name, pen_component = self._pending_stroke
        self._pending_stroke = None
        self._broadcast_annotation(node_name, pen_component)

    def _broadcast_annotation(self, node_name, pen_component):
        try:
            full_prop = f"{node_name}.{pen_component}"
            points = rv.commands.getFloatProperty(f"{full_prop}.points")
            if not points:
                return
            color = rv.commands.getFloatProperty(f"{full_prop}.color")
            brush = rv.commands.getStringProperty(f"{full_prop}.brush")[0]
            width = rv.commands.getFloatProperty(f"{full_prop}.width")
            join = rv.commands.getIntProperty(f"{full_prop}.join")[0]
            cap = rv.commands.getIntProperty(f"{full_prop}.cap")[0]
            frame = int(pen_component.split(":")[2])
            
            import uuid
            try:
                otio.schema.schemadef.module_from_name('SyncEvent')
                penuuid = str(uuid.uuid4())
                start_event = otio.schemadef.SyncEvent.PaintStart(
                    brush=brush,
                    rgba=list(color),
                    friendly_name=pen_component.split(':')[-1],
                    uuid=penuuid
                )
                mode_prop = f"{full_prop}.mode"
                if rv.commands.propertyExists(mode_prop) and rv.commands.getIntProperty(mode_prop)[0] == 1:
                    start_event.type = 'erase'
                
                x = [i for i in points[::2]]
                y = [i for i in points[1::2]]
                if len(width) == 1:
                    w = [width[0]] * (len(points) // 2)
                else:
                    w = [i for i in width]
                p = otio.schemadef.SyncEvent.PaintVertices(x, y, w)
                points_event = otio.schemadef.SyncEvent.PaintPoints(uuid=penuuid, points=p)
                
                start_json = otio.adapters.write_to_string(start_event, "otio_json")
                points_json = otio.adapters.write_to_string(points_event, "otio_json")
            except Exception:
                start_json = None
                points_json = None

            source_group = node_name.split("_p_")[1] if "_p_" in node_name else None
            media_path = None
            if source_group:
                try:
                    nodes = rv.commands.nodesInGroup(source_group)
                    for n in nodes:
                        try:
                            media_path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                            if media_path:
                                break
                        except Exception:
                            pass
                except Exception:
                    pass

            data = {
                "media_path": media_path,
                "fps": rv.commands.fps(),
                "node_name": node_name,
                "frame": frame, "points": list(points), "color": list(color),
                "brush": brush, "width": list(width), "join": join, "cap": cap,
                "start_event_json": start_json,
                "points_event_json": points_json,
                "sync_timestamp": time.time()
            }
            self.sync_manager.broadcast_annotation(data)
        except Exception as e:
            print(f"[OTIOSync] Failed to broadcast annotation: {e}")

    def _apply_playback(self, data):
        playing = data.get("playing", False)
        current_time = data.get("current_time", {})
        target_frame = int(current_time.get("value", 1))
        if rv.commands.frame() != target_frame:
            rv.commands.setFrame(target_frame)
        is_playing = rv.commands.isPlaying()
        if playing and not is_playing:
            rv.commands.play()
        elif not playing and is_playing:
            rv.commands.stop()

    def _apply_selection(self, data):
        nodes = data.get("nodes", [])
        if nodes:
            rv.commands.setSelection(nodes)

    def _apply_annotation(self, data):
        try:
            frame = data.get("frame")
            points = data.get("points")
            color = data.get("color")
            brush = data.get("brush")
            width = data.get("width", [2.0])
            join = data.get("join", 3)
            cap = data.get("cap", 1)
            node_name = data.get("node_name")
            if node_name and rv.commands.nodeExists(node_name):
                node = node_name
            else:
                eval_infos = rv.commands.metaEvaluateClosestByType(frame, "RVPaint")
                if not eval_infos:
                    return
                node = eval_infos[0]['node']
            paint_prop = f"{node}.paint"
            next_id = rv.commands.getIntProperty(f"{paint_prop}.nextId")[0]
            pen_node = f"pen:{next_id}:{frame}:remote"
            full_pen = f"{node}.{pen_node}"
            order_prop = f"{node}.frame:{frame}.order"

            rv.commands.newProperty(f"{full_pen}.color", rv.commands.FloatType, 4)
            rv.commands.newProperty(f"{full_pen}.width", rv.commands.FloatType, 1)
            rv.commands.newProperty(f"{full_pen}.brush", rv.commands.StringType, 1)
            rv.commands.newProperty(f"{full_pen}.points", rv.commands.FloatType, 2)
            rv.commands.newProperty(f"{full_pen}.debug", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.join", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.cap", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.splat", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.startFrame", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.duration", rv.commands.IntType, 1)
            rv.commands.newProperty(f"{full_pen}.mode", rv.commands.IntType, 1)
            rv.commands.setIntProperty(f"{full_pen}.mode", [0], True)  # RenderOverMode = 0
            rv.commands.setIntProperty(f"{full_pen}.debug", [0], True)
            rv.commands.setIntProperty(f"{full_pen}.join", [join], True)
            rv.commands.setIntProperty(f"{full_pen}.cap", [cap], True)
            rv.commands.setIntProperty(f"{full_pen}.startFrame", [frame], True)
            rv.commands.setIntProperty(f"{full_pen}.duration", [1], True)
            rv.commands.setFloatProperty(f"{full_pen}.color", list(color), True)
            rv.commands.insertFloatProperty(f"{full_pen}.width", list(width))
            rv.commands.setStringProperty(f"{full_pen}.brush", [brush], True)
            rv.commands.setIntProperty(f"{full_pen}.splat", [1 if brush == "gauss" else 0], True)
            rv.commands.insertFloatProperty(f"{full_pen}.points", list(points))
            if not rv.commands.propertyExists(order_prop):
                rv.commands.newProperty(order_prop, rv.commands.StringType, 1)
            rv.commands.insertStringProperty(order_prop, [pen_node])
            rv.commands.setIntProperty(f"{paint_prop}.nextId", [next_id + 1], True)
            QtCore.QTimer.singleShot(0, rv.commands.redraw)
        except Exception as e:
            print(f"[OTIOSync] Failed to apply remote annotation: {e}")

    def _apply_insert(self, clip_obj):
        ref = clip_obj.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            rv.commands.addSource(ref.target_url)

    def do_add_clip(self, event=None):
        paths = rv.commands.openFileDialog(False, False, False, "mp4|Movie Files|mov|Movie Files|m4v|Movie Files|mkv|Movie Files|avi|Movie Files", "")
        if not paths: return
        path = paths[0] if isinstance(paths, (list, tuple)) else paths
        
        rv.commands.addSource(path)
        
        import opentimelineio.opentime as otio_time
        try:
            fps = rv.commands.fps()
            start = rv.commands.inPoint()
            end = rv.commands.outPoint()
            duration = end - start + 1
            if start > 0: start -= 1
            time_range = otio_time.TimeRange(otio_time.RationalTime(start, fps), otio_time.RationalTime(duration, fps))
        except Exception:
            time_range = otio_time.TimeRange(otio_time.RationalTime(0, 24), otio_time.RationalTime(10000, 24))
            
        clip = otio.schema.Clip(name=os.path.basename(path), media_reference=otio.schema.ExternalReference(target_url=path, available_range=time_range))
        self.sync_manager.insert_child(SYNC_DEMO_TRACK_UUID, clip)
        
        if event: event.reject()

    def do_show_status(self, event=None):
        if self.sync_manager:
            role = "MASTER" if self.sync_manager.is_master else "CLIENT"
            print(f"[OTIOSync] Session: {self.sync_manager.session_id} | Role: {role} | Status: {self.sync_manager.status}")
        if event: event.reject()

    def deactivate(self):
        if self._timer: self._timer.stop()
        if self.sync_manager: self.sync_manager.close()
        rv.rvtypes.MinorMode.deactivate(self)

def createMode():
    return OpenRVSyncPlugin()
