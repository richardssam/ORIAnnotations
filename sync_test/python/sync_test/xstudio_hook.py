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
    # NOTE: timeline_to_otio_string is intentionally NOT used for /full_state — it
    # strips all metadata (no sync guids). Full state comes from the plugin's
    # manager.export_state() via the ORI_FULLSTATE_FILE bridge. See
    # docs/xstudio_constraints.md.
except ImportError:
    Connection = None

_global_conn = None

def _get_connection(port):
    global _global_conn
    if _global_conn is None:
        if Connection is None:
            raise RuntimeError("xstudio python API not found.")
        conn = Connection(auto_connect=False)
        conn.connect_remote("127.0.0.1", port)
        _global_conn = conn
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
            
            # Check if media exists via active playhead.
            # Default True: "unknown" is not the same as "missing". We only set
            # False when we have the path and can confirm the file is absent.
            # In xs_flat_playlist mode the container is a Playlist and
            # on_screen_media may not expose a single source, which previously
            # left media_exists=False and caused compare_states to fail for 10s.
            state["media_path"] = None
            state["media_exists"] = True
            try:
                from xstudio.core import get_global_playhead_events_atom, viewport_playhead_atom
                from xstudio.api.session.playhead import Playhead
                gphev = conn.request_receive(conn.remote(), get_global_playhead_events_atom())[0]
                ph_actor = conn.request_receive(gphev, viewport_playhead_atom())[0]
                if ph_actor:
                    ph = Playhead(conn, ph_actor)
                    # Report the actual playhead frame so frame checkpoints can
                    # validate xStudio too (was hard-coded None, which made every
                    # frame check silently skip xStudio).
                    try:
                        state["frame"] = ph.position
                    except Exception:
                        pass
                    ms = ph.on_screen_media
                    if ms:
                        ms_src = ms.media_source()
                        if ms_src and ms_src.media_reference:
                            uri_str = str(ms_src.media_reference.uri())
                            state["media_path"] = uri_str
                            if uri_str.startswith("file:/"):
                                local_path = uri_str
                                if local_path.startswith("file://localhost"):
                                    local_path = local_path[16:]
                                elif local_path.startswith("file://"):
                                    local_path = local_path[7:]
                                elif local_path.startswith("file:/"):
                                    local_path = local_path[5:]
                                state["media_exists"] = os.path.exists(local_path)
                            else:
                                state["media_exists"] = os.path.exists(uri_str)
            except Exception as e:
                logging.debug(f"Could not check playhead media: {e}")
            
        except Exception as e:
            logging.debug(f"Could not read container: {e}")
            # Fallback: if the viewport has no active container (e.g. media was
            # added via sync without triggering a selection), return the first
            # playlist name so the state comparison has something to work with.
            try:
                playlists = session.playlists
                if playlists:
                    state["clip"] = playlists[0].name
            except Exception:
                pass

        # 3. Annotations — enumerate ALL session bookmarks globally instead of
        # filtering media by the viewed container's name.  When a Timeline is on
        # screen, state["clip"] is the timeline name ("Default Sequence") while
        # the annotations live on bookmarks owned by the timeline clip's media
        # actor (not the bin media), so a media-name filter misses them entirely.
        try:
            for bm in session.bookmarks.bookmarks:
                ann_state = {"strokes": 0, "captions": 0}
                try:
                    detail = conn.request_receive_timeout(
                        200, bm.remote, bookmark_detail_atom())[0]
                    ann_state["start"] = detail.start.frames
                    ann_state["duration"] = detail.duration.frames
                except Exception:
                    pass
                try:
                    ann = bm.annotation_data
                    if ann:
                        data = ann.get("Data", {})
                        ann_state["strokes"] = len(data.get("pen_strokes", []))
                        ann_state["captions"] = len(data.get("captions", []))
                except Exception:
                    pass
                state["annotations"].append(ann_state)
        except Exception as e:
            logging.debug(f"Could not read annotations: {e}")

    except Exception as e:
        logging.error(f"Error in get_xstudio_state: {e}")

    # Comparable stroke/caption count so the runner can assert annotations were
    # created (mirrors the OpenRV inspector's annotation_count).
    state["annotation_count"] = sum(
        a.get("strokes", 0) + a.get("captions", 0) for a in state["annotations"]
    )

    return state

