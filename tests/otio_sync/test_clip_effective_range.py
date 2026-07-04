"""Tests for utils._clip_effective_range (rvplugin/ori_sync).

Regression test for a live-sync bug: a Clip with source_range=None (a
legitimate OTIO state meaning "use the whole available_range") was being
treated as native/no-timecode by code that checked only clip.source_range,
silently misplacing annotations for any clip relying on this fallback
(confirmed live: xStudio clips with real embedded timecode and no explicit
source_range override).
"""

import os
import importlib.util
import unittest

import opentimelineio as otio

# rvplugin/ori_sync/utils.py and xstudio_plugin/ori_sync/utils.py share the
# bare module name "utils" — a plain `sys.path.append` + `import utils` would
# silently resolve to whichever one another test in the same process happened
# to import first (a pre-existing suite-wide fragility, e.g. between
# test_xstudio_annotations.py and test_openrv_annotations.py). Load this
# specific file directly, under a private name, so this test is independent
# of import order.
_utils_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../rvplugin/ori_sync/utils.py"))
_spec = importlib.util.spec_from_file_location("_rv_ori_sync_utils", _utils_path)
_rv_utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rv_utils)
_clip_effective_range = _rv_utils._clip_effective_range


def _make_clip(source_range=None, available_range=None, target_url="/x.mov"):
    ref = otio.schema.ExternalReference(target_url=target_url)
    if available_range is not None:
        ref.available_range = available_range
    clip = otio.schema.Clip(name="c", media_reference=ref, source_range=source_range)
    return clip


class TestClipEffectiveRange(unittest.TestCase):
    def test_uses_source_range_when_present(self):
        sr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(10, 24),
            duration=otio.opentime.RationalTime(5, 24),
        )
        clip = _make_clip(source_range=sr)
        self.assertEqual(_clip_effective_range(clip), (10, 14))

    def test_falls_back_to_available_range_when_source_range_none(self):
        # The exact shape of the live bug: source_range=None, real timecode
        # lives on media_reference.available_range instead.
        avail = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(96899, 24),
            duration=otio.opentime.RationalTime(101, 24),
        )
        clip = _make_clip(source_range=None, available_range=avail)
        self.assertEqual(_clip_effective_range(clip), (96899, 96999))

    def test_none_when_neither_range_set(self):
        clip = _make_clip(source_range=None, available_range=None)
        self.assertIsNone(_clip_effective_range(clip))

    def test_source_range_takes_priority_over_available_range(self):
        sr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(5, 24),
            duration=otio.opentime.RationalTime(3, 24),
        )
        avail = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(96899, 24),
            duration=otio.opentime.RationalTime(101, 24),
        )
        clip = _make_clip(source_range=sr, available_range=avail)
        self.assertEqual(_clip_effective_range(clip), (5, 7))

    def test_frame_coverage_check_matches_playback_sync_usage(self):
        # Mirrors _clip_guid_for_media_and_frame's "start <= frame <= end" check.
        avail = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(96899, 24),
            duration=otio.opentime.RationalTime(101, 24),
        )
        clip = _make_clip(source_range=None, available_range=avail)
        start, end = _clip_effective_range(clip)
        self.assertTrue(start <= 96899 <= end)
        self.assertFalse(start <= 97001 <= end)


if __name__ == "__main__":
    unittest.main()
