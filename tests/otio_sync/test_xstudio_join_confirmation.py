"""xStudio-side post-join state confirmation (design D3, D7).

``PlaybackSyncController.confirm_join_state`` builds the "actual" side of the
comparison from what xStudio is actually displaying — the live playhead
position (:meth:`current_playback_state`) and ``_last_viewed_clip_guid``, set
only by a genuine ``show_atom`` — never from ``manager.active_timeline_guid``/
``playback_state``, which are written directly from received messages
(design's task 2 finding). These tests exercise it end-to-end against a real
``SyncManager``, with just enough of the ``xstudio`` package stubbed for
``playback_sync`` to import (see ``test_scan_through_guard.py`` for the same
pattern) and a fake playhead standing in for the live actor read.
"""
import importlib
import os
import sys
import types
import unittest

import opentimelineio as otio

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "python"))

from otio_sync_core.manager import SyncManager  # noqa: E402
from otio_sync_core.patcher import _otio_to_dict  # noqa: E402
from otio_sync_core.join_confirmation import CONFIRMED, MISMATCHED, NOT_CONFIRMED  # noqa: E402


def _stub_xstudio():
    if "xstudio" in sys.modules:
        return
    core_names = [
        "event_atom", "show_atom", "viewport_playhead_atom",
        "viewport_active_media_container_atom", "item_selection_atom",
        "selection_actor_atom", "selection_changed_atom", "source_atom",
        "position_atom", "play_forward_atom", "play_atom",
        "get_global_playhead_events_atom", "viewport_atom",
        "active_viewport_atom",
    ]
    mods = {
        "xstudio": {},
        "xstudio.core": {n: type(n, (), {}) for n in core_names},
        "xstudio.api": {},
        "xstudio.api.session": {},
        "xstudio.api.session.playhead": {"Playhead": type("Playhead", (), {})},
        "xstudio.api.session.container": {"Container": type("Container", (), {})},
        "xstudio.api.session.playlist": {"Playlist": type("Playlist", (), {})},
        "xstudio.api.session.playlist.timeline": {"Timeline": type("Timeline", (), {})},
        "xstudio.api.session.playlist.subset": {"Subset": type("Subset", (), {})},
        "xstudio.api.session.playlist.contact_sheet": {
            "ContactSheet": type("ContactSheet", (), {})
        },
    }
    for name, attrs in mods.items():
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


_stub_xstudio()

_pkg = types.ModuleType("_xs_ori_sync")
_pkg.__path__ = [os.path.join(repo_root, "xstudio_plugin", "ori_sync")]
sys.modules.setdefault("_xs_ori_sync", _pkg)
playback_sync = importlib.import_module("_xs_ori_sync.playback_sync")


def _timeline_payload(clip_guids, name="seq"):
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


def _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=10.0, playing=False):
    return {
        "timelines": {tl_guid: _timeline_payload(clip_guids)},
        "active_timeline_guid": tl_guid,
        "playback_state": {
            "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": frame, "rate": 24.0},
            "playing": playing,
        },
        "display_state": {},
    }


class _FakeNetwork:
    def __init__(self):
        self.sent = []

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payloads(self):
        return []

    def stop(self):
        pass


class _FakeFrameRate:
    def fps(self):
        return 24.0


class _FakePlayhead:
    def __init__(self, position=0, playing=False):
        self.position = position
        self.playing = playing
        self.frame_rate = _FakeFrameRate()


class _FakeCmdQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class _FakePlugin:
    def __init__(self, manager):
        self.manager = manager
        self.active_playhead = _FakePlayhead()
        self.connection = types.SimpleNamespace(default_timeout_ms=2000)
        self._cmd_queue = _FakeCmdQueue()
        self.display = types.SimpleNamespace(
            read_xs_display_state=lambda: {"channel": "RGBA", "annotations_visible": True}
        )


class XstudioJoinConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.manager = SyncManager(
            session_id="s", self_guid="self-guid", network=_FakeNetwork(), app_name="xstudio",
        )
        self.plugin = _FakePlugin(self.manager)
        self.ctl = playback_sync.PlaybackSyncController(self.plugin)
        self.ctl._pending_seek_frame = None

    def test_matching_display_confirms(self):
        payload = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=10, playing=False)
        self.ctl._last_viewed_clip_guid = "clip-a"

        self.ctl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], CONFIRMED)
        self.assertEqual(self.manager.join_confirmation["differences"], [])

    def test_wrong_clip_on_screen_mismatches(self):
        payload = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        # A second sequence with a different clip, so the "actual" side
        # resolves to a different timeline than the snapshot's.
        track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        clip = otio.schema.Clip(name="clip-z")
        clip.metadata["sync"] = {"guid": "clip-z"}
        track.append(clip)
        self.manager._timelines["tl-2"] = otio.schema.Timeline(name="other")
        self.manager._timelines["tl-2"].tracks.append(track)
        self.plugin.active_playhead = _FakePlayhead(position=10, playing=False)
        self.ctl._last_viewed_clip_guid = "clip-z"

        self.ctl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], MISMATCHED)
        self.assertTrue(self.manager.join_confirmation["differences"])

    def test_wrong_frame_mismatches(self):
        payload = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=500, playing=False)
        self.ctl._last_viewed_clip_guid = "clip-a"

        self.ctl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], MISMATCHED)

    def test_playing_snapshot_tolerates_an_advanced_frame(self):
        payload = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=10.0, playing=True)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=500, playing=True)
        self.ctl._last_viewed_clip_guid = "clip-a"

        self.ctl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], CONFIRMED)

    def test_no_playhead_requeues_rather_than_reporting(self):
        """Checking before the build settles must not manufacture a report (D3)."""
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = None

        self.ctl.confirm_join_state(attempt=0)

        self.assertIsNone(self.manager.join_confirmation)
        self.assertEqual(
            self.plugin._cmd_queue.items,
            [("confirm_join_state", {"attempt": 1, "generation": None})],
        )

    def test_no_display_confirmed_clip_yet_requeues_rather_than_confirming(self):
        """Regression for the live 2026-08-15 22:00 finding (task 6.3).

        With no real show_atom having fired yet, ``_last_viewed_clip_guid``
        is still None even though the playhead exists and no seek is
        pending. The check must not proceed and quietly leave
        playback_state/active_timeline_guid as manager.export_state()'s
        record — that record is set unconditionally by apply_snapshot from
        the very snapshot being confirmed against, so proceeding would
        confirm the manager against itself regardless of whether adoption
        ever actually happened. Live testing with adoption *disabled*
        reproduced exactly this: the check still reported "confirmed".
        """
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=999, playing=False)
        self.ctl._last_viewed_clip_guid = None  # no real show_atom yet

        self.ctl.confirm_join_state(attempt=0)

        self.assertIsNone(self.manager.join_confirmation)
        self.assertEqual(
            self.plugin._cmd_queue.items,
            [("confirm_join_state", {"attempt": 1, "generation": None})],
        )

    def test_no_display_confirmed_clip_ever_records_not_confirmed(self):
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=999, playing=False)
        self.ctl._last_viewed_clip_guid = None

        self.ctl.confirm_join_state(attempt=self.ctl._JOIN_CONFIRM_MAX_ATTEMPTS)

        self.assertEqual(self.manager.join_confirmation["outcome"], NOT_CONFIRMED)

    def test_a_bin_clip_with_no_tracked_sequence_reports_no_active_timeline(self):
        """A resolved-but-sequence-less clip is a real answer, not a fallback.

        Isolating a clip that belongs to no tracked sequence (e.g. a flat
        playlist/bin clip) legitimately has no active *sequence* timeline —
        the actual side must say so explicitly (None) rather than silently
        borrowing manager.export_state()'s active_timeline_guid, which would
        trivially match a snapshot with the same record for the wrong reason.
        """
        payload = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=10, playing=False)
        # A real show_atom fired, but for a clip that belongs to no tracked
        # sequence timeline in this manager at all.
        self.ctl._last_viewed_clip_guid = "clip-not-in-any-sequence"

        self.ctl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], MISMATCHED)
        self.assertTrue(
            any("active timeline" in d for d in self.manager.join_confirmation["differences"]),
            self.manager.join_confirmation["differences"],
        )

    def test_a_later_join_supersedes_a_pending_check_rather_than_confirming_it(self):
        """Regression for the live 2026-08-15 20:55 race.

        A deferred/re-queued check is pinned to the generation it was
        scheduled for.  If a second STATE_SNAPSHOT lands on this (already
        joining) peer before that check runs, the check must abandon rather
        than silently compare the display against the *new* snapshot and
        report on a join it was never scheduled to check.
        """
        first = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(first)
        generation = self.manager.join_generation
        self.plugin.active_playhead = None  # not yet checkable — will requeue

        self.ctl.confirm_join_state(attempt=0, generation=generation)
        self.assertIsNone(self.manager.join_confirmation)
        self.plugin._cmd_queue.items.clear()

        # A second snapshot supersedes the first before the requeued check runs.
        second = _snapshot_payload(tl_guid="tl-1", frame=999.0, playing=False)
        self.manager.apply_snapshot(second)
        self.plugin.active_playhead = _FakePlayhead(position=10, playing=False)
        self.ctl._last_viewed_clip_guid = "clip-a"

        self.ctl.confirm_join_state(attempt=1, generation=generation)

        # Neither confirmed nor mismatched nor not_confirmed against the wrong
        # snapshot — abandoned outright, and nothing queued for it either.
        self.assertIsNone(self.manager.join_confirmation)
        self.assertEqual(self.plugin._cmd_queue.items, [])

    def test_giving_up_respects_a_superseding_generation_too(self):
        """The give-up path must not mark a newer join "not confirmed" either."""
        first = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(first)
        generation = self.manager.join_generation
        second = _snapshot_payload(tl_guid="tl-1", frame=20.0, playing=False)
        self.manager.apply_snapshot(second)  # supersedes before the give-up fires
        self.plugin.active_playhead = None

        self.ctl.confirm_join_state(
            attempt=self.ctl._JOIN_CONFIRM_MAX_ATTEMPTS, generation=generation
        )

        self.assertIsNone(self.manager.join_confirmation)

    def test_giving_up_records_not_confirmed(self):
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = None

        self.ctl.confirm_join_state(attempt=self.ctl._JOIN_CONFIRM_MAX_ATTEMPTS)

        self.assertEqual(self.manager.join_confirmation["outcome"], NOT_CONFIRMED)

    def test_deferred_seek_requeues_rather_than_checking_early(self):
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=0, playing=False)
        self.ctl._pending_seek_frame = 10  # a seek is still outstanding

        self.ctl.confirm_join_state(attempt=0)

        self.assertIsNone(self.manager.join_confirmation)
        self.assertEqual(
            self.plugin._cmd_queue.items,
            [("confirm_join_state", {"attempt": 1, "generation": None})],
        )

    def test_confirmation_sends_nothing_and_touches_no_playhead(self):
        """No state request, no broadcast, no local change (design D5)."""
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=10, playing=False)
        self.ctl._last_viewed_clip_guid = "clip-a"
        sent_before = len(self.manager.network.sent)

        self.ctl.confirm_join_state()

        self.assertEqual(len(self.manager.network.sent), sent_before)
        self.assertEqual(self.plugin.active_playhead.position, 10)


if __name__ == "__main__":
    unittest.main()
