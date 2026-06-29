#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""ColorSyncController — OCIO color sync for xStudio.

Bridges the synced color metadata (``Timeline.metadata["color"]`` and
``Clip.metadata["color_space"]``, see the ``color-pipeline-sync`` capability) to
xStudio's OCIO colour pipeline:

* a clip's resolved **input** colorspace is written to the media source metadata
  key ``/colour_pipeline/override_input_cs`` — the exact key xStudio's OCIO
  plugin writes when the user picks a Source colourspace, and which the OCIO
  engine reads back (``source_transform`` does ``csc->setSrc(override_input_cs)``).
* the timeline **output** space is written to the viewport colour pipeline's
  OCIO ``Display`` attribute.

Receive is driven by the manager's ``on_property_changed`` callback (fired on the
poll thread while a remote patch is applied).  Broadcast is polled, mirroring
:class:`DisplaySyncController`.  Colourspace names are resolved against the
vocabulary convention in :mod:`otio_sync_core.color`; an unresolvable name warns
and is left unapplied (color must never abort a sync apply).

All xStudio reads run inside :func:`bounded_timeout` so a stale media/viewport
actor fails fast instead of freezing the poll thread for 100 s.
"""

import time

from otio_sync_core import color
from otio_sync_core.manager import STATE_SYNCED

from .utils import _log, _log_exc, bounded_timeout

#: Bounded window (ms) for an individual poll-thread colour read.  Kept small so
#: a stale media actor fails fast — with many bin clips a 2 s timeout per read
#: multiplied into an ~18 s poll-thread stall that starved the structural-sync
#: path (clip inserts / sequence rebuilds).
_COLOR_TIMEOUT_MS = 400
#: Total wall-clock budget (s) for one poll_and_broadcast_color pass.  Caps the
#: poll-thread cost regardless of how many (possibly stale) media are mapped.
_COLOR_POLL_BUDGET_S = 1.5
#: Media-source metadata key that holds the per-media OCIO input colourspace.
_OVERRIDE_INPUT_CS = "override_input_cs"
_COLOUR_PIPELINE = "/colour_pipeline"

#: Whether to live-sync the timeline ``output_space`` (display) across peers.
#: Disabled: the viewport OCIO **Display** is the local monitor (e.g.
#: "Apple Display P3 - Display"), which is device-centric — broadcasting it
#: clobbers each peer's display with the sender's monitor.  See the RFC note
#: that output is a per-device hint.  Only the input colorspace is synced.
_SYNC_OUTPUT_SPACE = False


class ColorSyncController:
    """Apply and broadcast OCIO color state against the synced color metadata."""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        #: Last-known input colorspace, keyed by clip sync GUID.
        self._last_input: dict = {}
        #: Last-known display output colorspace name.
        self._last_output: "str | None" = None
        self._last_color_scan: float = 0.0

    def reset(self) -> None:
        """Clear cached colour state on disconnect."""
        self._last_input = {}
        self._last_output = None

    # ── name handling ───────────────────────────────────────────────────────

    def _resolvable_name(self, value: str) -> "str | None":
        """Return the bare name to feed xStudio, or ``None`` if not resolvable."""
        if not value:
            return None
        vocab, name = color.parse_colorspace(value)
        if vocab not in color.RESOLVED_VOCABULARIES:
            _log(f"color: vocabulary {vocab!r} not resolvable in xStudio; "
                 f"leaving {value!r} unapplied")
            return None
        return name

    def _qualify(self, name: str) -> str:
        """Tag a bare xStudio colourspace name for the wire (default ``ocio``)."""
        vocab, _ = color.parse_colorspace(name)
        if ":" in name and vocab in color.RESOLVED_VOCABULARIES:
            return name
        return f"{color.DEFAULT_VOCABULARY}:{name}"

    # ── receive (apply) ─────────────────────────────────────────────────────

    def apply_property_change(self, target_uuid: str, path: str, value) -> None:
        """``manager.on_property_changed`` callback — apply colour metadata changes.

        Fired on the poll thread for every property change; only colour paths are
        acted on.  Non-colour paths and unresolvable names are ignored.
        """
        if not path:
            return
        try:
            if path == f"metadata/{color.COLOR_SPACE}":
                self.apply_clip_color_space(target_uuid, value)
            elif path == f"metadata/{color.COLOR_GROUP}/{color.OUTPUT_SPACE}":
                if _SYNC_OUTPUT_SPACE:
                    self.apply_timeline_output(value)
        except Exception:
            _log_exc("color: apply_property_change failed")

    def apply_clip_color_space(self, clip_guid: str, value) -> None:
        """Apply a received clip ``color_space`` to its xStudio media."""
        name = self._resolvable_name(value)
        if name is None:
            return
        media, _aspect = self.plugin.media.media_for_sync_guid(clip_guid)
        if media is None:
            _log(f"color: no xStudio media for clip {clip_guid[:8]}")
            return
        try:
            media.media_source().set_metadata(name, f"{_COLOUR_PIPELINE}/{_OVERRIDE_INPUT_CS}")
            self._last_input[clip_guid] = name
            _log(f"RECV color clip={clip_guid[:8]} override_input_cs={name!r}")
        except Exception as e:
            _log(f"color: set override_input_cs failed for {clip_guid[:8]}: {e}")

    def apply_timeline_output(self, value) -> None:
        """Apply a received timeline ``output_space`` to the viewport OCIO Display."""
        name = self._resolvable_name(value)
        if name is None:
            return
        vp = self.plugin.display.get_viewport()
        if vp is None:
            return
        try:
            vp.colour_pipeline.display.set_value(name)
            self._last_output = name
            _log(f"RECV color output_space display={name!r}")
        except Exception as e:
            _log(f"color: set display failed: {e}")

    # ── broadcast (write-back) — polled ─────────────────────────────────────

    def poll_and_broadcast_color(self) -> None:
        """Broadcast local colour changes (per-media input, viewport output)."""
        manager = self.plugin.manager
        if not manager or manager.status != STATE_SYNCED:
            return

        # Per-media input colorspace from the OCIO override metadata.
        # Dead media refs (e.g. flat-playlist media left over from a
        # flat→sequence transition) time out on every read; evict them so the
        # poll self-heals to reading only live media instead of paying a stale
        # timeout each cycle.  A wall-clock budget caps the worst-case pass while
        # the dead refs are still being pruned.
        mapping = dict(self.plugin.media._sync_guid_to_xs_media)
        _deadline = time.monotonic() + _COLOR_POLL_BUDGET_S
        dead: list[str] = []
        for clip_guid, media in mapping.items():
            if time.monotonic() >= _deadline:
                _log("Poll color: budget exceeded — deferring remaining media to next pass")
                break
            try:
                with bounded_timeout(self.plugin.connection, _COLOR_TIMEOUT_MS):
                    md = media.media_source().get_metadata(_COLOUR_PIPELINE)
                cur = (md.get(_OVERRIDE_INPUT_CS) or None) if md else None
            except Exception:
                dead.append(clip_guid)
                continue
            if cur and cur != self._last_input.get(clip_guid):
                self._last_input[clip_guid] = cur
                manager.set_property(
                    clip_guid, f"metadata/{color.COLOR_SPACE}", self._qualify(cur))
                _log(f"Poll color: clip={clip_guid[:8]} color_space={self._qualify(cur)}")

        # Prune dead media so we never pay their read timeout again.
        for _g in dead:
            self.plugin.media.evict(_g)
            self._last_input.pop(_g, None)
        if dead:
            _log(f"Poll color: evicted {len(dead)} dead media ref(s)")

        # Timeline output colorspace from the viewport OCIO Display.
        tl_guid = getattr(manager, "active_timeline_guid", None)
        if _SYNC_OUTPUT_SPACE and tl_guid:
            cur = self._read_display()
            if cur and cur != self._last_output:
                self._last_output = cur
                manager.set_property(
                    tl_guid,
                    f"metadata/{color.COLOR_GROUP}/{color.OUTPUT_SPACE}",
                    self._qualify(cur),
                )
                _log(f"Poll color: timeline={tl_guid[:8]} output_space={self._qualify(cur)}")

    def _read_display(self) -> "str | None":
        vp = self.plugin.display.get_viewport()
        if vp is None:
            return None
        try:
            with bounded_timeout(self.plugin.connection, _COLOR_TIMEOUT_MS):
                return str(vp.colour_pipeline.display.value()) or None
        except Exception:
            return None
