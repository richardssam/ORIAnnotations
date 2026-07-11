"""Unit tests for SyncManager's annotation-deletion lookup helpers.

Covers ``annotation_clip_guid_for_stroke_uuid`` and
``surviving_annotation_commands`` (added for RV clear-paint/clear-all-paint
and xStudio Ctrl+D delete detection), independent of any host runtime.
"""
import os
import sys
import unittest

import opentimelineio as otio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../python')))

from otio_sync_core.manager import SyncManager


def _make_annotation_track(clips):
    track = otio.schema.Track(name="Annotations")
    for clip in clips:
        track.append(clip)
    return track


def _make_ann_clip(clip_guid, ann_clip_guid, uuids, frame=0):
    clip = otio.schema.Clip(name=f"Annotation_{frame}")
    clip.source_range = otio.opentime.TimeRange(
        otio.opentime.RationalTime(frame, 24),
        otio.opentime.RationalTime(1, 24),
    )
    clip.metadata["clip_guid"] = clip_guid
    clip.metadata["sync"] = {"guid": ann_clip_guid}
    clip.metadata["annotation_commands"] = [{"uuid": u} for u in uuids]
    return clip


class TestAnnotationClipGuidForStrokeUuid(unittest.TestCase):
    def setUp(self):
        self.manager = SyncManager(session_id="test", self_guid="self", network=None)
        self.timeline = otio.schema.Timeline("T")
        self.ann_clip_a = _make_ann_clip("clip-A", "ann-A", ["u1", "u2"], frame=0)
        self.ann_clip_b = _make_ann_clip("clip-A", "ann-B", ["u3"], frame=5)
        self.timeline.tracks.append(
            _make_annotation_track([self.ann_clip_a, self.ann_clip_b])
        )
        self.manager._timelines["tl-1"] = self.timeline
        self.manager._object_map["ann-A"] = self.ann_clip_a
        self.manager._object_map["ann-B"] = self.ann_clip_b

    def test_resolves_uuid_to_owning_clip(self):
        self.assertEqual(
            self.manager.annotation_clip_guid_for_stroke_uuid("u1"), "ann-A"
        )
        self.assertEqual(
            self.manager.annotation_clip_guid_for_stroke_uuid("u2"), "ann-A"
        )
        self.assertEqual(
            self.manager.annotation_clip_guid_for_stroke_uuid("u3"), "ann-B"
        )

    def test_unknown_uuid_returns_none(self):
        self.assertIsNone(
            self.manager.annotation_clip_guid_for_stroke_uuid("does-not-exist")
        )

    def test_surviving_commands_removes_deleted(self):
        survivors = self.manager.surviving_annotation_commands("ann-A", {"u1"})
        self.assertEqual([c["uuid"] for c in survivors], ["u2"])

    def test_surviving_commands_all_deleted_is_empty(self):
        survivors = self.manager.surviving_annotation_commands("ann-A", {"u1", "u2"})
        self.assertEqual(survivors, [])

    def test_surviving_commands_unknown_clip_is_empty(self):
        survivors = self.manager.surviving_annotation_commands("no-such-clip", {"u1"})
        self.assertEqual(survivors, [])


if __name__ == "__main__":
    unittest.main()
