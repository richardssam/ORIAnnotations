import rv.commands
import rv.extra_commands

try:
    from otio_sync_core.manager import STATE_SYNCED
except ImportError:
    STATE_SYNCED = "synced"

from utils import _log


class DisplaySyncController:
    # channelFlood encoding from rvui.mu showChannel(): 0=RGBA, 1=R, 2=G, 3=B, 4=A, 5=Luma
    _RV_FLOOD_TO_CH = {0: "RGBA", 1: "R", 2: "G", 3: "B", 4: "A"}
    _RV_CH_TO_FLOOD = {"RGBA": 0, "R": 1, "G": 2, "B": 3, "A": 4}

    def __init__(self, plugin):
        self.plugin = plugin
        self._last_display_state = {}
        self._display_color_nodes_logged = False

    def _members_recursive(self, group):
        """Return *group*'s members plus the members of any pipeline subgroups."""
        try:
            members = list(rv.commands.nodesInGroup(group))
        except Exception:
            return []
        for member in list(members):
            try:
                if rv.commands.nodeType(member).endswith("PipelineGroup"):
                    members.extend(rv.commands.nodesInGroup(member))
            except Exception:
                pass
        return members

    def _rv_display_color_nodes(self):
        """Return the channel-flood node for each active display pane.

        A pane's display pipeline carries ``color.channelFlood`` on an
        ``RVDisplayColor`` node by default, or on an ``OCIODisplay`` node when
        the pane's ``RVDisplayPipelineGroup`` is OCIO-managed (see
        ``RvTopViewToolBar::hasOCIODisplayPipeline`` in RV's source, which
        switches between ``@RVDisplayColor.color.channelFlood`` and
        ``@OCIODisplay.color.channelFlood`` the same way) -- so we probe each
        ``RVDisplayGroup``'s members for the property rather than assuming a
        node type.  Excludes ``defaultOutputGroup*`` which is the export/output
        pipeline and is NOT modified by the r/g/b/a channel-isolation keys.
        RV creates one display group per layout pane; pressing r/g/b/a only
        changes the *focused* pane, so we must read and write all of them.
        """
        try:
            groups = rv.commands.nodesOfType("RVDisplayGroup")
        except Exception:
            groups = []
        out = []
        for group in groups:
            if "defaultoutput" in group.lower():
                continue
            for n in self._members_recursive(group):
                try:
                    if rv.commands.propertyExists(f"{n}.color.channelFlood"):
                        out.append(n)
                        break
                except Exception:
                    continue
        if not self._display_color_nodes_logged:
            self._display_color_nodes_logged = True
            _log(f"display channelFlood nodes: {out}")
        return out

    def _rv_display_color_node(self):
        """Return the first active-viewer RVDisplayColor node, or None."""
        nodes = self._rv_display_color_nodes()
        return nodes[0] if nodes else None

    def _rv_color_node_for_current_source(self):
        """Return the RVColor node for the currently visible source, or None.

        ``rv.commands.sourcesAtFrame`` returns a list of source node names of
        the form ``sourceGroupNNNNNN_source``.  The corresponding RVColor pipeline
        node is ``sourceGroupNNNNNN_colorPipeline_0``.
        """
        try:
            sources = rv.commands.sourcesAtFrame(rv.commands.frame())
            if sources:
                src = sources[0]
                if src.endswith("_source"):
                    return src[:-len("_source")] + "_colorPipeline_0"
        except Exception:
            pass
        nodes = rv.commands.nodesOfType("RVColor")
        return nodes[0] if nodes else None

    def _read_annotations_visible(self):
        """Return the session-wide annotation visibility, read from RV's
        per-source ``<RVPaint node>.paint.show`` toggle ("Show Drawings").

        Must read the *currently viewed* ``RVPaint`` node specifically (via
        ``metaEvaluateClosestByType``, the same resolution
        ``annotation_sync._find_paint_node_for_media`` uses) rather than "any
        node that happens to have the property set" -- since a remote peer's
        broadcast is applied to *every* ``RVPaint`` node (this feature's
        session-wide scope), several nodes can hold different values at once,
        and scanning for the first one found could read a stale node instead
        of the one the local user just toggled.

        The property only exists on a node once "Show Drawings" has been
        toggled for it at least once (freshly-created nodes have no opinion)
        -- default to visible (``True``) if absent, matching this feature's
        documented "absent means visible" semantics.
        """
        try:
            eval_infos = rv.commands.metaEvaluateClosestByType(rv.commands.frame(), "RVPaint")
            if eval_infos:
                prop = f"{eval_infos[0]['node']}.paint.show"
                if rv.commands.propertyExists(prop):
                    return bool(rv.commands.getIntProperty(prop)[0])
        except Exception as e:
            _log(f"WARN _read_annotations_visible: {e}")
        return True

    def _read_rv_display_state(self):
        """Return a dict with pan, zoom, exposure, channel and annotation
        visibility for the current session.

        Pan/zoom come from ``rv.extra_commands.translation()`` / ``.scale()``.
        Exposure comes from the ``RVColor.color.exposure`` node for the
        *currently visible* source (the ``e`` key; 3-element RGB array, channel
        0 used as scalar).  Channel comes from ``RVDisplayColor.color.channelFlood``
        (``r``/``g``/``b``/``a`` keys; 0=RGBA 1=R 2=G 3=B 4=A). Annotation
        visibility comes from the per-source "Show Drawings" toggle, applied
        session-wide on receive (see :meth:`_apply_display_state`).
        """
        state = {
            "pan": [0.0, 0.0],
            "zoom": 1.0,
            "exposure": 0.0,
            "channel": "RGBA",
            "annotations_visible": self._read_annotations_visible(),
        }
        # Pan and zoom via rv.extra_commands (viewer-level, not a node property).
        try:
            t = rv.extra_commands.translation()
            state["pan"] = [float(t[0]), float(t[1])]
        except Exception as e:
            _log(f"WARN _read_rv_display_state translation: {e}")
        try:
            state["zoom"] = float(rv.extra_commands.scale())
        except Exception as e:
            _log(f"WARN _read_rv_display_state scale: {e}")
        # Exposure — current source's RVColor node (e key).
        try:
            node = self._rv_color_node_for_current_source()
            if node:
                exp = rv.commands.getFloatProperty(f"{node}.color.exposure")
                state["exposure"] = float(exp[0]) if exp else 0.0
        except Exception as e:
            _log(f"WARN _read_rv_display_state exposure: {e}")
        # Channel — scan ALL displayGroup RVDisplayColor nodes.
        # Pressing r/g/b/a only changes the focused pane's node; if that pane is
        # not displayGroup0 we'd miss the change reading just one node.  Scan all
        # of them: if any deviates from the last known channel, use that value so
        # the change is detected and broadcast.
        dc_nodes = self._rv_display_color_nodes()
        if dc_nodes:
            last_flood = self._RV_CH_TO_FLOOD.get(
                self._last_display_state.get("channel", "RGBA"), 0)
            floods = []
            for n in dc_nodes:
                try:
                    f = rv.commands.getIntProperty(f"{n}.color.channelFlood")
                    floods.append(f[0] if f else 0)
                except Exception as e:
                    _log(f"WARN _read_rv_display_state channelFlood ({n}): {e}")
            if floods:
                # Prefer any pane that differs from the last broadcast state
                # (that's the pane the user just changed).
                changed = [f for f in floods if f != last_flood]
                state["channel"] = self._RV_FLOOD_TO_CH.get(
                    changed[0] if changed else floods[0], "RGBA")
        return state

    def _broadcast_display_state(self):
        """Read the current RV display state and broadcast it if it has changed.

        When exposure changes, all per-source ``RVColor`` nodes are normalised
        to the new value before broadcasting.  This ensures that navigating
        between clips (which may have had different per-clip exposures set
        before the sync was active) does not trigger spurious re-broadcasts on
        the next frame.
        """
        if self.plugin._rv_updating or not self.plugin.sync_manager or self.plugin.sync_manager.status != STATE_SYNCED:
            return
        state = self._read_rv_display_state()
        if state == self._last_display_state:
            return
        prev = self._last_display_state
        self._last_display_state = state
        # Guard the normalisation writes with _rv_updating so that the
        # synchronous graph-state-change events they fire are suppressed by
        # on_rv_graph_state_change.  Without this, each write re-enters
        # _broadcast_display_state while the other panes are still mid-update,
        # causing the "changed" detection to misread a partially-normalised
        # state and broadcast the wrong channel back.
        self.plugin._rv_updating = True
        try:
            # Normalise all source nodes to the new exposure so that navigating
            # between clips does not trigger false change detections next tick.
            if state["exposure"] != prev.get("exposure"):
                ev = float(state["exposure"])
                try:
                    for node in rv.commands.nodesOfType("RVColor"):
                        rv.commands.setFloatProperty(
                            f"{node}.color.exposure", [ev, ev, ev], True)
                except Exception:
                    pass
            # Normalise all display panes to the new channel so that subsequent
            # reads from any pane agree and don't re-trigger a broadcast.
            if state["channel"] != prev.get("channel"):
                flood = self._RV_CH_TO_FLOOD.get(state["channel"], 0)
                for dc in self._rv_display_color_nodes():
                    try:
                        rv.commands.setIntProperty(
                            f"{dc}.color.channelFlood", [flood], True)
                    except Exception:
                        pass
        finally:
            self.plugin._rv_updating = False
        _log(f"SEND display zoom={state['zoom']:.3f} pan={state['pan']} "
             f"exposure={state['exposure']:.3f} channel={state['channel']}")
        self.plugin.sync_manager.broadcast_display_state(state)

    def _apply_display_state(self, data):
        """Apply an incoming display state dict to the local RV session.

        Pan/zoom are applied via ``rv.extra_commands`` only when the incoming
        values are non-None.  A ``None`` value means the sender does not support
        pan/zoom (e.g. xStudio) and the local values should be left unchanged.
        Exposure is written to **all** ``RVColor`` source nodes (3-element RGB)
        so every clip matches.  Channel is written to
        ``RVDisplayColor.color.channelFlood``.
        """
        pan = data.get("pan")
        zoom = data.get("zoom")
        exposure = data.get("exposure", 0.0)
        channel = data.get("channel", "RGBA")
        annotations_visible = data.get("annotations_visible", True)
        _log(f"RECV display pan={pan} zoom={zoom} "
             f"exposure={exposure:.3f} channel={channel} "
             f"annotations_visible={annotations_visible}")

        if pan is not None:
            try:
                rv.extra_commands.setTranslation((float(pan[0]), float(pan[1])))
            except Exception as e:
                _log(f"RECV display: pan set failed: {e}")
        if zoom is not None:
            try:
                rv.extra_commands.setScale(float(zoom))
            except Exception as e:
                _log(f"RECV display: zoom set failed: {e}")

        # Apply exposure to every source node so all clips match.
        try:
            ev = float(exposure)
            for node in rv.commands.nodesOfType("RVColor"):
                rv.commands.setFloatProperty(
                    f"{node}.color.exposure", [ev, ev, ev], True)
        except Exception as e:
            _log(f"RECV display: exposure set failed: {e}")

        flood = self._RV_CH_TO_FLOOD.get(channel, 0)
        for dc in self._rv_display_color_nodes():
            try:
                rv.commands.setIntProperty(f"{dc}.color.channelFlood",
                                           [flood], True)
            except Exception as e:
                _log(f"RECV display: channel set failed ({dc}): {e}")

        # Annotation visibility ("Show Drawings") is applied session-wide --
        # every RVPaint node, not just the one that originated the change on
        # the sending peer -- matching xStudio's own global toggle scope.
        try:
            show_val = 1 if annotations_visible else 0
            for node in rv.commands.nodesOfType("RVPaint"):
                prop = f"{node}.paint.show"
                if not rv.commands.propertyExists(prop):
                    rv.commands.newProperty(prop, rv.commands.IntType, 1)
                rv.commands.setIntProperty(prop, [show_val], True)
        except Exception as e:
            _log(f"RECV display: annotations_visible set failed: {e}")

        # Keep _last_display_state consistent with what we actually hold.
        # If the sender omitted pan/zoom, preserve our current read-back values
        # so the next broadcast comparison doesn't spuriously see a change.
        cur = self._read_rv_display_state()
        self._last_display_state = {
            "pan": [float(pan[0]), float(pan[1])] if pan is not None else cur["pan"],
            "zoom": float(zoom) if zoom is not None else cur["zoom"],
            "exposure": exposure,
            "channel": channel,
            "annotations_visible": annotations_visible,
        }
        rv.commands.redraw()
