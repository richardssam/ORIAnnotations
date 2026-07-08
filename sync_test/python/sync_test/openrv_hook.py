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
        "annotations": [],
        "is_master": None,
    }

    try:
        try:
            import otio_sync_core
            _mgr_for_master = otio_sync_core.get_registered_manager()
            if _mgr_for_master is not None:
                state["is_master"] = bool(_mgr_for_master.is_master)
        except Exception:
            pass
        # Report the 1-indexed LOCAL frame (frame - frameStart + 1) rather than
        # the raw global frame so timecode-bearing media works correctly.
        # validate_checkpoint expects adjusted = protocol_value + 1, where
        # protocol_value is 0-indexed (xStudio's position). For non-timecode
        # media frameStart()=1 so this equals frame(); for timecode media
        # (e.g. laser_ACES_sRGB.mov with embedded TC ≈91699) it strips the
        # timecode base and gives the same 1-indexed local frame the protocol
        # value implies.
        try:
            state["frame"] = rv.commands.frame() - rv.commands.frameStart() + 1
        except Exception:
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

        # Prefer the synced active timeline name from the manager: the
        # "first non-empty sequence" heuristic above is ambiguous once more than
        # one sequence exists (it can report a non-active sequence's name, e.g.
        # "Sequence of graphic" while RV is actually viewing "Default Sequence").
        # The manager's active_timeline_guid is the authoritative synced selection
        # and matches what RV's viewport shows.
        try:
            import otio_sync_core
            _mgr = otio_sync_core.get_registered_manager()
            _atl_guid = getattr(_mgr, "active_timeline_guid", None) if _mgr else None
            if _atl_guid:
                _atl = _mgr._timelines.get(_atl_guid)
                # If the active timeline is a single-clip-view timeline, report
                # the containing sequence's name instead — peers differ on view
                # mode (single-clip vs sequence) but share the same sequence, so
                # this keeps compare_states consistent with the structural
                # projection (which resolves clip-timelines to their sequence).
                if _atl is not None:
                    _ctf = _atl.metadata.get("clip_timeline_for")
                    if _ctf:
                        for _g, _tl in _mgr._timelines.items():
                            if _tl.metadata.get("clip_timeline_for"):
                                continue
                            if any(
                                getattr(c, "metadata", {}).get("sync", {}).get("guid") == _ctf
                                for trk in _tl.tracks for c in trk
                            ):
                                _atl = _tl
                                break
                    if getattr(_atl, "name", None):
                        state["clip"] = _atl.name
        except Exception:
            pass

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
        
        # Count painted strokes across all RVPaint nodes. A stroke is a
        # component named "pen:..." or "text:..."; the leaf properties under it
        # (e.g. ".points") share that component prefix, so we de-dupe to the
        # component path.  This lets the test assert annotations were *created*
        # (placement/frame correctness is validated separately).
        total_strokes = 0
        _GEOMETRY_PREFIXES = ("pen:", "text:", "rect:", "ellipse:", "arrow:")
        for pnode in rv.commands.nodesOfType("RVPaint"):
            try:
                comps = set()
                geom_comps = set()
                for prop in rv.commands.properties(pnode):
                    short = prop[len(pnode) + 1:] if prop.startswith(pnode + ".") else prop
                    if short.startswith("pen:") or short.startswith("text:"):
                        comps.add(short.rsplit(".", 1)[0])
                    if short.startswith(_GEOMETRY_PREFIXES):
                        geom_comps.add(short.rsplit(".", 1)[0])
                total_strokes += len(comps)

                # Surface native geometry (width for pen/erase, size for
                # shapes) per stroke, additive to the existing counts above —
                # lets callers assert on drawn/received geometry, not just
                # presence. Reuses the already-tested read_stroke rather than
                # re-parsing properties here.
                try:
                    from otio_sync_core import rv_paint_applier
                    for comp in geom_comps:
                        try:
                            stroke = rv_paint_applier.read_stroke(rv.commands, pnode, comp)
                        except Exception:
                            stroke = None
                        if not stroke:
                            continue
                        ann_entry = {"node": pnode, "component": comp, "kind": stroke.get("kind")}
                        if "width" in stroke:
                            ann_entry["width"] = stroke["width"]
                        if "size" in stroke:
                            ann_entry["size"] = stroke["size"]
                        state["annotations"].append(ann_entry)
                except ImportError:
                    pass
            except Exception:
                continue
        state["annotation_count"] = total_strokes

    except Exception as e:
        logging.error(f"Error getting openrv state: {e}")

    return state

