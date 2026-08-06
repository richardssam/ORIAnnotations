#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Resolve protocol *view* frames to *media* frames against an OTIO structure.

The sync protocol reports a playhead as a **view frame** — a 0-based offset
into whatever is loaded, where frame 0 is the first frame of the loaded view
(see the ``project_sync_frame_base`` note). It is *not* a media/source frame.
A clip whose ``source_range`` is ``None`` (a legitimate OTIO state meaning
"use the whole ``available_range``", used by hosts like xStudio to carry
embedded timecode) maps view frame 0 onto a media frame such as 98499.

This module turns a view frame into the media frame it addresses, using OTIO's
own time transforms:

* ``sequence`` view mode — ``Track.child_at_time`` finds the clip under the
  playhead and ``Track.transformed_time`` expresses the view time in that
  clip's media/source space (which composes the ``source_range`` /
  ``available_range`` offset automatically, so ``source_range is None`` needs
  no special case).
* ``source`` view mode — a single clip is loaded in isolation, so the media
  frame is a direct offset into that clip's effective range.

Callers that instead copied the view frame straight into ``source_range`` (the
old ``convert_recording_to_timeline`` behaviour) silently emitted clips whose
source frames fell outside the media's real range — the media rendered black
and any annotation overlays sat on the wrong picture.
"""

from __future__ import annotations

from typing import Any

import opentimelineio as otio
from opentimelineio.opentime import RationalTime


class FrameResolutionError(ValueError):
    """A view frame could not be resolved to a media frame.

    Raised when the clip under the playhead has neither a ``source_range`` nor
    a ``media_reference.available_range`` (so its media frames are unknown), or
    when the playhead lands on something that is not a clip. Resolving to a raw
    view frame instead would address nonexistent media, so we fail loudly.
    """


def clip_effective_range(clip: Any) -> otio.opentime.TimeRange | None:
    """Return the frame range that addresses *clip*'s media.

    Checks ``source_range`` first; when that is ``None`` — meaning "use the
    whole media" — falls back to ``media_reference.available_range``, which is
    where hosts like xStudio store the real embedded-timecode range.

    :param clip: An OTIO clip (or any object exposing ``source_range`` /
        ``media_reference``).
    :returns: The effective :class:`~opentimelineio.opentime.TimeRange`, or
        ``None`` when neither range is set.
    """
    rng = getattr(clip, "source_range", None)
    if rng is None:
        ref = getattr(clip, "media_reference", None)
        rng = getattr(ref, "available_range", None) if ref is not None else None
    return rng


def resolve_view_frame(
    track: Any,
    view_frame: float,
    *,
    view_mode: str = "sequence",
    selected_clip: Any = None,
    rate: float = 24.0,
) -> tuple[Any, int]:
    """Resolve a *view* frame to ``(clip, media_frame)``.

    :param track: The active video :class:`~opentimelineio.schema.Track` for
        ``sequence`` mode. Ignored in ``source`` mode.
    :param view_frame: The protocol playhead as a 0-based view/timeline frame.
    :param view_mode: ``"sequence"`` (resolve against the track's clips) or
        ``"source"`` (resolve directly against ``selected_clip``).
    :param selected_clip: The single clip loaded in ``source`` mode.
    :param rate: Frame rate of the incoming ``view_frame``.
    :returns: The resolved clip and the integer media frame it addresses.
    :raises FrameResolutionError: when the target clip has no known frame range,
        the playhead lands on a non-clip, or ``source`` mode has no clip.
    """
    if view_mode == "source":
        if selected_clip is None:
            raise FrameResolutionError(
                "source view mode requires a selected clip to resolve against"
            )
        return _resolve_source(selected_clip, view_frame, rate)
    return _resolve_sequence(track, view_frame, rate)


def _resolve_source(clip: Any, view_frame: float, rate: float) -> tuple[Any, int]:
    """Resolve ``source``-mode: media frame = effective_range.start + view_frame."""
    eff = clip_effective_range(clip)
    if eff is None:
        raise FrameResolutionError(
            f"clip {_clip_label(clip)!r} has no source_range or available_range; "
            "cannot resolve view frame to media frame"
        )
    start = eff.start_time
    offset = RationalTime(view_frame, rate)
    if offset.rate != start.rate:
        offset = offset.rescaled_to(start.rate)
    return clip, int(round((start + offset).value))


def _resolve_sequence(track: Any, view_frame: float, rate: float) -> tuple[Any, int]:
    """Resolve ``sequence``-mode via ``child_at_time`` + ``transformed_time``."""
    if track is None:
        raise FrameResolutionError("sequence view mode requires an active track")
    t = RationalTime(view_frame, rate)
    try:
        child = track.child_at_time(t)
    except Exception as exc:  # out of bounds / empty track
        raise FrameResolutionError(
            f"view frame {view_frame} is outside the active track: {exc}"
        ) from exc
    if not isinstance(child, otio.schema.Clip):
        kind = type(child).__name__ if child is not None else "None"
        raise FrameResolutionError(
            f"view frame {view_frame} lands on a {kind}, not a clip"
        )
    if clip_effective_range(child) is None:
        raise FrameResolutionError(
            f"clip {_clip_label(child)!r} under view frame {view_frame} has no "
            "source_range or available_range; cannot resolve to a media frame"
        )
    media = track.transformed_time(t, child)
    return child, int(round(media.value))


def _clip_label(clip: Any) -> str:
    """Best-effort human label (sync guid if present, else name)."""
    meta = getattr(clip, "metadata", None)
    if meta is not None:
        try:
            guid = meta.get("sync", {}).get("guid")
            if guid:
                return str(guid)
        except Exception:
            pass
    return str(getattr(clip, "name", "<clip>"))
