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
        for pnode in rv.commands.nodesOfType("RVPaint"):
            try:
                comps = set()
                for prop in rv.commands.properties(pnode):
                    short = prop[len(pnode) + 1:] if prop.startswith(pnode + ".") else prop
                    if short.startswith("pen:") or short.startswith("text:"):
                        comps.add(short.rsplit(".", 1)[0])
                total_strokes += len(comps)
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
