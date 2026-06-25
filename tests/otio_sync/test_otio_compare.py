"""Tests for the structural, guid-tolerant OTIO comparison (§9.3).

Validates the comparison logic against the real reference timeline
``test_media/source/otio_test_quicktime.otio`` so the round-trip assertion the
sync test relies on is proven independently of a live RV session.
"""

import os
import sys
import copy

import opentimelineio as otio

sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../sync_test/python"))
)

from sync_test import otio_compare

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
REFERENCE = os.path.join(REPO_ROOT, "test_media/source/otio_test_quicktime.otio")


def _reference_timeline():
    return otio.adapters.read_from_file(REFERENCE)


def test_reference_cut_structure_is_the_known_20_cut_pattern():
    """The reference is 20 one-frame cuts alternating A/B/C/D at frames 100..119."""
    struct = otio_compare.load_cut_structure(REFERENCE)
    assert len(struct) == 1  # single video track
    _track_name, cuts = struct[0]
    assert len(cuts) == 20

    expected = [
        (f"seq_{'ABCD'[i % 4]}.mov", 100 + i, 1) for i in range(20)
    ]
    assert cuts == expected


def test_identical_timeline_compares_equal():
    tl = _reference_timeline()
    equal, diffs = otio_compare.compare(tl, copy.deepcopy(tl))
    assert equal, diffs


def test_tolerant_of_guids_absolute_paths_and_clip_names():
    """A round-tripped export (fresh guids, absolute paths, renamed clips) still
    compares equal — these are the expected, ignorable differences."""
    ref = _reference_timeline()
    mutated = copy.deepcopy(ref)

    for ti, track in enumerate(mutated.tracks):
        track.metadata["sync"] = {"guid": "track-guid-xyz"}
        for ci, clip in enumerate(track):
            if not isinstance(clip, otio.schema.Clip):
                continue
            # Fresh sync guid (volatile).
            clip.metadata["sync"] = {"guid": f"clip-{ti}-{ci}", "origin": "otio_import"}
            # Clip name may not survive RV's node graph round-trip.
            clip.name = f"renamed_{ci}"
            # Media absolutized + a redundant './' segment, as RV would store it.
            ref_obj = clip.media_reference
            if isinstance(ref_obj, otio.schema.ExternalReference) and ref_obj.target_url:
                base = os.path.basename(ref_obj.target_url)
                ref_obj.target_url = f"/Users/someone/project/./encoded/{base}"

    equal, diffs = otio_compare.compare(ref, mutated)
    assert equal, diffs


def test_detects_a_dropped_clip():
    ref = _reference_timeline()
    mutated = copy.deepcopy(ref)
    # Remove the first clip from the video track — a real structural change.
    video = next(t for t in mutated.tracks if otio_compare._is_video_track(t))
    del video[0]

    equal, diffs = otio_compare.compare(ref, mutated)
    assert not equal
    assert any("clip count" in d for d in diffs)


def test_detects_a_changed_cut_in_point():
    ref = _reference_timeline()
    mutated = copy.deepcopy(ref)
    video = next(t for t in mutated.tracks if otio_compare._is_video_track(t))
    first = next(c for c in video if isinstance(c, otio.schema.Clip))
    # Shift the in-point: a cut-trim style change must be detected.
    sr = first.source_range
    first.source_range = otio.opentime.TimeRange(
        start_time=sr.start_time + otio.opentime.RationalTime(5, sr.start_time.rate),
        duration=sr.duration,
    )

    equal, diffs = otio_compare.compare(ref, mutated)
    assert not equal
    assert any("cut[0]" in d for d in diffs)
