#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""DisplaySyncController — viewport display-state sync (zoom, exposure, channel)."""

import json
import time

from xstudio.api.intrinsic.viewport import Viewport
from xstudio.core import serialise_atom

from otio_sync_core.manager import STATE_SYNCED  # noqa: E402

from .utils import _log, bounded_timeout

# Bounded timeout (ms) for quick poll-thread viewport reads — well below the
# 100 s default so a stale viewport actor fails fast instead of freezing.
_DISPLAY_TIMEOUT_MS = 2000

def _parse_vec(val) -> list[float]:
    """Parse an Imath vector from JSON, skipping JSONStore type headers if present."""
    if not val:
        return []
    if isinstance(val[0], str):
        # Skip type name (e.g. 'vec2') and version number (usually 1)
        return [float(x) for x in val[2:]]
    return [float(x) for x in val]


class DisplaySyncController:
    """Owns display-state sync (exposure, channel, zoom) with the active viewport.

    :param plugin: Back-reference to the ORISyncPlugin instance.
    """

    _XS_TO_PROTO_CHANNEL = {
        "RGB": "RGBA", "RGBA": "RGBA",
        "Red": "R", "Green": "G", "Blue": "B", "Alpha": "A",
        "R": "R", "G": "G", "B": "B", "A": "A",
    }
    _PROTO_TO_XS_CHANNEL = {
        "RGBA": "RGB", "R": "Red", "G": "Green", "B": "Blue", "A": "Alpha",
    }

    # Protocol pan lives in the same host-neutral space as annotation geometry
    # (otio_sync_core.coords: H-normalised, Y-up, centre-origin — one unit is
    # one full image *height*; see coords.px_to_otio/otio_to_px). RV's own
    # rv.extra_commands.translation() is passed straight through as this
    # value with zero conversion (rvplugin/ori_sync/display_sync.py), so it
    # is being treated as already living in that space.
    #
    # xStudio's raw vp.pan (== state_.translate_, see Viewport::pan()/
    # set_pan()) is instead one unit per *half* of the viewport's own
    # extent per axis (viewport.cpp's mouse-pan handler builds it from
    # NDC in [-1, 1]) — i.e. twice as sensitive per unit as the protocol
    # space, uniformly on both axes (update_matrix()'s projection_matrix_
    # never lets any .scale() call touch the translation row, so x/y reach
    # the renderer identically; no aspect term belongs here — an x-only
    # aspect factor tried earlier was based on the mouse-drag interaction
    # code, a different, unrelated path we don't go through when writing
    # vp.pan directly). Hence the flat factor of 2 in both directions.
    _XS_PAN_UNITS_PER_PROTOCOL_UNIT = 2.0

    def __init__(self, plugin) -> None:
        self.plugin = plugin

        # Cached Viewport object; created lazily, cleared on disconnect.
        self._viewport: "Viewport | None" = None
        # Timeline to set as on-screen source once the viewport is ready.
        # Set by builder.do_load_timelines; consumed and cleared by get_viewport.
        self._pending_on_screen_source = None
        # Last display state broadcast; compared each poll tick to detect changes.
        self._last_display_state: dict = {}
        # xStudio's internal viewport scale at the first successful read. Used
        # to normalise state_.scale_ (image_pixels/viewport_pixels) to RV's
        # convention (1.0 = fit-to-window).
        self._xs_base_scale: float | None = None
        # Last read value of the playhead "Pinned Source Mode" attribute.
        self._last_pinned_source_mode: bool | None = None
        # Timestamps
        self._last_display_scan: float = 0.0
        self._last_viewport_error_log_time: float = 0.0

    def reset(self) -> None:
        """Clear display state on disconnect."""
        self._viewport = None
        self._pending_on_screen_source = None
        self._last_display_state = {}
        self._xs_base_scale = None
        self._last_pinned_source_mode = None

    # ── viewport ──────────────────────────────────────────────────────────────

    def get_viewport(self) -> "Viewport | None":
        """Return a cached Viewport for the active xStudio window, or None on error."""
        if self._viewport is not None:
            if self._pending_on_screen_source is not None:
                try:
                    self.plugin.connection.api.session.set_on_screen_source(
                        self._pending_on_screen_source
                    )
                    _log(f"Applied deferred on-screen source: {getattr(self._pending_on_screen_source, 'name', '?')}")
                except Exception:
                    pass
                self._pending_on_screen_source = None
            return self._viewport
        try:
            self._viewport = Viewport(self.plugin.connection, active_viewport=True)
            _log("Viewport acquired")
        except Exception as e:
            now = time.monotonic()
            if now - self._last_viewport_error_log_time >= 5.0:
                _log(f"get_viewport: {e}")
                self._last_viewport_error_log_time = now
            return self._viewport
        if self._pending_on_screen_source is not None:
            try:
                self.plugin.connection.api.session.set_on_screen_source(
                    self._pending_on_screen_source
                )
                _log(f"Applied deferred on-screen source: {getattr(self._pending_on_screen_source, 'name', '?')}")
            except Exception:
                pass
            self._pending_on_screen_source = None
        return self._viewport

    # ── read ──────────────────────────────────────────────────────────────────

    def read_xs_display_state(self) -> dict:
        """Return a display state dict read from the active xStudio viewport.

        The colour_pipeline reads (``cp.exposure.value()``, ``cp.channel.value()``)
        are synchronous request_receive calls bounded only by the connection's
        100 s default.  ``bounded_timeout`` lowers that so a stale viewport actor
        fails fast instead of freezing the poll thread; on failure the cached
        viewport is dropped so the next call re-acquires a live one.
        """
        state: dict = {"pan": None, "zoom": None, "exposure": 0.0, "channel": "RGBA"}
        vp = self.get_viewport()
        if vp is None:
            return state

        # All reads share one bounded window.  Treat ANY failure as "viewport
        # actor unresponsive": drop this update and clear the cached viewport so
        # the next call re-acquires a live one (the same stale-actor pattern that
        # affects the playhead during source-view switches).
        try:
            with bounded_timeout(self.plugin.connection, _DISPLAY_TIMEOUT_MS):
                cp = vp.colour_pipeline
                state["exposure"] = float(cp.exposure.value())
                xs_ch = cp.channel.value()
                state["channel"] = self._XS_TO_PROTO_CHANNEL.get(str(xs_ch), "RGBA")
                js = self.plugin.connection.request_receive_timeout(
                    100, vp.remote, serialise_atom()
                )[0]
                vp_state = json.loads(js.dump())["base"]
                raw_scale = float(vp_state["scale"])
                translate = _parse_vec(vp_state.get("translate"))

                translate_x = float(translate[0]) if len(translate) > 0 else 0.0
                translate_y = float(translate[1]) if len(translate) > 1 else 0.0

                fit_mode = vp.get_attribute("Fit (F)").value()
                if fit_mode != "Off":
                    self._xs_base_scale = raw_scale
                    state["zoom"] = 1.0
                    state["pan"] = [0.0, 0.0]
                else:
                    if self._xs_base_scale is None and raw_scale > 0.0:
                        self._xs_base_scale = raw_scale
                    state["zoom"] = (raw_scale / self._xs_base_scale) if self._xs_base_scale else 1.0
                    # Inverse of apply_display_state's write conversion — see
                    # _XS_PAN_UNITS_PER_PROTOCOL_UNIT above. Both axes are also
                    # inverted between the two apps' pan conventions (confirmed
                    # empirically — panning in either app moved the peer the
                    # opposite way on both x/y).
                    k = self._XS_PAN_UNITS_PER_PROTOCOL_UNIT
                    state["pan"] = [-translate_x / k, translate_y / k]
        except Exception as e:
            _log(f"read_xs_display_state: read failed ({e}) — dropping stale viewport")
            self._viewport = None
            return {"pan": [0.0, 0.0], "zoom": 1.0, "exposure": 0.0, "channel": "RGBA"}
        return state

    # ── apply ─────────────────────────────────────────────────────────────────

    def apply_display_state(self, state: dict) -> None:
        """Apply a received display state dict to the local xStudio viewport."""
        vp = self.get_viewport()
        if vp is None:
            return

        pan = state.get("pan")
        zoom = state.get("zoom")
        exposure = state.get("exposure", 0.0)
        channel = state.get("channel", "RGBA")

        try:
            vp.colour_pipeline.exposure.set_value(float(exposure))
        except Exception as e:
            _log(f"RECV display: exposure set failed: {e}")

        try:
            xs_ch = self._PROTO_TO_XS_CHANNEL.get(channel, "RGB")
            vp.colour_pipeline.channel.set_value(xs_ch)
        except Exception as e:
            _log(f"RECV display: channel set failed: {e}")

        if pan is not None or zoom is not None:
            try:
                js = self.plugin.connection.request_receive_timeout(
                    100, vp.remote, serialise_atom()
                )[0]
                vp_state = json.loads(js.dump())["base"]

                fit_mode = vp.get_attribute("Fit (F)").value()
                if fit_mode != "Off":
                    if self._xs_base_scale is None:
                        self._xs_base_scale = float(vp_state["scale"])
                    vp.set_attribute("Fit (F)", "Off")
                    _log("Set viewport fit mode to Off for pan/zoom sync")

                if zoom is not None:
                    if self._xs_base_scale is None:
                        self._xs_base_scale = float(vp_state["scale"])
                    vp.scale = float(zoom) * self._xs_base_scale

                if pan is not None:
                    k = self._XS_PAN_UNITS_PER_PROTOCOL_UNIT
                    vp.pan = (-float(pan[0]) * k, float(pan[1]) * k)
            except Exception as e:
                _log(f"RECV display: pan/zoom set failed: {e}")

        readback = self.read_xs_display_state()
        self._last_display_state = {
            "pan": readback["pan"],
            "zoom": readback["zoom"],
            "exposure": exposure,
            "channel": channel,
        }
        _log(f"RECV display exposure={exposure:.3f} channel={channel} "
             f"zoom={zoom} pan={pan}")

    # ── poll ──────────────────────────────────────────────────────────────────

    def poll_and_broadcast_display(self) -> None:
        """Broadcast display state when display settings (exposure, channel, zoom, pan) change."""
        manager = self.plugin.manager
        if not manager or manager.status != STATE_SYNCED:
            return
        state = self.read_xs_display_state()
        if state == self._last_display_state:
            return
        self._last_display_state = state
        _log(f"Poll display: broadcasting exposure={state['exposure']:.3f} "
             f"channel={state['channel']} zoom={state['zoom']} pan={state['pan']}")
        manager.broadcast_display_state(state)
