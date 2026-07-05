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
    from xstudio.core import viewport_active_media_container_atom, viewport_playhead_atom, bookmark_detail_atom, BookmarkDetail
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
        "annotations": [],
        "is_master": None,
    }

    try:
        conn = _get_connection(port)

        # Best-effort: read is_master from the in-process plugin's full-state
        # file bridge (ORI_FULLSTATE_FILE). This out-of-process hook cannot
        # reach the plugin's SyncManager directly, but export_state() already
        # writes is_master into that file (see manager.py) for exactly this
        # kind of harness visibility.
        try:
            full = get_xstudio_full_state(port)
            if isinstance(full, dict) and "is_master" in full:
                state["is_master"] = full["is_master"]
        except Exception:
            pass
        
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
                        pen_strokes = data.get("pen_strokes", [])
                        ann_state["strokes"] = len(pen_strokes)
                        ann_state["captions"] = len(data.get("captions", []))
                        # Surface native geometry per stroke, additive to the
                        # counts above — lets callers assert on drawn/received
                        # geometry (e.g. round-tripped pen width), not just
                        # presence.
                        ann_state["stroke_thickness"] = [
                            s.get("thickness") for s in pen_strokes
                        ]
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

        elif action == "draw_annotation":
            kind = payload.get("kind")
            if kind != "pen":
                raise ValueError(
                    f"draw_annotation: kind {kind!r} not supported for xStudio — "
                    "xStudio has no wired-up native shape broadcast path yet"
                )
            return _draw_xstudio_annotation(conn, payload)

        elif action == "capture_frame":
            return _capture_xstudio_frame(conn, payload)

        raise ValueError(f"Unknown action: {action}")
    except Exception as e:
        logging.error(f"Error in execute_xstudio_command: {e}")
        return {"action": payload.get("action"), "status": "error", "error": str(e)}


def _draw_xstudio_annotation(conn, payload):
    """Simulate a completed native pen draw by writing a real bookmark.

    Writes directly via the same remote annotation API (``Bookmark.set_annotation``)
    the running plugin itself uses on the receive side — no new xStudio-plugin
    code is involved. Because this harness connection and the plugin's own
    connection share the same live session, the plugin's own poll loop
    (``flush_pending_annotations``) will discover this bookmark and broadcast it
    exactly as it would a real user-drawn stroke. The caller is responsible for
    waiting for that convergence (bounded by the plugin's debounce/scan-interval
    constants) — this function only performs the write.
    """
    import datetime
    from xstudio.core import get_global_playhead_events_atom, viewport_playhead_atom

    thickness = float(payload.get("thickness", 0.01))
    color = payload.get("color", [1.0, 1.0, 1.0, 1.0])
    colour = [float(c) for c in color[:3]]
    opacity = float(color[3]) if len(color) > 3 else 1.0

    gphev = conn.request_receive(conn.remote(), get_global_playhead_events_atom())[0]
    ph_actor = conn.request_receive(gphev, viewport_playhead_atom())[0]
    if not ph_actor:
        raise RuntimeError("draw_annotation: no active playhead")
    ph = Playhead(conn, ph_actor)
    media = ph.on_screen_media
    if media is None:
        raise RuntimeError("draw_annotation: no on-screen media at current playhead")
    frame = ph.position or 0
    fps = 25.0
    try:
        fps = ph.frame_rate.fps() or fps
    except Exception:
        pass

    stroke = {
        "colour": colour,
        # Legacy r/g/b keys alongside "colour", matching the production
        # xs_annotation_codec.sync_events_to_xs_strokes stroke shape — some
        # xStudio versions read these instead of "colour". Omitting them (as
        # this harness originally did) rendered the stroke as plain white
        # regardless of the requested colour, a real bug the frame-capture
        # visual check caught (the numeric round-trip check only asserts
        # thickness, never colour).
        "r": colour[0],
        "g": colour[1],
        "b": colour[2],
        "opacity": opacity,
        # Raw native thickness — the value under test, deliberately not
        # derived from any OTIO size.
        "thickness": thickness,
        "softness": 0.0,
        "size_sensitivity": 1.0,
        "opacity_sensitivity": 1.0,
        # "Brush" is not a recognised stroke type in the production codec
        # (which uses "color"/"erase"); it silently fell back to a default
        # render, part of the same bug as the missing r/g/b keys above.
        "type": "color",
        "is_erase_stroke": False,
        # Two points, flat [x, y, pressure, opacity] quads in xStudio-native
        # (W-normalised, Y-down) space.
        "points": [-0.05, 0.0, 1.0, 1.0, 0.05, 0.0, 1.0, 1.0],
    }

    bm = conn.api.session.bookmarks.add_bookmark(target=media)
    detail = BookmarkDetail()
    detail.start = datetime.timedelta(seconds=frame / fps)
    detail.duration = datetime.timedelta(seconds=0)
    conn.request_receive(bm.remote, bookmark_detail_atom(), detail)
    bm.set_annotation(strokes=[stroke], captions=[])

    return {
        "action": "draw_annotation",
        "status": "success",
        "bookmark_uuid": str(bm.uuid),
    }


def _capture_xstudio_frame(conn, payload):
    """Render the current on-screen frame (video + annotation) to an image.

    Resolves the bookmark at the current playhead's media/frame using the same
    global bookmark enumeration ``get_xstudio_state`` uses, then renders via
    ``OffscreenViewport.render_bookmark_with_transparency`` with an explicit
    width/height (design D1) so the output resolution is a known, comparable
    quantity — the comparison step still reads the file's actual dimensions
    back rather than trusting this request was honored exactly.
    """
    from xstudio.api.intrinsic.viewport import OffscreenViewport
    from xstudio.core import get_global_playhead_events_atom, viewport_playhead_atom

    output_path = payload.get("output_path")
    if not output_path:
        raise ValueError("capture_frame: output_path is required")
    width = int(payload.get("width", 1920))
    height = int(payload.get("height", 1080))

    gphev = conn.request_receive(conn.remote(), get_global_playhead_events_atom())[0]
    ph_actor = conn.request_receive(gphev, viewport_playhead_atom())[0]
    if not ph_actor:
        raise RuntimeError("capture_frame: no active playhead")
    ph = Playhead(conn, ph_actor)
    media = ph.on_screen_media
    if media is None:
        raise RuntimeError("capture_frame: no on-screen media at current playhead")
    frame = ph.position or 0

    target_bookmark = None
    for bm in conn.api.session.bookmarks.bookmarks:
        detail = bm.detail
        if not detail.owner or not detail.owner.actor:
            continue
        if str(detail.owner.uuid) != str(media.uuid):
            continue
        try:
            seconds_per_frame = media.media_source().rate.seconds()
            bm_frame = round(detail.start.total_seconds() / seconds_per_frame)
            bm_duration_frames = round(detail.duration.total_seconds() / seconds_per_frame)
        except Exception:
            bm_frame, bm_duration_frames = 0, 0
        if bm_frame <= frame <= bm_frame + max(bm_duration_frames, 0):
            target_bookmark = bm
            break

    if target_bookmark is None:
        raise RuntimeError(
            f"capture_frame: no bookmark found at frame {frame} for the on-screen media"
        )
    logging.info(
        f"capture_frame: matched bookmark {target_bookmark.uuid} at frame {frame} "
        f"(has_annotation={target_bookmark.has_annotation})"
    )

    viewport = OffscreenViewport(conn)
    viewport.render_bookmark_with_transparency(
        output_path, target_bookmark.uuid,
        include_image=True, include_drawings=True,
        width=width, height=height,
    )
    return {"action": "capture_frame", "status": "success", "output_path": output_path}


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
