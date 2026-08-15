"""Tests for post-join state confirmation.

Three layers, matching the design's own separation:

- :mod:`otio_sync_core.join_confirmation` — the host-agnostic comparison,
  reusing ``project_state``/``diff_states`` (design D1) with the frame-vs-
  playing gate (D4).
- :class:`~otio_sync_core.manager.SyncManager` — retaining the snapshot as
  received (D2), recording the outcome, clearing it (D6), and never sending
  anything as a side effect (D5).
- Each host integration's ``confirm_join_state`` — building the "actual" side
  from what the peer is actually displaying, not from the manager's own
  record (design's task 2 finding, D7).
"""

from __future__ import annotations

import copy
import os
import sys
import unittest

import opentimelineio as otio

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "python"))

from otio_sync_core.join_confirmation import (  # noqa: E402
    CONFIRMED,
    MISMATCHED,
    NOT_CONFIRMED,
    confirm_join_state,
)
from otio_sync_core.manager import SyncManager  # noqa: E402
from otio_sync_core.patcher import _otio_to_dict  # noqa: E402


def _timeline_payload(clip_guids, name="seq"):
    """A real OTIO timeline, round-tripped to wire-dict form.

    Hand-rolled dicts lack the ``OTIO_SCHEMA`` markers ``_dict_to_otio``
    (``SyncManager.apply_snapshot``'s deserialiser) requires, so this builds
    actual :mod:`opentimelineio` objects and serialises them the same way the
    manager does when broadcasting one.
    """
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    for cg in clip_guids:
        clip = otio.schema.Clip(name=cg)
        clip.metadata["sync"] = {"guid": cg}
        clip.source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(24, 24),
        )
        track.append(clip)
    timeline = otio.schema.Timeline(name=name)
    timeline.tracks.append(track)
    return _otio_to_dict(timeline)


def _snapshot_payload(
    tl_guid="tl-1",
    clip_guids=("clip-a", "clip-b"),
    frame=10.0,
    playing=False,
    extra_timelines=None,
):
    timelines = {tl_guid: _timeline_payload(clip_guids)}
    if extra_timelines:
        timelines.update(extra_timelines)
    return {
        "timelines": timelines,
        "active_timeline_guid": tl_guid,
        "playback_state": {
            "current_time": {
                "OTIO_SCHEMA": "RationalTime.1",
                "value": frame,
                "rate": 24.0,
            },
            "playing": playing,
        },
        "display_state": {},
    }


class ConfirmJoinStateTest(unittest.TestCase):
    """The host-agnostic comparison (design D1, D4)."""

    def test_matching_states_confirm(self):
        expected = _snapshot_payload()
        actual = copy.deepcopy(expected)
        outcome, differences = confirm_join_state(expected, actual)
        self.assertEqual(outcome, CONFIRMED)
        self.assertEqual(differences, [])

    def test_different_clip_reports_mismatch_naming_the_clip(self):
        expected = _snapshot_payload(clip_guids=("clip-a", "clip-b"))
        actual = _snapshot_payload(clip_guids=("clip-a", "clip-c"))
        outcome, differences = confirm_join_state(expected, actual)
        self.assertEqual(outcome, MISMATCHED)
        self.assertTrue(any("clip" in d for d in differences), differences)

    def test_different_frame_reports_mismatch(self):
        expected = _snapshot_payload(frame=10.0, playing=False)
        actual = _snapshot_payload(frame=200.0, playing=False)
        outcome, differences = confirm_join_state(expected, actual)
        self.assertEqual(outcome, MISMATCHED)
        self.assertTrue(any("frame" in d for d in differences), differences)

    def test_different_active_timeline_reports_mismatch(self):
        expected = _snapshot_payload(
            tl_guid="tl-1",
            extra_timelines={"tl-2": _timeline_payload(("clip-x",), name="other")},
        )
        actual = copy.deepcopy(expected)
        actual["active_timeline_guid"] = "tl-2"
        outcome, differences = confirm_join_state(expected, actual)
        self.assertEqual(outcome, MISMATCHED)
        self.assertTrue(any("active timeline" in d for d in differences), differences)

    def test_playing_snapshot_with_advanced_frame_confirms(self):
        """A joiner adopting a playing host legitimately moves past its frame (D4)."""
        expected = _snapshot_payload(frame=10.0, playing=True)
        actual = _snapshot_payload(frame=200.0, playing=True)
        outcome, differences = confirm_join_state(expected, actual)
        self.assertEqual(outcome, CONFIRMED)
        self.assertEqual(differences, [])

    def test_paused_snapshot_with_same_frame_difference_mismatches(self):
        """The same gap is a real mismatch once the snapshot describes a pause."""
        expected = _snapshot_payload(frame=10.0, playing=False)
        actual = _snapshot_payload(frame=200.0, playing=False)
        outcome, differences = confirm_join_state(expected, actual)
        self.assertEqual(outcome, MISMATCHED)

    def test_frame_within_tolerance_confirms(self):
        expected = _snapshot_payload(frame=10.0, playing=False)
        actual = _snapshot_payload(frame=13.0, playing=False)
        outcome, differences = confirm_join_state(expected, actual, frame_tolerance=5)
        self.assertEqual(outcome, CONFIRMED)
        self.assertEqual(differences, [])

    def test_gate_reads_the_snapshots_playing_not_the_peers(self):
        """The playing flag that matters is the snapshot's, not this peer's own."""
        # The snapshot says paused; this peer happens to be playing now (e.g. it
        # started playback locally right after joining) — still compared.
        expected = _snapshot_payload(frame=10.0, playing=False)
        actual = _snapshot_payload(frame=200.0, playing=True)
        outcome, _ = confirm_join_state(expected, actual)
        self.assertEqual(outcome, MISMATCHED)