def get_openrv_full_state():
    """Return the sync plugin's reduced state as a StateSnapshot-shaped dict.

    Sources directly from the in-process ``SyncManager`` the plugin registered
    (``manager.export_state()``) so the structure, GUIDs and frame match the
    recorded master snapshot exactly.  Returns an ``{"error": ...}`` dict if the
    manager has not registered yet.
    """
    import otio_sync_core
    manager = otio_sync_core.get_registered_manager()
    if manager is None:
        return {"error": "sync manager not registered yet"}
    return manager.export_state()


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

    elif action == "export_otio":
        # Export the current OTIO timeline via RV's native otio_writer so the
        # sync test can compare it (guid/path-tolerant) against the reference.
        import otio_writer
        import opentimelineio as otio
        path = payload.get("filepath")
        # Prefer an OTIO-imported Stack (the `tracks` RVStackGroup, marked with an
        # otio.* component); fall back to the current view node.
        markers = ("otio.timeline_name", "otio.timeline_metadata", "otio.metadata")
        root = None
        for n in rv.commands.nodesOfType("RVStackGroup"):
            if n == "defaultStack":
                continue
            if any(rv.commands.propertyExists(f"{n}.{p}") for p in markers):
                root = n
                break
        if root is None:
            root = rv.commands.viewNode()
        timeline = otio_writer.create_timeline_from_node(root)
        otio.adapters.write_to_file(timeline, path)
        return {"action": action, "status": "success", "root": root, "filepath": path}

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

    elif action == "draw_annotation":
        return _draw_openrv_annotation(payload)

    elif action == "capture_frame":
        return _capture_openrv_frame(payload)

    raise ValueError(f"Unknown action: {action}")


