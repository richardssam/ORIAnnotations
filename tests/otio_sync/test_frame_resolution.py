"""Tests for otio_sync_core.frame_resolution.

Covers view-frame -> media-frame resolution against an OTIO structure:
- source_range=None clips with embedded-timecode available_range
- explicit trimmed source_range
- a non-zero (frameStart != 0) media base and a rate mismatch
- source view mode as a direct offset into the selected clip
- the fail-loudly error when neither range is known
"""

import os
import sys
import unittest

import opentimelineio as otio
from opentimelineio.opentime import RationalTime, TimeRange

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../python")))

from otio_sync_core.frame_resolution import (  # noqa: E402
    FrameResolutionError,
    clip_effective_range,
    resolve_view_frame,
)


def _clip(name, *, source_range=None, avail_start=None, avail_dur=None, rate=24.0):
    ref = otio.schema.ExternalReference(target_url=f"/{name}.mov")
    if avail_start is not None:
        ref.available_range = TimeRange(
            RationalTime(avail_start, rate), RationalTime(avail_dur, rate)
        )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=source_range)


class TestClipEffectiveRange(unittest.TestCase):
    def test_prefers_source_range(self):
        sr = TimeRange(RationalTime(10, 24.0), RationalTime(5, 24.0))
        c = _clip("a", source_range=sr, avail_start=98499, avail_dur=101)
        self.assertEqual(clip_effective_range(c).start_time.value, 10)

    def test_falls_back_to_available_range(self):
        c = _clip("a", avail_start=98499, avail_dur=101)
        self.assertEqual(clip_effective_range(c).start_time.value, 98499)

    def test_none_when_neither(self):
        c = _clip("a")
        self.assertIsNone(clip_effective_range(c))


class TestResolveSequence(unittest.TestCase):
    def test_source_range_none_embedded_timecode(self):
        # The demo case: view frame 31 on media that really starts at 98499.
        track = otio.schema.Track()
        track.append(_clip("A", avail_start=98499, avail_dur=101))
        clip, media = resolve_view_frame(track, 31, view_mode="sequence")
        self.assertEqual(clip.name, "A")
        self.assertEqual(media, 98530)  # 98499 + 31, inside [98499, 98600)

    def test_explicit_trimmed_source_range(self):
        track = otio.schema.Track()
        sr = TimeRange(RationalTime(98530, 24.0), RationalTime(20, 24.0))
        track.append(_clip("B", source_range=sr, avail_start=98499, avail_dur=101))
        clip, media = resolve_view_frame(track, 5, view_mode="sequence")
        self.assertEqual(media, 98535)  # 98530 + 5

    def test_boundary_crossing_two_clips(self):
        track = otio.schema.Track()
        track.append(_clip("A", avail_start=98499, avail_dur=50))
        track.append(_clip("B", avail_start=500, avail_dur=50))
        self.assertEqual(resolve_view_frame(track, 49, view_mode="sequence"),
                         (track[0], 98548))
        # view frame 50 crosses into clip B, resetting into B's media space
        clip, media = resolve_view_frame(track, 50, view_mode="sequence")
        self.assertEqual((clip.name, media), ("B", 500))

    def test_rate_mismatch_query_rescaled(self):
        # Media at 48fps; query a 24fps view frame -> rescaled before offset.
        track = otio.schema.Track()
        track.append(_clip("A", avail_start=1000, avail_dur=200, rate=48.0))
        clip, media = resolve_view_frame(track, 10, view_mode="sequence", rate=24.0)
        # 10 @24fps = 20 @48fps -> 1000 + 20
        self.assertEqual(media, 1020)

    def test_missing_range_fails_loudly(self):
        track = otio.schema.Track()
        track.append(_clip("A"))  # no source_range, no available_range
        with self.assertRaises(FrameResolutionError):
            resolve_view_frame(track, 0, view_mode="sequence")

    def test_gap_under_playhead_fails(self):
        track = otio.schema.Track()
        track.append(otio.schema.Gap(
            source_range=TimeRange(RationalTime(0, 24.0), RationalTime(10, 24.0))))
        with self.assertRaises(FrameResolutionError):
            resolve_view_frame(track, 3, view_mode="sequence")


class TestResolveSource(unittest.TestCase):
    def test_direct_offset_into_selected_clip(self):
        c = _clip("S", avail_start=98499, avail_dur=101)
        clip, media = resolve_view_frame(
            None, 12, view_mode="source", selected_clip=c
        )
        self.assertEqual((clip.name, media), ("S", 98511))

    def test_source_requires_clip(self):
        with self.assertRaises(FrameResolutionError):
            resolve_view_frame(None, 0, view_mode="source", selected_clip=None)

    def test_source_missing_range_fails(self):
        c = _clip("S")
        with self.assertRaises(FrameResolutionError):
            resolve_view_frame(None, 0, view_mode="source", selected_clip=c)


if __name__ == "__main__":
    unittest.main()