def get_xstudio_full_state(port=14441):
    """Return xStudio's reduced state as a StateSnapshot-shaped dict.

    Read from the file the in-process plugin writes (``ORI_FULLSTATE_FILE``),
    which is ``manager.export_state()`` — guid-accurate, keyed by the real sync
    guids. This out-of-process inspector cannot reach the plugin's manager, and
    ``timeline_to_otio_string`` strips all sync metadata, so reconstructing here
    is impossible; the file bridge is the source of truth.
    """
    path = os.environ.get("ORI_FULLSTATE_FILE")
    if not path:
        return {"error": "ORI_FULLSTATE_FILE not set"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"error": "full-state file not yet written by xStudio plugin"}
    except Exception as e:
        return {"error": str(e)}


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
            conn.api.session.set_on_screen_source(pl)
            return {"action": action, "status": "success"}
            
        elif action == "set_selection":
            name = payload.get("name")
            
            # Check playlists
            for i, pl in enumerate(conn.api.session.playlists):
                if pl.name == name or (name in ["Default Sequence", "Sequence", "Default"] and i == 0):
                    conn.api.session.set_on_screen_source(pl)
                    return {"action": action, "status": "success"}
                    
                # Check media (clip)
                for m in pl.media:
                    import os
                    if m.name == name or os.path.basename(m.name) == name:
                        conn.api.session.set_on_screen_source(m)
                        return {"action": action, "status": "success"}
                        
            raise ValueError(f"Could not find sequence or media matching name: {name}")

        elif action == "save_session":
            path = payload.get("filepath")
            if not path.startswith("file://"):
                path = "file://" + path
            from xstudio.core import URI
            conn.api.session.save_as(URI(path))
            return {"action": action, "status": "success"}

        elif action == "delete_media":
            name = payload.get("name")
            for pl in conn.api.session.playlists:
                for m in pl.media:
                    import os
                    if m.name == name or os.path.basename(m.name) == name:
                        pl.remove_media(m)
                        return {"action": action, "status": "success"}
            raise ValueError(f"Could not find media matching name: {name}")

        elif action == "export_otio":
            # Export the on-screen timeline's OTIO. xStudio's to_otio_string()
            # drops metadata, but the sync test compares only the (guid-free)
            # cut structure, so that is fine here.
            from xstudio.api.session.playlist import Timeline
            path = payload.get("filepath")
            timeline = None
            container = conn.api.session.viewed_container
            if isinstance(container, Timeline):
                timeline = container
            else:
                # Fall back to the first timeline found on any playlist.
                for pl in conn.api.session.playlists:
                    for child in getattr(pl, "timelines", None) or []:
                        timeline = child
                        break
                    if timeline:
                        break
            if timeline is None:
                raise ValueError("No timeline available to export")
            otio_str = timeline.to_otio_string()
            with open(path, "w") as f:
                f.write(otio_str)
            return {"action": action, "status": "success", "filepath": path}

        raise ValueError(f"Unknown action: {action}")
    except Exception as e:
        logging.error(f"Error in execute_xstudio_command: {e}")
        return {"action": payload.get("action"), "status": "error", "error": str(e)}

def start_xstudio_inspector(http_port, xstudio_port):
    def get_state_callback():
        return get_xstudio_state(xstudio_port)

    def get_full_state_callback():
        return get_xstudio_full_state(xstudio_port)

    def execute_callback(payload):
        return execute_xstudio_command(payload, xstudio_port)

    server = InspectionServer(
        http_port,
        get_state_callback,
        execute_command_callback=execute_callback,
        get_full_state_callback=get_full_state_callback,
    )
    server.start()
    return server
