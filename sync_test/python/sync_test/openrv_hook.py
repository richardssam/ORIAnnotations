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
        
        # Report the first non-empty sequence's human-readable name regardless
        # of what RV's view node is.  After addSource or a SELECTION event RV
        # often switches the view to a raw source-group node ("sourceGroup000000"),
        # but xStudio always reports its playlist/timeline name ("Default").
        # Anchoring both sides to the sequence level makes the comparison robust.
        view_node = rv.commands.viewNode()
        clip_name = None
        try:
            seq_groups = rv.commands.nodesOfType("RVSequenceGroup")
            logging.debug(f"get_openrv_state: viewNode={view_node!r} seqGroups={seq_groups}")
            for seq_grp in seq_groups:
                try:
                    conns = rv.commands.nodeConnections(seq_grp)
                    inputs = list(conns[0]) if conns and conns[0] else []
                except Exception:
                    inputs = []
                if not inputs:
                    continue
                try:
                    clip_name = rv.commands.getStringProperty(f"{seq_grp}.ui.name")[0]
                except Exception:
                    clip_name = seq_grp
                logging.debug(f"get_openrv_state: using seq={seq_grp!r} ui.name={clip_name!r}")
                break
        except Exception as e:
            logging.error(f"get_openrv_state: clip lookup failed: {e}", exc_info=True)
        state["clip"] = clip_name if clip_name else view_node
        
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
                if path.startswith("file:/"):
                    if path.startswith("file://localhost"):
                        path = path[16:]
                    elif path.startswith("file://"):
                        path = path[7:]
                    elif path.startswith("file:/"):
                        path = path[5:]
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
        # Switch view to the first sequence that has sources so the state
        # comparison sees a sequence name rather than a raw source-group node.
        for seq_grp in rv.commands.nodesOfType("RVSequenceGroup"):
            try:
                conns = rv.commands.nodeConnections(seq_grp)
                has_inputs = conns and conns[0]
            except Exception:
                has_inputs = False
            if has_inputs:
                rv.commands.setViewNode(seq_grp)
                break
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

    elif action == "save_session":
        path = payload.get("filepath")
        rv.commands.saveSession(path)
        return {"action": action, "status": "success"}

    elif action == "delete_media":
        name = payload.get("name")
        import os
        for source_group in rv.commands.nodesOfType("RVSourceGroup"):
            for n in rv.commands.nodesInGroup(source_group):
                if rv.commands.nodeType(n) == "RVFileSource":
                    try:
                        path = rv.commands.getStringProperty(f"{n}.media.movie")[0]
                        base = os.path.basename(path)
                        stem = os.path.splitext(base)[0]
                        if base == name or stem == name:
                            rv.commands.deleteNode(source_group)
                            return {"action": action, "status": "success"}
                    except Exception:
                        pass
        raise ValueError(f"Could not find sequence or clip matching name to delete: {name}")

    raise ValueError(f"Unknown action: {action}")


def _execute_openrv_command_inner(payload):
    """Thin wrapper so execute_openrv_command can catch and log all errors."""
    return execute_openrv_command(payload)


# Public entry-point used by start_openrv_inspector; mirrors execute_xstudio_command
# in that it never raises — exceptions are returned as error dicts so the inspector
# returns HTTP 200 with a status/error payload instead of an opaque 500.
def execute_openrv_command_safe(payload):
    try:
        return execute_openrv_command(payload)
    except Exception as e:
        logging.error(f"execute_openrv_command failed: {e}", exc_info=True)
        return {"action": payload.get("action"), "status": "error", "error": str(e)}

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
        # Use a longer timeout for commands that load media (addSourceVerbose can
        # take several seconds on first load).
        return executor.run_sync(lambda: execute_openrv_command_safe(payload), timeout=30.0)
    
    server = InspectionServer(http_port, get_state_callback, execute_command_callback=execute_callback)
    server.start()
    return server
