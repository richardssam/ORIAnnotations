#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""DisplaySyncController — viewport display-state sync (zoom, exposure, channel)."""

import json
import time

from xstudio.api.intrinsic.viewport import Viewport
from xstudio.core import serialise_atom

from otio_sync_core.manager import STATE_SYNCED  # noqa: E402
from otio_sync_core.authority import CHANNEL_DISPLAY  # noqa: E402

from .utils import _log, _log_exc, bounded_timeout

# Bounded timeout (ms) for quick poll-thread viewport reads — well below the
# 100 s default so a stale viewport actor fails fast instead of freezing.
_DISPLAY_TIMEOUT_MS = 2000

# How long after putting the snapshot's first timeline on screen a resulting
# show_atom is still attributable to that call rather than to a user.
#
# Deliberately longer than apply_selection's 0.5 s for the same guard: that one
# covers an in-session selection switch, where the show_atom follows promptly.
# This one follows a session build, so the event trails media loading rather
# than a selection change — measured at 585 ms on 2026-08-15, which 0.5 s would
# have missed by 85 ms. The cost of being generous is that a user who joins and
# immediately picks a clip has that first pick suppressed; the cost of being
# tight is that joining moves every other peer, which is the failure this
# exists to prevent.
_ON_SCREEN_SOURCE_ECHO_S = 3.0

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
        # Handle to the AnnotationsUI plugin, retained (not just a subscribe-time
        # local) so remote annotations_visible changes can be applied via its
        # "Visibility" attribute.  Acquired at connect time by
        # acquire_annotations_ui(); this controller is its only reader.
        self._ann_ui_plugin = None

    def acquire_annotations_ui(self) -> None:
        """Retain a handle to the AnnotationsUI plugin (called at connect time).

        We do NOT subscribe to it: nothing is ever broadcast on a plugin's
        events group.  Failure is non-fatal — the handle stays None and
        annotation-visibility applies are skipped.
        """
        try:
            self._ann_ui_plugin = self.plugin.get_plugin("AnnotationsUI")
        except Exception:
            _log_exc("Could not get a handle to the AnnotationsUI plugin")

    def reset(self) -> None:
        """Clear display state on disconnect."""
        self._viewport = None
        self._pending_on_screen_source = None
        self._last_display_state = {}
        self._xs_base_scale = None
        self._last_pinned_source_mode = None
        self._ann_ui_plugin = None
        self._last_display_scan = 0.0
        self._last_viewport_error_log_time = 0.0

    # ── viewport ──────────────────────────────────────────────────────────────

    def _apply_pending_on_screen_source(self) -> None:
        """Show the timeline the session build parked here, without asserting it.

        Joining a session is not a user action, and nothing about it may change
        what the session is looking at.  This call puts the *first* timeline of
        the snapshot on screen so the joiner has something to display —
        ``do_load_timelines`` sets ``_pending_on_screen_source`` to
        ``first_xs_timeline`` — and xStudio then fires a ``show_atom`` for it,
        which the selection handler reads as a fresh local isolation.

        Observed 2026-08-15 09:07:37: a peer joined a session whose host was on
        a later clip, mid-shot.  The joiner applied the deferred source at
        :09:07:36.952, the ``show_atom`` arrived 585 ms later, and the joiner
        broadcast ``mode=source`` with ``forcing frame=0`` for the first clip.
        The host applied it and jumped to the first clip at frame 1 —
        ``RECV selection: clip 'car_ACES_sRGB' ... source switch dispatched``.

        The guard is the same one ``apply_selection`` arms for the same reason
        (its own ``set_on_screen_source`` call).  It is armed here, at the call,
        rather than inferred afterwards from provenance: this peer knows it just
        set the source, and that is a fact rather than a deduction from a
        5-second settling window that may already have expired.

        A single helper rather than the two identical copies this replaced —
        the guard belongs to the call, and two call sites is how one of them
        ends up without it.
        """
        if self._pending_on_screen_source is None:
            return
        # Armed BEFORE the call: the show_atom can be dispatched from inside it.
        self.plugin.playback.suppress_selection_broadcast(_ON_SCREEN_SOURCE_ECHO_S)
        try:
            self.plugin.connection.api.session.set_on_screen_source(
                self._pending_on_screen_source
            )
            _log(
                "Applied deferred on-screen source:"
                f" {getattr(self._pending_on_screen_source, 'name', '?')}"
                f" (broadcast suppressed {_ON_SCREEN_SOURCE_ECHO_S:.1f}s — joining"
                " must not change what the session is viewing)"
            )
        except Exception:
            pass
        self._pending_on_screen_source = None

    def get_viewport(self) -> "Viewport | None":
        """Return a cached Viewport for the active xStudio window, or None on error."""
        if self._viewport is not None:
            self._apply_pending_on_screen_source()
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
        self._apply_pending_on_screen_source()
        return self._viewport

    # ── read ──────────────────────────────────────────────────────────────────

    def _read_annotations_visible(self) -> bool:
        """Return the session-wide annotation visibility, read from the
        ``AnnotationsUI`` plugin's ``"Visibility"`` boolean attribute (the
        state driven by the global 'V' "Toggle annotation visibility" hotkey).

        Defaults to visible (``True``) if the plugin handle isn't available
        or the attribute can't be read, matching this feature's documented
        "absent means visible" semantics.
        """
        ann_ui = self._ann_ui_plugin
        if ann_ui is None:
            return True
        try:
            return bool(ann_ui.get_attribute("Visibility").value())
        except Exception as e:
            _log(f"WARN _read_annotations_visible: {e}")
            return True

    def read_xs_display_state(self) -> dict:
        """Return a display state dict read from the active xStudio viewport.

        The colour_pipeline reads (``cp.exposure.value()``, ``cp.channel.value()``)
        are synchronous request_receive calls bounded only by the connection's
        100 s default.  ``bounded_timeout`` lowers that so a stale viewport actor
        fails fast instead of freezing the poll thread; on failure the cached
        viewport is dropped so the next call re-acquires a live one.
        """
        state: dict = {
            "pan": None, "zoom": None, "exposure": 0.0, "channel": "RGBA",
            "annotations_visible": self._read_annotations_visible(),
        }
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
            return {
                "pan": [0.0, 0.0], "zoom": 1.0, "exposure": 0.0, "channel": "RGBA",
                "annotations_visible": state["annotations_visible"],
            }
        return state

    # ── apply ─────────────────────────────────────────────────────────────────

    def apply_display_state(self, state: dict) -> None:
        """Apply a received display state dict to the local xStudio viewport."""
        vp = self.get_viewport()
        if vp is None:
            return

        # Feeds claim_lease()'s horizon (design.md D4): an asynchronous
        # attribute-changed callback attributable to this apply must not be
        # allowed to claim the display lease the sending peer still holds.
        self.plugin.stamp_remote_apply(CHANNEL_DISPLAY)

        pan = state.get("pan")
        zoom = state.get("zoom")
        exposure = state.get("exposure", 0.0)
        channel = state.get("channel", "RGBA")
        annotations_visible = state.get("annotations_visible", True)

        # Setting the "Visibility" boolean attribute directly is a no-op:
        # AnnotationsUI::attribute_changed() has no branch for it at all, only
        # for action_attribute_. The real 'V' hotkey path sets
        # action_attribute_'s value to ["HideVisibility"/"ShowVisibility"],
        # which both updates annotations_visible_ AND (critically) calls
        # send_event(...) to notify AnnotationsCore's hide_all_drawings_ flag
        # -- the thing that actually affects rendering. Drive the same path.
        ann_ui = self._ann_ui_plugin
        if ann_ui is not None:
            try:
                action = "ShowVisibility" if annotations_visible else "HideVisibility"
                ann_ui.set_attribute("action_attribute", [action])
            except Exception as e:
                _log(f"RECV display: annotations_visible set failed: {e}")

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
            "annotations_visible": annotations_visible,
        }
        _log(f"RECV display exposure={exposure:.3f} channel={channel} "
             f"zoom={zoom} pan={pan} annotations_visible={annotations_visible}")

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
        self.plugin.claim_lease(CHANNEL_DISPLAY)
        manager.broadcast_display_state(state)
