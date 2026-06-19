"""Color pipeline sync for OpenRV.

Bridges the synced color metadata (``Timeline.metadata["color"]`` and
``Clip.metadata["color_space"]``, see the ``color-pipeline-sync`` capability)
to RV's OCIO pipeline:

* a clip's resolved **input** colorspace is written to the OCIO node carrying
  ``ocio.inColorSpace`` in that source's ``RVLinearizePipelineGroup``;
* the timeline **output** space is written to the OCIO node in the display
  pipeline.

The reverse direction is **event-driven**: RV fires a ``graph-state-change`` for
``<node>.ocio.inColorSpace`` whenever the user changes a colorspace, so we read
the value straight from that node, map it to the owning clip (or the display)
and broadcast it over the existing ``SetProperty`` path — no new protocol
message, and no polling/guessing of node names.

The node that holds ``ocio.inColorSpace`` is discovered by probing for the
property (``propertyExists``) rather than matching a node *type*, because RV
exposes it on the pipeline-group node (e.g. ``sourceGroup000000_tolinPipeline_0``)
rather than on a separately-named ``OCIOFile`` child.

All host calls are wrapped defensively: an unresolvable name (a vocabulary RV
does not handle, or a source/display that is not OCIO-managed) emits a warning
and leaves the node untouched.  Color must never abort a load or a sync apply.
"""

import re

import rv.commands

import opentimelineio as otio

try:
    from otio_sync_core.manager import STATE_SYNCED
except ImportError:
    STATE_SYNCED = "synced"

try:
    from otio_sync_core import color
except ImportError:  # pragma: no cover - color module always ships with core
    color = None

from utils import _log, _media_path

#: Suffix of the RV property that carries an OCIO colorspace name.
_OCIO_PROP = ".ocio.inColorSpace"

#: Whether to live-sync the timeline ``output_space`` (display) across peers.
#: Disabled: the viewport OCIO **Display** is the local monitor (e.g.
#: "Apple Display P3 - Display"), which is device-centric — broadcasting it
#: clobbers each peer's display with the sender's monitor.  See the RFC note
#: that output is a per-device hint.  Only the input colorspace is synced.
_SYNC_OUTPUT_SPACE = False


