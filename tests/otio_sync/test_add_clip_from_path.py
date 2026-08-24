"""Tests for SequenceSyncController.add_clip_from_path (rvplugin/ori_sync).

Pins the behaviour of the "Add Clip to Timeline…" action after it moved out of
``plugin.py``'s menu callback and into the controller (change:
rv-plugin-encapsulation). The spec requires peers to observe an *identical*
``insert_child`` message before and after that move, so these tests assert the
exact clip name, target track guid, and time range the old inline code produced
-- including its two quirks: the ``end - start + 1`` duration and the
decrement-start-unless-zero rule.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

import opentimelineio as otio

# Same import dance as test_make_clip_frame_normalize.py: rvplugin/ori_sync and
# xstudio_plugin/ori_sync both expose a bare "utils" module, so mock rv/PySide
# and sys.path-insert rvplugin/ori_sync BEFORE importing sequence_sync.
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


def _make_controller(track_guid="track-guid-0"):
    ctrl = SequenceSyncController.__new__(SequenceSyncController)
    ctrl.plugin = MagicMock()
    ctrl._active_media_track_guid = track_guid
    return ctrl


class TestAddClipFromPath(unittest.TestCase):
    def setUp(self):
        # reset_mock() alone leaves side_effect in place, so the fallback test's
        # RuntimeError would leak into every test that sorts after it.
        _mock_rv.commands.reset_mock(side_effect=True, return_value=True)
        _mock_rv.commands.fps.return_value = 24.0
        _mock_rv.commands.inPoint.return_value = 1
        _mock_rv.commands.outPoint.return_value = 10

    def test_adds_source_and_inserts_clip_on_active_track(self):
        ctrl = _make_controller()
        clip = ctrl.add_clip_from_path("/media/shots/car.mov")

        _mock_rv.commands.addSource.assert_called_once_with("/media/shots/car.mov")

        ctrl.plugin.sync_manager.insert_child.assert_called_once()
        parent_guid, inserted = ctrl.plugin.sync_manager.insert_child.call_args[0]
        self.assertEqual(parent_guid, "track-guid-0")
        self.assertIs(inserted, clip)

        self.assertEqual(clip.name, "car.mov")
        ref = clip.media_reference
        self.assertIsInstance(ref, otio.schema.ExternalReference)
        self.assertEqual(ref.target_url, "/media/shots/car.mov")

    def test_time_range_matches_the_pre_extraction_arithmetic(self):
        """duration = out - in + 1; start decremented only when non-zero."""
        ctrl = _make_controller()
        clip = ctrl.add_clip_from_path("/media/shots/car.mov")

        rng = clip.media_reference.available_range
        # inPoint=1 -> start decremented to 0; outPoint=10 -> duration 10.
        self.assertEqual(rng.start_time.value, 0)
        self.assertEqual(rng.duration.value, 10)
        self.assertEqual(rng.start_time.rate, 24.0)

    def test_start_of_zero_is_not_decremented(self):
        _mock_rv.commands.inPoint.return_value = 0
        _mock_rv.commands.outPoint.return_value = 4
        ctrl = _make_controller()
        clip = ctrl.add_clip_from_path("/media/shots/car.mov")

        rng = clip.media_reference.available_range
        self.assertEqual(rng.start_time.value, 0)
        self.assertEqual(rng.duration.value, 5)

    def test_falls_back_to_default_range_when_rv_query_raises(self):
        _mock_rv.commands.inPoint.side_effect = RuntimeError("no in point")
        ctrl = _make_controller()
        clip = ctrl.add_clip_from_path("/media/shots/car.mov")

        rng = clip.media_reference.available_range
        self.assertEqual(rng.start_time.value, 0)
        self.assertEqual(rng.duration.value, 10000)
        self.assertEqual(rng.start_time.rate, 24)

    def test_no_sync_manager_is_a_no_op(self):
        ctrl = _make_controller()
        ctrl.plugin.sync_manager = None

        self.assertIsNone(ctrl.add_clip_from_path("/media/shots/car.mov"))
        _mock_rv.commands.addSource.assert_not_called()

    def test_missing_active_track_still_adds_source_but_broadcasts_nothing(self):
        """Pre-extraction behaviour: insert_child(None, clip) was a silent no-op,
        but the source had already been added to RV. That asymmetry is preserved."""
        ctrl = _make_controller(track_guid=None)

        self.assertIsNone(ctrl.add_clip_from_path("/media/shots/car.mov"))
        _mock_rv.commands.addSource.assert_called_once_with("/media/shots/car.mov")
        ctrl.plugin.sync_manager.insert_child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
