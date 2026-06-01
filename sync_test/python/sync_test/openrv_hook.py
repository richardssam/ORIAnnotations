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

def start_openrv_inspector(http_port):
    def callback():
        return get_openrv_state()
    
    server = InspectionServer(http_port, callback)
    server.start()
    return server