class ColorSyncController:
    """Apply and broadcast OCIO color state against the synced color metadata."""

    def __init__(self, plugin):
        self.plugin = plugin
        #: Last-known input colorspace, keyed by source group node.
        self._last_input = {}
        #: Last-known display output colorspace name.
        self._last_output = None

    # ------------------------------------------------------------------
    # Name handling
    # ------------------------------------------------------------------

    def _resolvable_name(self, value):
        """Return the bare name to feed RV, or ``None`` if RV cannot resolve it."""
        if not value or color is None:
            return None
        vocab, name = color.parse_colorspace(value)
        if vocab not in color.RESOLVED_VOCABULARIES:
            _log(f"WARN color: vocabulary '{vocab}' not resolvable in RV; "
                 f"leaving '{value}' unapplied")
            return None
        return name

    def _qualify(self, name):
        """Tag a bare RV colorspace name for the wire (default vocabulary ``ocio``)."""
        if color is None:
            return name
        vocab, _ = color.parse_colorspace(name)
        if ":" in name and vocab in color.RESOLVED_VOCABULARIES:
            return name
        return f"{color.DEFAULT_VOCABULARY}:{name}"

    # ------------------------------------------------------------------
    # Node discovery (probe by property, not by node type)
    # ------------------------------------------------------------------

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

    def _ocio_input_node(self, source_group):
        """Return the node carrying ``ocio.inColorSpace`` for *source_group*.

        ``None`` when the source is not OCIO-managed (RV is using the default
        ``RVLinearize`` path instead).
        """
        for n in self._members_recursive(source_group):
            try:
                if rv.commands.propertyExists(n + _OCIO_PROP):
                    return n
            except Exception:
                continue
        return None

    def _ocio_display_nodes(self):
        """Return display-pipeline nodes carrying ``ocio.inColorSpace`` (may be empty)."""
        out = []
        try:
            groups = rv.commands.nodesOfType("RVDisplayGroup")
        except Exception:
            groups = []
        for group in groups:
            for n in self._members_recursive(group):
                try:
                    if rv.commands.propertyExists(n + _OCIO_PROP):
                        out.append(n)
                except Exception:
                    continue
        return out

    def _source_group_for_clip(self, clip):
        ref = getattr(clip, "media_reference", None)
        url = getattr(ref, "target_url", None)
        if not url:
            return None
        return self.plugin.sequence._path_to_source_group_map().get(_media_path(url))

    def _media_path_for_source_group(self, source_group):
        """Return the media path of *source_group*'s file source, or ``None``."""
        try:
            for n in rv.commands.nodesInGroup(source_group):
                if rv.commands.nodeType(n) == "RVFileSource":
                    movie = rv.commands.getStringProperty(f"{n}.media.movie")
                    if movie and movie[0]:
                        return movie[0]
        except Exception as e:
            _log(f"WARN color: media path for {source_group}: {e}")
        return None

    @staticmethod
    def _clips(timeline):
        try:
            return list(timeline.find_clips())
        except Exception:
            try:
                return list(timeline.each_clip())
            except Exception:
                return []

    # ------------------------------------------------------------------
    # Apply (receive)
    # ------------------------------------------------------------------

    def _set_source_input_colorspace(self, source_group, value):
        name = self._resolvable_name(value)
        if name is None:
            return
        node = self._ocio_input_node(source_group)
        if not node:
            _log(f"WARN color: no ocio.inColorSpace node for {source_group}; "
                 f"source not OCIO-managed")
            return
        try:
            rv.commands.setStringProperty(node + _OCIO_PROP, [name], True)
            self._last_input[source_group] = name
            _log(f"color: {source_group} inColorSpace -> {name}")
        except Exception as e:
            _log(f"WARN color: set inColorSpace on {node}: {e}")

    def apply_clip_color_space(self, clip_guid, value):
        """Apply a received clip ``color_space`` change to its RV source."""
        clip = self.plugin.sync_manager.object_map.get(clip_guid)
        if not isinstance(clip, otio.schema.Clip):
            return
        sg = self._source_group_for_clip(clip)
        if sg:
            self._set_source_input_colorspace(sg, value)

    def apply_timeline_output(self, value):
        """Apply a received timeline ``output_space`` to the OCIO display node(s)."""
        if not _SYNC_OUTPUT_SPACE:
            return
        name = self._resolvable_name(value)
        if name is None:
            return
        nodes = self._ocio_display_nodes()
        if not nodes:
            _log("WARN color: no OCIO display node; display not OCIO-managed")
            return
        for n in nodes:
            try:
                rv.commands.setStringProperty(n + _OCIO_PROP, [name], True)
            except Exception as e:
                _log(f"WARN color: set display inColorSpace on {n}: {e}")
        self._last_output = name

    def on_property_changed(self, target_uuid, path, value):
        """Dispatch a received ``metadata/color*`` change to the right applier.

        The caller is expected to wrap this in the plugin's ``_rv_updating``
        guard so the node writes do not echo back out.
        """
        if color is None or not path:
            return
        if path == f"metadata/{color.COLOR_SPACE}":
            self.apply_clip_color_space(target_uuid, value)
        elif path == f"metadata/{color.COLOR_GROUP}/{color.OUTPUT_SPACE}":
            if _SYNC_OUTPUT_SPACE:
                self.apply_timeline_output(value)
        elif path.startswith(f"metadata/{color.COLOR_GROUP}/"):
            # config / working_space change: re-resolve every inheriting clip.
            self.apply_all()

    def apply_all(self):
        """Resolve and apply color for every clip in the active timeline."""
        if color is None:
            return
        sm = self.plugin.sync_manager
        tl = getattr(sm, "active_timeline", None)
        if tl is None:
            return
        for clip in self._clips(tl):
            resolved = color.resolve_input_colorspace(clip, tl)
            if not resolved:
                continue
            sg = self._source_group_for_clip(clip)
            if sg:
                self._set_source_input_colorspace(sg, resolved)
        out = color.read_timeline_color(tl).get(color.OUTPUT_SPACE)
        if out:
            self.apply_timeline_output(out)
        try:
            rv.commands.redraw()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Broadcast (write-back) — driven by RV's graph-state-change event
    # ------------------------------------------------------------------

    def on_graph_state_change(self, event):
        """Handle an ``ocio.inColorSpace`` change; return ``True`` if consumed.

        Returns ``False`` for non-color events so the caller can fall through to
        the other graph-state-change handlers.  When it consumes the event it
        calls ``event.reject()`` itself (matching the annotation handler).
        """
        if color is None:
            return False
        contents = event.contents()
        if not contents.endswith(_OCIO_PROP):
            return False
        sm = self.plugin.sync_manager
        if self.plugin._rv_updating or not sm or sm.status != STATE_SYNCED:
            event.reject()
            return True
        node = contents[: -len(_OCIO_PROP)]
        try:
            value = rv.commands.getStringProperty(node + _OCIO_PROP)[0]
        except Exception as e:
            _log(f"WARN color: read {node}{_OCIO_PROP}: {e}")
            event.reject()
            return True
        self._broadcast_node_color(node, value)
        event.reject()
        return True

    @staticmethod
    def _source_group_of(node):
        """Return the ``sourceGroupNNNNNN`` token in *node*'s name, or ``None``.

        Derived from the node name rather than ``nodeGroup()`` because the OCIO
        node lives two levels deep (``sourceGroup000000`` ›
        ``sourceGroup000000_tolinPipeline`` › ``sourceGroup000000_tolinPipeline_0``),
        so ``nodeGroup()`` only yields the intermediate pipeline group.
        """
        m = re.match(r"(sourceGroup\d+)", node)
        return m.group(1) if m else None

    def _broadcast_node_color(self, node, value):
        """Broadcast a colorspace change read from *node* over ``SetProperty``.

        Classification is by node identity: a ``sourceGroup*`` node is always an
        input (clip ``color_space``); anything else (display / output pipeline)
        is the timeline ``output_space``.  A source whose clip GUID can't be
        resolved is reported, not silently re-routed to the output.
        """
        if not value:
            return
        sm = self.plugin.sync_manager
        qualified = self._qualify(value)
        source_group = self._source_group_of(node)

        if source_group is not None:
            media_path = self._media_path_for_source_group(source_group)
            clip_guid = (self.plugin.playback._clip_guid_for_media_path(media_path)
                         if media_path else None)
            if not clip_guid:
                _log(f"WARN color: no clip for source {source_group} "
                     f"(node={node}, media={media_path})")
                return
            if value != self._last_input.get(source_group):
                self._last_input[source_group] = value
                sm.set_property(
                    clip_guid, f"metadata/{color.COLOR_SPACE}", qualified)
                _log(f"SEND color clip={clip_guid} color_space={qualified}")
        elif _SYNC_OUTPUT_SPACE:
            tl_guid = getattr(sm, "active_timeline_guid", None)
            if tl_guid and value != self._last_output:
                self._last_output = value
                sm.set_property(
                    tl_guid,
                    f"metadata/{color.COLOR_GROUP}/{color.OUTPUT_SPACE}",
                    qualified,
                )
                _log(f"SEND color timeline={tl_guid} output_space={qualified}")