def _draw_openrv_annotation(payload):
    """Simulate a completed native draw and broadcast it via the real send path.

    Writes raw paint-node properties directly (NOT via ``rv_paint_applier``'s
    caller, ``sync_events_to_rv_specs`` — that would re-apply the forward
    OTIO->RV width scale we're trying to test the *reverse* of) at the current
    frame's paint node, then calls the real ``AnnotationSyncController._broadcast_annotation``
    — the same function OpenRV's own pen-up handler calls — so the annotation
    is broadcast exactly as a live user stroke would be.
    """
    import uuid as uuid_mod
    import otio_sync_core
    from otio_sync_core import rv_paint_applier
    from otio_sync_core.rv_annotation_codec import TYPE_STRING, TYPE_FLOAT, TYPE_INT

    kind = payload.get("kind")
    frame = rv.commands.frame()
    eval_infos = rv.commands.metaEvaluateClosestByType(frame, "RVPaint")
    if not eval_infos:
        raise ValueError(f"draw_annotation: no RVPaint node found at frame {frame}")
    node = eval_infos[0]["node"]

    if kind == "pen":
        width = float(payload.get("width", 2.0))
        points = payload.get("points", [-0.05, 0.0, 0.05, 0.0])
        color = payload.get("color", [1.0, 1.0, 1.0, 1.0])
        spec = {
            "kind": "pen",
            "uuid": str(uuid_mod.uuid4()),
            "user": "sync_test",
            "props": [
                ("brush", TYPE_STRING, ["circle"], 1),
                ("color", TYPE_FLOAT, list(color), 4),
                ("debug", TYPE_INT, [0], 1),
                ("join", TYPE_INT, [3], 1),
                ("cap", TYPE_INT, [1], 1),
                ("splat", TYPE_INT, [0], 1),
                # Raw native width — the value under test, deliberately NOT
                # derived from any OTIO size via RV_WIDTH_SCALE.
                ("width", TYPE_FLOAT, [width, width], 1),
                ("points", TYPE_FLOAT, list(points), 2),
            ],
        }
    elif kind in ("rect", "ellipse"):
        border_width = float(payload.get("border_width", 1.0))
        border_rgba = payload.get("border_rgba", [1.0, 1.0, 1.0, 1.0])
        inner_rgba = payload.get("inner_rgba", [0.0, 0.0, 0.0, 0.0])
        spec = {
            "kind": kind,
            "uuid": str(uuid_mod.uuid4()),
            "user": "sync_test",
            "props": [
                ("min", TYPE_FLOAT, [-0.1, -0.1], 2),
                ("max", TYPE_FLOAT, [0.1, 0.1], 2),
                ("borderColor", TYPE_FLOAT, list(border_rgba), 4),
                ("innerColor", TYPE_FLOAT, list(inner_rgba), 4),
                # Raw native border width — the value under test.
                ("borderWidth", TYPE_FLOAT, [border_width], 1),
                ("startFrame", TYPE_INT, [frame], 1),
                ("duration", TYPE_INT, [1], 1),
                ("eye", TYPE_INT, [2], 1),
                ("uuid", TYPE_STRING, [str(uuid_mod.uuid4())], 1),
                ("softDeleted", TYPE_INT, [0], 1),
            ],
        }
    elif kind == "text":
        # Raw native fontSize (WCS fraction of image height) — the value
        # under test, deliberately NOT derived from any OTIO font_size via
        # font_size_to_rv (that's the forward conversion; this harness tests
        # the reverse of it, same convention as pen's raw "width" above).
        # Matches what RV's real annotate tool actually writes post-FTGL
        # removal (`annotate_mode.mu`'s `fontSize = size * scale`) — the
        # legacy `size` field is also populated for old-build compatibility
        # but is no longer what any current rendering/sync reads.
        position = payload.get("position", [0.15, -0.08])
        text = payload.get("text", "sync test")
        color = payload.get("color", [1.0, 1.0, 1.0, 1.0])
        scale = float(payload.get("scale", 1.0))
        base_props = [
            ("position", TYPE_FLOAT, list(position), 2),
            ("color", TYPE_FLOAT, list(color), 4),
            ("spacing", TYPE_FLOAT, [0.8], 1),
            ("font", TYPE_STRING, [""], 1),
            ("text", TYPE_STRING, [text], 1),
            ("scale", TYPE_FLOAT, [scale], 1),
            ("rotation", TYPE_FLOAT, [0.0], 1),
            ("origin", TYPE_STRING, [""], 1),
            ("debug", TYPE_INT, [0], 1),
            ("startFrame", TYPE_INT, [frame], 1),
            ("duration", TYPE_INT, [1], 1),
            ("mode", TYPE_INT, [0], 1),
            ("uuid", TYPE_STRING, [str(uuid_mod.uuid4())], 1),
            ("softDeleted", TYPE_INT, [0], 1),
        ]
        if "legacy_size" in payload:
            # Simulates a session/broadcast predating the QPainter renderer:
            # only the old raw `size` (ptsize/10000 convention) exists, no
            # `fontSize` property at all — exercises
            # rv_paint_applier.read_stroke's fallback reconstruction
            # (`size*10000/1080*scale`) rather than reading `fontSize`
            # directly.
            legacy_size = float(payload["legacy_size"])
            size_props = [("size", TYPE_FLOAT, [legacy_size], 1)]
        else:
            # Matches what RV's real annotate tool writes post-FTGL removal
            # (`annotate_mode.mu`'s `fontSize = size * scale`) — the value
            # under test, deliberately NOT derived from any OTIO font_size
            # via font_size_to_rv (that's the forward conversion; this
            # harness tests the reverse of it, same convention as pen's raw
            # "width" above). Legacy `size` is also populated (chosen so the
            # C++ fallback formula would reconstruct the same fontSize on an
            # older build — scale cancels out of that reconstruction, see
            # rv_annotation_codec's analogous `_text_spec` derivation) purely
            # for old-build compatibility; current rendering/sync ignore it
            # whenever `fontSize` is present.
            font_size_wcs = float(payload.get("font_size_wcs", 0.0444))
            size_props = [
                ("size", TYPE_FLOAT, [font_size_wcs * 1080.0 / 10000.0], 1),
                ("fontSize", TYPE_FLOAT, [font_size_wcs * scale], 1),
            ]
        spec = {
            "kind": "text",
            "uuid": str(uuid_mod.uuid4()),
            "user": "sync_test",
            "props": base_props + size_props,
        }
    elif kind == "arrow":
        thickness = float(payload.get("thickness", 1.0))
        color = payload.get("color", [1.0, 1.0, 1.0, 1.0])
        start = payload.get("start", [-0.1, -0.1])
        end = payload.get("end", [0.1, 0.1])
        spec = {
            "kind": "arrow",
            "uuid": str(uuid_mod.uuid4()),
            "user": "sync_test",
            "props": [
                ("startPos", TYPE_FLOAT, list(start), 2),
                ("endPos", TYPE_FLOAT, list(end), 2),
                ("borderColor", TYPE_FLOAT, list(color), 4),
                ("innerColor", TYPE_FLOAT, list(color), 4),
                ("borderWidth", TYPE_FLOAT, [0.0], 1),
                ("thickness", TYPE_FLOAT, [thickness], 1),
                ("startFrame", TYPE_INT, [frame], 1),
                ("duration", TYPE_INT, [1], 1),
                ("eye", TYPE_INT, [2], 1),
                ("uuid", TYPE_STRING, [str(uuid_mod.uuid4())], 1),
                ("softDeleted", TYPE_INT, [0], 1),
            ],
        }
    else:
        raise ValueError(f"draw_annotation: unknown kind {kind!r}")

    next_strokeid = rv_paint_applier.apply_specs(
        [spec], rv.commands, rv_node=node, frame=frame, mode="append"
    )
    written_strokeid = next_strokeid - 1
    component = f"{spec['kind']}:{written_strokeid}:{frame}:{spec['user']}"

    controller = otio_sync_core.get_registered_annotation_controller()
    if controller is None:
        raise RuntimeError(
            "draw_annotation: annotation controller not registered yet "
            "(plugin has not connected to a session)"
        )
    controller._broadcast_annotation(node, component)

    return {"action": "draw_annotation", "status": "success", "node": node, "component": component}


