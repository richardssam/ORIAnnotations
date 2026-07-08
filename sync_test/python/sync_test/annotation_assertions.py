"""Compute expected cross-app annotation geometry and assert round-trip fidelity.

These helpers deliberately reuse the *same* production codec functions/constants
the apps themselves use for each conversion (see
``otio_sync_core.rv_annotation_codec`` / ``otio_sync_core.xs_annotation_codec``)
rather than hardcoding expected numbers, so an assertion built from them fails
exactly when a codec's forward and reverse directions disagree — the shape of
bug this test harness exists to catch (see the ``sync-test-draw-annotation``
change design doc, decision D3/D4).
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from otio_sync_core import coords, rv_annotation_codec
from otio_sync_core.xs_annotation_codec import XS_FONT_SCALE

# Mirrors xstudio_plugin/ori_sync/annotation_sync.py's
# AnnotationSyncController.DEBOUNCE_SECONDS (0.25) and
# xstudio_plugin/ori_sync/ori_sync_plugin.py's ANNOTATION_SCAN_INTERVAL (1.0),
# generously padded. Duplicated here rather than imported: those modules
# import xStudio's own `xstudio.core` at module scope, which is only
# available inside xStudio's embedded interpreter, not this out-of-process
# harness — importing them here would make this module (and everything that
# imports it) fail outside a running xStudio. If those constants change, this
# bound should be revisited by hand.
XSTUDIO_ANNOTATION_CONVERGENCE_TIMEOUT = 10.0


#: Default shape geometry drawn by `openrv_hook.py::_draw_openrv_annotation`
#: (OTIO-normalized, H-norm/Y-up/centre-origin per `coords`), keyed by `kind`.
#: Single source of truth shared between the draw command's defaults and the
#: sync-test-frame-capture visual check, which needs the same ground truth to
#: project expected pixel geometry — see that change's design doc D4.
DEFAULT_SHAPE_GEOMETRY = {
    "pen": {"points": (-0.05, 0.0, 0.05, 0.0)},
    "rect": {"min": (-0.1, -0.1), "max": (0.1, 0.1)},
    "ellipse": {"min": (-0.1, -0.1), "max": (0.1, 0.1)},
    "arrow": {"start": (-0.1, -0.1), "end": (0.1, 0.1)},
}

#: xStudio's native default pen-stroke points (see
#: `xstudio_hook.py::_draw_xstudio_annotation`'s hardcoded
#: ``[-0.05, 0.0, ..., 0.05, 0.0, ...]``), in xStudio's own W-normalised/Y-down
#: space. Unlike RV's raw paint properties (already OTIO-normalised-equivalent
#: for position, per `coords`'s module docstring), xStudio's native stroke
#: coordinates are NOT usable directly as OTIO ground truth — they need the
#: same ``x_otio = x_xs * aspect_half``, ``y_otio = -y_xs * aspect_half``
#: conversion `xs_annotation_codec.xs_strokes_to_sync_events` applies.
_XSTUDIO_NATIVE_PEN_POINTS = (-0.05, 0.0, 0.05, 0.0)


def _xstudio_pen_geometry(aspect_half: Optional[float] = None) -> dict:
    aspect_half = coords.DEFAULT_ASPECT_HALF if aspect_half is None else aspect_half
    x0, y0, x1, y1 = _XSTUDIO_NATIVE_PEN_POINTS
    return {"points": (x0 * aspect_half, -y0 * aspect_half, x1 * aspect_half, -y1 * aspect_half)}


def shape_geometry_for_driver(
    kind: str, driver_name: str, aspect_half: Optional[float] = None
) -> Optional[dict]:
    """Return the OTIO-normalized ground-truth geometry a `draw_annotation`
    with the given `kind` actually produces when driven by `driver_name`.

    Only `pen` currently differs by driver: xStudio's native pen-stroke
    coordinates need the `aspect_half` conversion above, while RV's raw paint
    coordinates (used directly as `DEFAULT_SHAPE_GEOMETRY` for every kind,
    including RV-driven pen) don't. `rect`/`ellipse`/`arrow` are OpenRV-only
    drivers today (xStudio has no native shape-drawing broadcast path yet), so
    they only ever need `DEFAULT_SHAPE_GEOMETRY`.

    Returns None if `kind` has no known geometry at all.
    """
    if kind == "pen" and driver_name == "xstudio":
        return _xstudio_pen_geometry(aspect_half)
    return DEFAULT_SHAPE_GEOMETRY.get(kind)


def _otio_size_from_rv_pen_width(rv_width: float, aspect_half: Optional[float] = None) -> float:
    return rv_width / rv_annotation_codec.RV_WIDTH_SCALE


def _otio_size_from_xstudio_pen_thickness(xs_thickness: float, aspect_half: Optional[float] = None) -> float:
    aspect_half = coords.DEFAULT_ASPECT_HALF if aspect_half is None else aspect_half
    return 2.0 * xs_thickness * aspect_half


def _otio_size_from_rv_border_width(rv_border_width: float, aspect_half: Optional[float] = None) -> float:
    return rv_border_width


def _otio_size_from_rv_arrow_thickness(rv_thickness: float, aspect_half: Optional[float] = None) -> float:
    return rv_thickness * 2.0


#: (kind, driver_app_name) -> function(nominal_native_value, aspect_half=None)
#: -> OTIO-normalized size.
#: Exposes the *intermediate* OTIO-normalized geometry each `expected_*` formula
#: below computes internally, so a visual check can project it into pixel space
#: (via `coords.otio_to_px`) without caring which peer app's native units the
#: numeric round-trip check happens to be comparing against.
_OTIO_SIZE_FROM_DRIVER_NOMINAL = {
    ("pen", "openrv"): _otio_size_from_rv_pen_width,
    ("pen", "xstudio"): _otio_size_from_xstudio_pen_thickness,
    ("rect", "openrv"): _otio_size_from_rv_border_width,
    ("ellipse", "openrv"): _otio_size_from_rv_border_width,
    ("arrow", "openrv"): _otio_size_from_rv_arrow_thickness,
}


def otio_size_from_driver_nominal(
    kind: str, driver_name: str, nominal: float, aspect_half: Optional[float] = None
) -> Optional[float]:
    """Return the OTIO-normalized size the driver's native `nominal` input maps to.

    Returns None if no formula is registered for `(kind, driver_name)`.
    """
    fn = _OTIO_SIZE_FROM_DRIVER_NOMINAL.get((kind, driver_name))
    if fn is None:
        return None
    return fn(nominal, aspect_half)


def expected_xstudio_thickness_from_rv_pen_width(
    rv_width: float, aspect_half: Optional[float] = None
) -> float:
    """RV-native pen width -> OTIO size (RV reverse codec) -> xStudio thickness (xStudio forward codec)."""
    aspect_half = coords.DEFAULT_ASPECT_HALF if aspect_half is None else aspect_half
    otio_size = _otio_size_from_rv_pen_width(rv_width, aspect_half)
    return otio_size / (2.0 * aspect_half)


def expected_rv_width_from_xstudio_pen_thickness(
    xs_thickness: float, aspect_half: Optional[float] = None
) -> float:
    """xStudio-native pen thickness -> OTIO size (xStudio reverse codec) -> RV width (RV forward codec)."""
    otio_size = _otio_size_from_xstudio_pen_thickness(xs_thickness, aspect_half)
    return otio_size * rv_annotation_codec.RV_WIDTH_SCALE


def expected_xstudio_thickness_from_rv_border_width(
    rv_border_width: float, aspect_half: Optional[float] = None
) -> float:
    """RV-native rect borderWidth -> OTIO size (RV reverse shape codec) -> xStudio tessellated thickness (xStudio forward shape codec)."""
    aspect_half = coords.DEFAULT_ASPECT_HALF if aspect_half is None else aspect_half
    otio_size = _otio_size_from_rv_border_width(rv_border_width, aspect_half)
    return otio_size / (2.0 * aspect_half)


def expected_xstudio_thickness_from_rv_ellipse_border_width(
    rv_border_width: float, aspect_half: Optional[float] = None
) -> float:
    """RV-native ellipse borderWidth -> OTIO size (RV reverse shape codec) -> xStudio tessellated thickness (xStudio forward shape codec)."""
    return expected_xstudio_thickness_from_rv_border_width(rv_border_width, aspect_half)


def expected_xstudio_thickness_from_rv_arrow_thickness(
    rv_thickness: float, aspect_half: Optional[float] = None
) -> float:
    """RV-native arrow thickness -> OTIO size (RV reverse arrow codec) -> xStudio tessellated thickness (xStudio forward shape codec)."""
    aspect_half = coords.DEFAULT_ASPECT_HALF if aspect_half is None else aspect_half
    otio_size = _otio_size_from_rv_arrow_thickness(rv_thickness, aspect_half)
    return otio_size / (2.0 * aspect_half)


def expected_xstudio_font_size_from_rv_size(
    rv_size: float, aspect_half: Optional[float] = None
) -> float:
    """RV-native text ``.size`` -> OTIO font_size (RV reverse text codec) -> xStudio caption font_size (xStudio forward text codec).

    Imports ``XS_FONT_SCALE`` directly from ``xs_annotation_codec`` rather than
    duplicating the literal — this formula exists specifically because that
    constant was found to be miscalibrated; a second inline copy here would
    reintroduce the same drift risk it's meant to catch.
    """
    otio_font_size = rv_annotation_codec.rv_to_font_size(rv_size)
    return otio_font_size * XS_FONT_SCALE


def expected_xstudio_caption_position_from_rv_position(
    rv_position, aspect_half: Optional[float] = None
):
    """RV-native (OTIO-normalized) text position -> xStudio caption position.

    Same ``aspect_half`` transform already proven correct for pen/shape
    points (``x_xs = x_otio / aspect_half``, ``y_xs = -y_otio / aspect_half``).
    """
    aspect_half = coords.DEFAULT_ASPECT_HALF if aspect_half is None else aspect_half
    x_otio, y_otio = rv_position
    return (x_otio / aspect_half, -y_otio / aspect_half)


def wait_for_predicate(
    fetch_state: Callable[[], dict],
    predicate: Callable[[dict], bool],
    timeout: float,
    interval: float = 1.0,
) -> Optional[dict]:
    """Poll ``fetch_state()`` until ``predicate(state)`` is true or ``timeout`` elapses.

    Returns the last fetched state regardless of outcome, so callers get a
    useful failure payload whether or not the predicate ever matched.
    """
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        state = fetch_state()
        if state and "error" not in state and predicate(state):
            return state
        time.sleep(interval)
    return state


def assert_almost_equal(actual: Optional[float], expected: float, tolerance: float = 1e-4, msg: str = "") -> None:
    if actual is None or abs(actual - expected) > tolerance:
        raise AssertionError(
            f"{msg}: expected ~{expected!r} (tolerance {tolerance!r}), got {actual!r}"
        )
