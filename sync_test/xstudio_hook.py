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

def get_xstudio_state(port=14441):
    if Connection is None:
        raise RuntimeError("xstudio python API not found.")

    state = {
        "clip": None,
        "frame": None,
        "playing": False,
        "annotations": []
    }

    conn = Connection(auto_connect=False)
    try:
        conn.connect_remote("127.0.0.1", port)
        
        # 1. Playhead Status
        try:
            vp = Viewport(conn, active_viewport=True)
            ph_actor = conn.request_receive_timeout(
                100, vp.remote, viewport_playhead_atom()
            )[0]
            ph = Playhead(conn, ph_actor)
            state["frame"] = ph.position.frames
            state["playing"] = ph.playing
        except Exception as e:
            logging.debug(f"Could not read playhead: {e}")

        # 2. Viewed Container / Clip
        session = conn.api.session
        try:
            session_actor = session.remote
            result = conn.request_receive_timeout(
                100, session_actor, viewport_active_media_container_atom()
            )[0]
            c = Container(conn, result.actor)
            state["clip"] = c.name
        except Exception as e:
            logging.debug(f"Could not read container: {e}")

        # 3. Annotations
        try:
            # We assume annotations are on the viewed media's bookmarks
            # To be precise, we check playlists for the media matching the viewed clip
            for pl in session.playlists:
                for m in pl.media:
                    if m.name == state["clip"]:
                        for bm in m.ordered_bookmarks():
                            detail = conn.request_receive(bm.remote, bookmark_detail_atom())[0]
                            ann = bm.annotation_data
                            ann_state = {
                                "start": detail.start.frames,
                                "duration": detail.duration.frames,
                                "strokes": 0,
                                "captions": 0
                            }
                            if ann:
                                data = ann.get("Data", {})
                                ann_state["strokes"] = len(data.get("pen_strokes", []))
                                ann_state["captions"] = len(data.get("captions", []))
                            state["annotations"].append(ann_state)
        except Exception as e:
            logging.debug(f"Could not read annotations: {e}")

    finally:
        conn.disconnect()

    return state

def start_xstudio_inspector(http_port, xstudio_port):
    def callback():
        return get_xstudio_state(xstudio_port)
    
    server = InspectionServer(http_port, callback)
    server.start()
    return server