def _capture_openrv_frame(payload):
    """Grab the live viewport widget in-process and save it to disk.

    Uses `testchart/grab_frame.py`'s proven technique (`rv.commands.sessionGLView()`
    wrapped as a Qt widget, `.grab().save(path)`) rather than an external `rvio`
    subprocess: `rvio` has known crash issues in this build (see the font-crash
    fallback that technique was originally written for), and the sync_test
    instance is already live, so no save/reload round-trip is needed either way.
    """
    output_path = payload.get("output_path")
    if not output_path:
        raise ValueError("capture_frame: output_path is required")

    width = payload.get("width")
    height = payload.get("height")
    if width and height:
        # Request a known capture size (design D2, option 1 — rv.commands.setViewSize
        # is a small, low-risk existing command) so the output resolution is
        # comparable to xStudio's explicit render size. Not guaranteed exact
        # (HiDPI/window-manager rounding) — the comparison step reads the saved
        # image's actual dimensions rather than trusting this request was
        # honored precisely (design D2, option 2, kept as the robust fallback).
        rv.commands.setViewSize(int(width), int(height))

    # Settle/redraw before capture (design Risk: capture timing) — force a
    # redraw and flush pending Qt paint events so the grab reflects the
    # annotation that was just applied, not a stale buffer.
    rv.commands.redraw()
    try:
        from PySide6 import QtWidgets
        import shiboken6
    except ImportError:
        from PySide2 import QtWidgets
        import shiboken2 as shiboken6
    QtWidgets.QApplication.processEvents()

    ptr = rv.commands.sessionGLView()
    if not ptr:
        raise RuntimeError("capture_frame: sessionGLView() returned no widget pointer")
    view = shiboken6.wrapInstance(ptr, QtWidgets.QWidget)
    view.grab().save(output_path)

    return {"action": "capture_frame", "status": "success", "output_path": output_path}


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

        def execute(self, item):
            func, q = item
            try:
                res = func()
                q.put(("ok", res))
            except Exception as e:
                q.put(("error", e))

        def run_sync(self, func, timeout=5.0):
            q = queue.Queue()
            self.execute_signal.emit((func, q))
            try:
                status, res = q.get(timeout=timeout)
                if status == "error":
                    raise res
                return res
            except queue.Empty:
                raise RuntimeError("Timeout waiting for main thread execution")

    # This is called from the main thread when the plugin loads/rvpush executes
    executor = MainThreadExecutor()

    def get_state_callback():
        return executor.run_sync(get_openrv_state)

    def get_full_state_callback():
        # export_state() serializes OTIO timelines the plugin mutates on RV's
        # main thread, so marshal it there.
        return executor.run_sync(get_openrv_full_state)

    def execute_callback(payload):
        # Use a longer timeout for commands that load media (addSourceVerbose can
        # take several seconds on first load).
        return executor.run_sync(lambda: execute_openrv_command_safe(payload), timeout=30.0)

    server = InspectionServer(
        http_port,
        get_state_callback,
        execute_command_callback=execute_callback,
        get_full_state_callback=get_full_state_callback,
    )
    server.start()
    return server
