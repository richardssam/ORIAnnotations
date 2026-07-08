#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Host-neutral annotation coordinate geometry.

This module is the single authoritative source for the OTIO-normalized
coordinate space that every host (RV, xStudio, …) and the pixel/testchart
ground truth convert to and from.

.. rubric:: Scope boundary

``coords`` owns **only** host-neutral geometry: the aspect-ratio scale, the
pixel ↔ OTIO-normalized transforms, and shared annotation defaults.

Host-specific *unit* conversions do **not** live here — they belong in that
host's codec, mirroring how :mod:`otio_sync_core.xs_annotation_codec` keeps
xStudio's ``font_size * 2.5`` factor inline. In particular, RV's
``font_size ↔ .size`` factor (``RV_FONT_SCALE``) and pen-width factor
(``RV_WIDTH_SCALE``) live in :mod:`otio_sync_core.rv_annotation_codec`, not
here.

.. rubric:: OTIO-normalized space

H-normalised, Y-up, centre-origin::

    nx = (px - W/2) / H
    ny = -((py - H/2) / H)

so ``x ∈ [−W/(2H), +W/(2H)]`` and ``y ∈ [−0.5, +0.5]``. This matches the RV
paint / OTIO ``SyncEvent`` convention.
"""

from __future__ import annotations

from typing import Tuple

# --- Shared annotation defaults -------------------------------------------

#: ``aspect_half`` fallback for 1920×1080 media (``1920 / (2 × 1080)``).
DEFAULT_ASPECT_HALF: float = 8.0 / 9.0

#: RV-neutral letter spacing. xStudio captions have no spacing concept; ``0.0``
#: collapses letter spacing in RV, so xs-originated captions default to this.
DEFAULT_SPACING: float = 0.8

#: Default caption font size (host-neutral OTIO ``TextAnnotation.font_size``).
DEFAULT_FONT_SIZE: float = 50.0


# --- Geometry -------------------------------------------------------------

def aspect_half(width: float, height: float) -> float:
    """Return ``width / (2 * height)`` — the OTIO-norm x half-extent.

    Falls back to :data:`DEFAULT_ASPECT_HALF` when *height* is zero or missing,
    so callers never divide by zero on unresolved media resolution.

    :param width: Media width in pixels.
    :param height: Media height in pixels.
    :returns: ``W / (2H)``, or :data:`DEFAULT_ASPECT_HALF` if *height* ≤ 0.
    """
    if not height or height <= 0:
        return DEFAULT_ASPECT_HALF
    return float(width) / (2.0 * float(height))


def px_to_otio(px: float, py: float, width: float, height: float) -> Tuple[float, float]:
    """Convert image pixel coordinates to OTIO-normalized (H-norm, Y-up).

    :param px: Pixel x (0 = left edge).
    :param py: Pixel y (0 = top edge).
    :param width: Media width in pixels.
    :param height: Media height in pixels.
    :returns: ``(nx, ny)`` in OTIO-normalized space.
    """
    nx = (px - width / 2.0) / height
    ny = -((py - height / 2.0) / height)
    return float(nx), float(ny)


def otio_to_px(x: float, y: float, width: float, height: float) -> Tuple[float, float]:
    """Convert OTIO-normalized coordinates back to image pixels.

    Inverse of :func:`px_to_otio`.

    :param x: OTIO-normalized x.
    :param y: OTIO-normalized y.
    :param width: Media width in pixels.
    :param height: Media height in pixels.
    :returns: ``(px, py)`` in pixel space (floats; caller rounds if needed).
    """
    px = x * height + width / 2.0
    py = -y * height + height / 2.0
    return float(px), float(py)