class ManagerJoinConfirmationTest(unittest.TestCase):
    """Retention, recording, and no-side-effects (design D2, D5, D6)."""

    class _FakeNetwork:
        def __init__(self):
            self.sent = []

        def send_payload(self, payload):
            self.sent.append(payload)

        def receive_payloads(self):
            return []

        def stop(self):
            pass

    def _manager(self):
        return SyncManager(
            session_id="s", self_guid="self-guid",
            network=self._FakeNetwork(), app_name="test",
        )

    def test_apply_snapshot_retains_the_payload_as_received(self):
        """Not one rebuilt from what was adopted (task 1.3)."""
        mgr = self._manager()
        payload = _snapshot_payload(frame=42.0)
        mgr.apply_snapshot(payload)
        retained = mgr._pending_join_snapshot
        self.assertIsNotNone(retained)
        self.assertEqual(
            retained["playback_state"]["current_time"]["value"], 42.0
        )
        # export_state()'s extra keys (is_master, self_guid, ...) prove a
        # retained payload rebuilt from the adopted state would look
        # different from the raw wire payload — it must not have picked
        # those up, because it was never derived from export_state() at all.
        self.assertNotIn("is_master", retained)

    def test_confirm_join_records_confirmed_and_clears_retained_snapshot(self):
        mgr = self._manager()
        payload = _snapshot_payload(frame=10.0)
        mgr.apply_snapshot(payload)
        network_sends_before = len(mgr.network.sent)

        outcome = mgr.confirm_join(copy.deepcopy(payload))

        self.assertEqual(outcome, CONFIRMED)
        self.assertEqual(mgr.join_confirmation, {"outcome": CONFIRMED, "differences": []})
        self.assertIsNone(mgr._pending_join_snapshot)
        # No state request, no broadcast (design D5).
        self.assertEqual(len(mgr.network.sent), network_sends_before)

    def test_confirm_join_records_mismatch_with_differences(self):
        mgr = self._manager()
        payload = _snapshot_payload(frame=10.0, playing=False)
        mgr.apply_snapshot(payload)

        actual = _snapshot_payload(frame=999.0, playing=False)
        outcome = mgr.confirm_join(actual)

        self.assertEqual(outcome, MISMATCHED)
        self.assertEqual(mgr.join_confirmation["outcome"], MISMATCHED)
        self.assertTrue(mgr.join_confirmation["differences"])

    def test_confirm_join_is_a_noop_without_a_pending_join(self):
        mgr = self._manager()
        outcome = mgr.confirm_join(_snapshot_payload())
        self.assertIsNone(outcome)
        self.assertIsNone(mgr.join_confirmation)

    def test_record_join_confirmation_not_confirmed(self):
        """A join that never settles is recorded distinctly (task 4.2/4.4)."""
        mgr = self._manager()
        mgr.apply_snapshot(_snapshot_payload())
        mgr.record_join_confirmation(NOT_CONFIRMED)
        self.assertEqual(mgr.join_confirmation, {"outcome": NOT_CONFIRMED, "differences": []})
        self.assertIsNone(mgr._pending_join_snapshot)

    def test_record_join_confirmation_does_not_overwrite_a_settled_outcome(self):
        """A stray/duplicate call after an outcome is already recorded changes nothing."""
        mgr = self._manager()
        mgr.apply_snapshot(_snapshot_payload())
        mgr.record_join_confirmation(CONFIRMED)
        mgr.record_join_confirmation(MISMATCHED, ["should not land"])
        self.assertEqual(mgr.join_confirmation["outcome"], CONFIRMED)

    def test_a_peer_that_never_joined_has_no_outcome(self):
        mgr = self._manager()
        self.assertIsNone(mgr.join_confirmation)

    def test_a_later_join_starts_from_nothing(self):
        """Task 1.2: the retained snapshot does not leak across joins."""
        mgr = self._manager()
        mgr.apply_snapshot(_snapshot_payload(frame=1.0))
        mgr.confirm_join(_snapshot_payload(frame=1.0))
        self.assertIsNone(mgr._pending_join_snapshot)

        mgr.apply_snapshot(_snapshot_payload(frame=2.0))
        self.assertIsNotNone(mgr._pending_join_snapshot)
        self.assertEqual(
            mgr._pending_join_snapshot["playback_state"]["current_time"]["value"], 2.0
        )


if __name__ == "__main__":
    unittest.main()
