"""Structural, guid-tolerant comparison of OTIO timelines.

The OTIO-import sync test (§9 of the ``otio-import-sync`` change) confirms that
an imported ``.otio`` survives a round-trip through an app: imported into RV (or
xStudio), synced to a peer, exported again, and compared back to the original
reference file.

Two timelines that describe the *same edit* will not be byte-identical after a
round-trip — sync GUIDs are freshly minted, media URLs get absolutized, and clip
names may not survive RV's node graph. What MUST stay invariant is the **cut
structure**: for each video track, the ordered list of cuts, where each cut is
``(media_basename, start_frame, duration_frames)``. This module reduces a
timeline to that essence so the comparison ignores the volatile parts.
"""

import os

import opentimelineio as otio


def _is_video_track(track):
    # ``kind`` is an enum in some OTIO builds and a plain string in others.
    return str(track.kind) in ("Video", "TrackKind.Video", "TrackKind.Video")


def _media_basename(clip):
    """Return the bare filename of a clip's media, or '' if none.

    Normalizes away absolute-vs-relative path differences (RV exports absolute
    paths; the reference uses ``./encoded/seq_A.mov``) by keeping only the
    basename.
    """
    ref = clip.media_reference
    url = getattr(ref, "target_url", None)
    if not url:
        return ""
    # Strip any URL query/fragment, then take the basename.
    url = url.split("?")[0].split("#")[0]
    return os.path.basename(url.rstrip("/"))


def _cut(clip):
    """Reduce a clip to ``(media_basename, start_frame, duration_frames)``."""
    sr = clip.source_range
    if sr is None:
        return (_media_basename(clip), None, None)
    start = otio.opentime.to_frames(sr.start_time, sr.start_time.rate)
    dur = otio.opentime.to_frames(sr.duration, sr.duration.rate)
    return (_media_basename(clip), start, dur)


def cut_structure(timeline):
    """Return the guid/path/name-invariant cut structure of *timeline*.

    :param timeline: an ``otio.schema.Timeline``.
    :returns: ``[(track_name, [(media_basename, start_frame, duration), ...]), ...]``
        for each video track, in order.
    """
    out = []
    for track in timeline.tracks:
        if not _is_video_track(track):
            continue
        cuts = [_cut(c) for c in track if isinstance(c, otio.schema.Clip)]
        out.append((track.name or "", cuts))
    return out


def load_cut_structure(path):
    """Load an ``.otio`` file and return its :func:`cut_structure`."""
    return cut_structure(otio.adapters.read_from_file(path))


def compare(reference, candidate, ignore_track_names=True):
    """Compare two timelines (or their cut structures) for structural equality.

    Accepts ``otio.schema.Timeline`` objects or pre-computed cut structures.

    :param reference: the expected timeline / cut structure.
    :param candidate: the timeline / cut structure to check.
    :param ignore_track_names: when True, compare only per-track cut lists, not
        the track names (RV may name the video track "Video" while another
        producer differs).
    :returns: ``(equal: bool, differences: list[str])``.
    """
    ref = reference if isinstance(reference, list) else cut_structure(reference)
    cand = candidate if isinstance(candidate, list) else cut_structure(candidate)

    diffs = []
    if len(ref) != len(cand):
        diffs.append(f"video track count: reference={len(ref)} candidate={len(cand)}")
        return (False, diffs)

    for i, ((r_name, r_cuts), (c_name, c_cuts)) in enumerate(zip(ref, cand)):
        if not ignore_track_names and r_name != c_name:
            diffs.append(f"track[{i}] name: reference={r_name!r} candidate={c_name!r}")
        if len(r_cuts) != len(c_cuts):
            diffs.append(
                f"track[{i}] clip count: reference={len(r_cuts)} candidate={len(c_cuts)}"
            )
            continue
        for j, (rc, cc) in enumerate(zip(r_cuts, c_cuts)):
            if rc != cc:
                diffs.append(
                    f"track[{i}] cut[{j}]: reference={rc} candidate={cc}"
                )

    return (len(diffs) == 0, diffs)
