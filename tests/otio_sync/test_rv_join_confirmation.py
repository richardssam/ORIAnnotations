"""OpenRV-side post-join state confirmation (design D3, D7).

``PlaybackSyncController.confirm_join_state`` builds the "actual" side of the
comparison from what RV is actually displaying (``rv.commands`` /
``_displayed_view``), not from the manager's own record — see the design's
task 2 finding. These tests exercise it end-to-end against a real
``SyncManager``, with ``rv.commands`` stubbed the same way
``test_playback_view_dispatch.py`` does (see ``docs/testing.md`` for why the
stub must be rebound per test rather than installed once at import time).
"""
import os
import sys
import types
import unittest

import opentimelineio as otio

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "python"))
sys.path.insert(0, os.path.join(repo_root, "rvplugin", "ori_sync"))

from otio_sync_core.manager import SyncManager  # noqa: E402
from otio_sync_core.patcher import _otio_to_dict  # noqa: E402
from otio_sync_core.join_confirmation import CONFIRMED, MISMATCHED, NOT_CONFIRMED  # noqa: E402

# ── Stub RV, following test_playback_view_dispatch.py's pattern ─────────────
_fake_rv = types.ModuleType("rv")
_fake_cmds = types.ModuleType("rv.commands")
_state = types.SimpleNamespace(
    node="seqNode", frame=1, playing=False, play_mode=0, frame_start=1,
)
_fake_cmds.fps = lambda: 24.0
_fake_cmds.frame = lambda: _state.frame
_fake_cmds.frameStart = lambda: _state.frame_start
_fake_cmds.isPlaying = lambda: _state.playing
_fake_cmds.playMode = lambda: _state.play_mode
_fake_cmds.viewNode = lambda: _state.node
_fake_cmds.setViewNode = lambda n: setattr(_state, "node", n)
_fake_cmds.nodeType = lambda v: "RVSequenceGroup"
_fake_cmds.selection = lambda: []
_fake_rv.commands = _fake_cmds
sys.modules.setdefault("rv", _fake_rv)
sys.modules.setdefault("rv.commands", _fake_cmds)

try:  # pragma: no cover
    import utils  # noqa: F401
except Exception:  # pragma: no cover
    _stub = types.ModuleType("utils")
    _stub._log = lambda *a, **k: None
    _stub._log_exc = lambda *a, **k: None
    _stub._media_path = lambda x: x
    _stub._clip_effective_range = lambda *a, **k: None
    sys.modules["utils"] = _stub

import playback_sync as _ps  # noqa: E402
from playback_sync import PlaybackSyncController  # noqa: E402


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


class _FakePlugin:
    def __init__(self, manager):
        self._rv_updating = False
        self.sync_manager = manager
        self.sequence = types.SimpleNamespace(
            _rv_node_to_timeline_guid={"seqNode": "tl-1", "otherSeqNode": "tl-2"},
            _otio_guid_to_root={},
            _get_sequence_inputs=lambda root: [],
        )
        self.display = types.SimpleNamespace(
            _read_rv_display_state=lambda: {"channel": "RGBA", "annotations_visible": True}
        )


class RvJoinConfirmationTest(unittest.TestCase):
    def setUp(self):
        self._saved_rv = _ps.rv
        _ps.rv = _fake_rv
        _state.node = "seqNode"
        _state.frame = 1
        _state.playing = False
        _state.play_mode = 0
        _state.frame_start = 1

        self.manager = SyncManager(
            session_id="s", self_guid="self-guid", network=_FakeNetwork(), app_name="openrv",
        )
        self.plugin = _FakePlugin(self.manager)
        self.ctrl = PlaybackSyncController(self.plugin)

    def tearDown(self):
        _ps.rv = self._saved_rv

    def test_matching_display_confirms(self):
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        _state.node = "seqNode"      # maps to tl-1, per _rv_node_to_timeline_guid
        _state.frame = 10 + _state.frame_start  # frame_base is subtracted back out

        self.ctrl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], CONFIRMED)
        self.assertEqual(self.manager.join_confirmation["differences"], [])

    def test_wrong_timeline_on_screen_mismatches(self):
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager._timelines["tl-2"] = otio.schema.Timeline(name="other")
        self.manager.apply_snapshot(payload)
        _state.node = "otherSeqNode"  # maps to tl-2, not the snapshot's tl-1
        _state.frame = 10 + _state.frame_start

        self.ctrl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], MISMATCHED)
        self.assertTrue(self.manager.join_confirmation["differences"])

    def test_wrong_frame_mismatches(self):
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        _state.node = "seqNode"
        _state.frame = 500 + _state.frame_start

        self.ctrl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], MISMATCHED)

    def test_unresolvable_view_node_reports_no_active_timeline_not_the_record(self):
        """Regression for the live 2026-08-15 22:00 finding (task 6.3).

        A view node that resolves to no tracked timeline at all must report
        `active_timeline_guid=None` explicitly, not silently fall back to
        manager.export_state()'s record — that record is set unconditionally
        by apply_snapshot from the very snapshot being confirmed against, so
        falling back to it would confirm the manager against itself.
        """
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        _state.node = "unmappedNode"  # not in _rv_node_to_timeline_guid or _otio_guid_to_root
        _state.frame = 10 + _state.frame_start

        self.ctrl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], MISMATCHED)
        self.assertTrue(
            any("active timeline" in d for d in self.manager.join_confirmation["differences"]),
            self.manager.join_confirmation["differences"],
        )

    def test_playing_snapshot_tolerates_an_advanced_frame(self):
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=True)
        self.manager.apply_snapshot(payload)
        _state.node = "seqNode"
        _state.playing = True
        _state.frame = 500 + _state.frame_start

        self.ctrl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], CONFIRMED)

    def test_a_later_join_supersedes_a_deferred_check_rather_than_confirming_it(self):
        """Regression for the live 2026-08-15 20:55 race.

        ``QTimer.singleShot(0, ...)`` means a second STATE_SNAPSHOT can land
        on this already-synced peer before the deferred check actually runs.
        Pinning the check to the generation captured when it was scheduled
        means that check abandons instead of silently confirming against the
        snapshot that superseded the one it was meant to verify.
        """
        first = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(first)
        generation = self.manager.join_generation

        # A second snapshot lands before the deferred callback runs — the
        # display now matches this new snapshot, not the first one.
        second = _snapshot_payload(tl_guid="tl-1", frame=999.0, playing=False)
        self.manager.apply_snapshot(second)
        _state.node = "seqNode"
        _state.frame = 999 + _state.frame_start

        self.ctrl.confirm_join_state(generation)

        self.assertIsNone(self.manager.join_confirmation)

    def test_unreadable_rv_state_records_not_confirmed(self):
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)

        def _raise():
            raise RuntimeError("no view")
        _fake_cmds.frame = _raise
        try:
            self.ctrl.confirm_join_state()
        finally:
            _fake_cmds.frame = lambda: _state.frame

        self.assertEqual(self.manager.join_confirmation["outcome"], NOT_CONFIRMED)

    def test_confirmation_sends_nothing(self):
        """No state request, no broadcast, no local change (design D5)."""
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        _state.node = "seqNode"
        _state.frame = 10 + _state.frame_start
        sent_before = len(self.manager.network.sent)
        node_before = _state.node

        self.ctrl.confirm_join_state()

        self.assertEqual(len(self.manager.network.sent), sent_before)
        self.assertEqual(_state.node, node_before)


if __name__ == "__main__":
    unittest.main()
