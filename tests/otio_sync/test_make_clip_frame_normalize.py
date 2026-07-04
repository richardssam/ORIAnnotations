"""Tests for SequenceSyncController._make_clip's frame-1 normalization (rvplugin/ori_sync).

Regression test for a live-sync bug: RV reports sourceMediaInfo().startFrame=1
as its own internal convention for media with NO real embedded timecode (see
playback_sync.py::_frame_base). Embedding that literal "1" into the OTIO
available_range sent to peers made xStudio treat frame 1 as the clip's start,
skipping the true first frame (confirmed live: xStudio displayed the second
frame of a non-timecode QuickTime instead of the first).
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

import opentimelineio as otio

# rvplugin/ori_sync/utils.py and xstudio_plugin/ori_sync/utils.py share the
# bare module name "utils" (see test_clip_effective_range.py) -- mock rv/PySide
# and sys.path-insert rvplugin/ori_sync BEFORE importing sequence_sync, and run
# this file standalone if colliding with xstudio-side test imports elsewhere.
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "rvplugin/ori_sync"))

sys.modules.setdefault("PySide2", MagicMock())
sys.modules.setdefault("PySide2.QtCore", MagicMock())
sys.modules.setdefault("PySide6", MagicMock())
sys.modules.setdefault("PySide6.QtCore", MagicMock())

_mock_rv = MagicMock()
sys.modules["rv"] = _mock_rv
sys.modules["rv.commands"] = _mock_rv.commands

from sequence_sync import SequenceSyncController  # noqa: E402


def _make_controller():
    """A minimal, uninitialised instance for calling _make_clip directly."""
    ctrl = SequenceSyncController.__new__(SequenceSyncController)
    ctrl.plugin = MagicMock()
    ctrl.plugin.playback._clip_guid_for_media_path.side_effect = Exception("no lookup in test")
    return ctrl


class TestMakeClipFrameNormalize(unittest.TestCase):
    def setUp(self):
        _mock_rv.commands.reset_mock()
        _mock_rv.commands.getStringProperty.return_value = ["/x/car.mov"]
        _mock_rv.commands.getFloatProperty.return_value = [24.0]

    def test_no_timecode_start_frame_1_normalizes_to_0(self):
        _mock_rv.commands.sourceMediaInfo.return_value = {"startFrame": 1, "endFrame": 101}
        ctrl = _make_controller()
        clip = ctrl._make_clip("sourceGroup0_source", fps=24.0)
        self.assertIsNotNone(clip)
        avail = clip.media_reference.available_range
        self.assertEqual(avail.start_time.value, 0.0)
        self.assertEqual(avail.duration.value, 101.0)

    def test_real_timecode_start_frame_unchanged(self):
        # 01:00:00:00 @ 24fps -> startFrame=86400; must NOT be treated as the
        # synthetic no-timecode default (only exactly 1 is normalized).
        _mock_rv.commands.sourceMediaInfo.return_value = {"startFrame": 86400, "endFrame": 86500}
        ctrl = _make_controller()
        clip = ctrl._make_clip("sourceGroup0_source", fps=24.0)
        self.assertIsNotNone(clip)
        avail = clip.media_reference.available_range
        self.assertEqual(avail.start_time.value, 86400.0)
        self.assertEqual(avail.duration.value, 101.0)

    def test_start_frame_0_unchanged(self):
        _mock_rv.commands.sourceMediaInfo.return_value = {"startFrame": 0, "endFrame": 100}
        ctrl = _make_controller()
        clip = ctrl._make_clip("sourceGroup0_source", fps=24.0)
        self.assertIsNotNone(clip)
        avail = clip.media_reference.available_range
        self.assertEqual(avail.start_time.value, 0.0)


if __name__ == "__main__":
    unittest.main()
