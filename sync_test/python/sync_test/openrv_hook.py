import json
import logging
from .inspector import InspectionServer

try:
    import rv.commands
    import rv.extra_commands
except ImportError:
    rv = None

def get_openrv_state():
    if rv is None:
        raise RuntimeError("rv python API not found. This script must be run inside OpenRV.")

    state = {
        "clip": None,
        "frame": None,
        "playing": False,
        "annotations": []
    }

    try:
        state["frame"] = rv.commands.frame()
        state["playing"] = rv.commands.isPlaying()
        
        # Get the currently viewed node (could be a sequence or a source)
        view_node = rv.commands.viewNode()
        state["clip"] = view_node
        
        state["media_path"] = None
        state["media_exists"] = False
        
        # Check if media actually exists on disk
        import os
        sources = rv.commands.sourcesAtFrame(rv.commands.frame())
        if sources:
            media_info = rv.commands.sourceMedia(sources[0])
            if media_info and len(media_info) > 0:
                path = media_info[0]
                state["media_path"] = path
                if path.startswith("file://"):
                    path = path[7:]
                state["media_exists"] = os.path.exists(path)
        else:
            # If no sources, we don't treat media as "missing" (just empty)
            state["media_exists"] = True
        
        # To get annotations, we check for RVPaint nodes in the graph
        paint_nodes = rv.commands.nodesOfType("RVPaint")
        total_strokes = 0
        for pnode in paint_nodes:
            # We can inspect properties of the paint node if needed
            # For now, just count if it exists and has strokes
            pass
            
        # Simplified annotation state for OpenRV for now
        # You'd expand this based on the exact representation of OpenRV annotations
        # expected by your tests.

    except Exception as e:
        logging.error(f"Error getting openrv state: {e}")

    return state

def execute_openrv_command(payload):
    if rv is None:
        raise RuntimeError("rv python API not found.")
        
    action = payload.get("action")
    if action == "add_media":
        url = payload.get("url")
        rv.commands.addSourceVerbose([url])
        return {"action": action, "status": "success"}
        
    elif action == "set_selection":
        name = payload.get("name")
        # First check sequences
        seq_groups = rv.commands.nodesOfType("RVSequenceGroup")
        for seq_group in seq_groups:
            try:
                seq_name = rv.commands.getStringProperty(f"{seq_group}.ui.name")[0]
                if seq_name == name or name in ["Default Sequence", "Sequence", "Default"]:
                    rv.commands.setViewNode(seq_group)
                    return {"action": action, "status": "success"}
            except Exception:
                if seq_group == name or name in ["Default Sequence", "Sequence", "Default"]:
                    rv.commands.setViewNode(seq_group)
                    return {"action": action, "status": "success"}
                    
        # Then check individual clips (sources)
        import os
        for source_group in rv.commands.nodesOfType("RVSourceGroup"):
            for n in rv.commands.nodesInGroup(source_group):
                if rv.commands.nodeType(n) == "RVFileSource":
                    try:
                        path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                        base = os.path.basename(path)
                        stem = os.path.splitext(base)[0]
                        if base == name or stem == name:
                            rv.commands.setViewNode(source_group)
                            return {"action": action, "status": "success"}
                    except Exception:
                        pass
                        
        raise ValueError(f"Could not find sequence or clip matching name: {name}")

    raise ValueError(f"Unknown action: {action}")

def start_openrv_inspector(http_port):
    import queue
    try:
        from PySide6 import QtCore
    except ImportError:
        from PySide2 import QtCore

    class MainThreadExecutor(QtCore.QObject):
        execute_signal = QtCore.Signal(object)
        
        def __init__(self):
            super().__init__()
            self.execute_signal.connect(self.execute)
            self.q = queue.Queue()
            
        def execute(self, func):
            try:
                res = func()
                self.q.put(("ok", res))
            except Exception as e:
                self.q.put(("error", e))
                
        def run_sync(self, func, timeout=5.0):
            self.execute_signal.emit(func)
            try:
                status, res = self.q.get(timeout=timeout)
                if status == "error":
                    raise res
                return res
            except queue.Empty:
                raise RuntimeError("Timeout waiting for main thread execution")

    # This is called from the main thread when the plugin loads/rvpush executes
    executor = MainThreadExecutor()

    def get_state_callback():
        return executor.run_sync(get_openrv_state)
        
    def execute_callback(payload):
        return executor.run_sync(lambda: execute_openrv_command(payload))
    
    server = InspectionServer(http_port, get_state_callback, execute_command_callback=execute_callback)
    server.start()
    return server
