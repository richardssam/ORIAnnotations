import os
import sys
import json
import logging
from .inspector import InspectionServer

# Ensure the embedded xStudio python framework packages are accessible
xstudio_site_packages = "/Users/sam/git/xstudio/build/xSTUDIO.app/Contents/Frameworks/lib/python3.11/site-packages"
if os.path.exists(xstudio_site_packages) and xstudio_site_packages not in sys.path:
    sys.path.insert(0, xstudio_site_packages)

try:
    from xstudio.connection import Connection
    from xstudio.api.session.playlist.timeline import Timeline
    from xstudio.core import viewport_active_media_container_atom, viewport_playhead_atom, bookmark_detail_atom
    from xstudio.api.session.container import Container
    from xstudio.api.intrinsic.viewport import Viewport
    from xstudio.api.session.playhead import Playhead
except ImportError:
    Connection = None

_global_conn = None

def _get_connection(port):
    global _global_conn
    if _global_conn is None:
        if Connection is None:
            raise RuntimeError("xstudio python API not found.")
        _global_conn = Connection(auto_connect=False)
        _global_conn.connect_remote("127.0.0.1", port)
    return _global_conn

def get_xstudio_state(port=14441):
    state = {
        "clip": None,
        "frame": None,
        "playing": False,
        "annotations": []
    }

    try:
        conn = _get_connection(port)
        
        # 1. Playhead Status
        state["frame"] = None
        state["playing"] = False

        # 2. Viewed Container / Clip
        session = conn.api.session
        try:
            session_actor = session.remote
            result = conn.request_receive_timeout(
                100, session_actor, viewport_active_media_container_atom()
            )[0]
            c = Container(conn, result.actor)
            logging.info(f"Container type: {c.type}")
            if c.type == "Timeline":
                from xstudio.api.session.playlist.timeline import Timeline
                t = Timeline(conn, result.actor)
                state["clip"] = t.name
                logging.info(f"Timeline name: {t.name}")
            else:
                state["clip"] = c.name
                logging.info(f"Container name: {c.name}")
            
            # Check if media exists
            state["media_path"] = None
            state["media_exists"] = True # Default to True so we don't fail if we can't find it
            
        except Exception as e:
            logging.debug(f"Could not read container: {e}")

        # 3. Annotations
        try:
            for pl in session.playlists:
                for m in pl.media:
                    if m.name == state["clip"]:
                        for bm in m.ordered_bookmarks():
                            try:
                                detail = conn.request_receive_timeout(200, bm.remote, bookmark_detail_atom())[0]
                                b_uuid = str(detail.uuid)
                            except Exception:
                                continue
                            ann_state = {
                                "start": detail.start.frames,
                                "duration": detail.duration.frames,
                                "strokes": 0,
                                "captions": 0
                            }
                            ann = bm.annotation_data
                            if ann:
                                data = ann.get("Data", {})
                                ann_state["strokes"] = len(data.get("pen_strokes", []))
                                ann_state["captions"] = len(data.get("captions", []))
                            state["annotations"].append(ann_state)
        except Exception as e:
            logging.debug(f"Could not read annotations: {e}")

    except Exception as e:
        logging.error(f"Error in get_xstudio_state: {e}")

    return state

def execute_xstudio_command(payload, port):
    try:
        conn = _get_connection(port)
        action = payload.get("action")
        
        if action == "add_media":
            url = payload.get("url")
            playlists = conn.api.session.playlists
            if not playlists:
                pl = conn.api.session.create_playlist("Default Sequence")[1]
            else:
                pl = playlists[0]
            pl.add_media(url)
            return {"action": action, "status": "success"}
            
        elif action == "set_selection":
            name = payload.get("name")
            
            # Check playlists
            for i, pl in enumerate(conn.api.session.playlists):
                if pl.name == name or (name in ["Default Sequence", "Sequence"] and i == 0):
                    conn.api.session.set_on_screen_source(pl)
                    return {"action": action, "status": "success"}
                    
                # Check media (clip)
                for m in pl.media:
                    import os
                    if m.name == name or os.path.basename(m.name) == name:
                        conn.api.session.set_on_screen_source(m)
                        return {"action": action, "status": "success"}
                        
            raise ValueError(f"Could not find sequence or media matching name: {name}")

        raise ValueError(f"Unknown action: {action}")
    except Exception as e:
        logging.error(f"Error in execute_xstudio_command: {e}")
        return {"action": payload.get("action"), "status": "error", "error": str(e)}

def start_xstudio_inspector(http_port, xstudio_port):
    def get_state_callback():
        return get_xstudio_state(xstudio_port)
        
    def execute_callback(payload):
        return execute_xstudio_command(payload, xstudio_port)
    
    server = InspectionServer(http_port, get_state_callback, execute_command_callback=execute_callback)
    server.start()
    return server
