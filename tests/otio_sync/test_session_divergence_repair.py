"""session-divergence-repair: a confirmed view/playback divergence gets a
bounded, rate-limited repair, at the two moments it currently goes
uncorrected.

Both signal sources — ``confirm_join_state``'s ``MISMATCHED`` outcome and a
sustained ``_tl_mismatch`` streak in ``apply_playback_state`` — funnel through
``PlaybackSyncController._trigger_view_recovery``, which calls
``manager.request_state(reason="recovery")``: the exact mechanism
structure-divergence-recovery already built (``tests/otio_sync/
test_structure_divergence_recovery.py``), so it composes with delta
buffering and replay unmodified rather than introducing a second recovery
path.

Uses the same xstudio-stub harness as ``test_xstudio_join_confirmation.py``
so this runs under the standard interpreter (``run_tests_core.sh``), not only
the xStudio-bundled one.
"""
import importlib
import os
import sys
import time as time_module
import types
import unittest

import opentimelineio as otio

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "python"))

from otio_sync_core.manager import SyncManager, STATE_SYNCED, RECOVERY_COOLDOWN  # noqa: E402
from otio_sync_core.patcher import _otio_to_dict  # noqa: E402
from otio_sync_core.join_confirmation import CONFIRMED, MISMATCHED, NOT_CONFIRMED  # noqa: E402
from otio_sync_core import protocol_messages as pm  # noqa: E402


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

    def set_attribute(self, name, value):
        pass


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

    def stamp_remote_apply(self, channel):
        pass


def _state_requests(mgr):
    return [
        e["payload"]["command"]["payload"]
        for e in mgr.network.sent
        if e["payload"]["command"]["event"] == pm.StateRequest.EVENT
    ]


class SessionDivergenceRepairTest(unittest.TestCase):
    def setUp(self):
        self.manager = SyncManager(
            session_id="s", self_guid="self-guid", network=_FakeNetwork(), app_name="xstudio",
        )
        self.manager.master_guid = "master-guid"
        self.plugin = _FakePlugin(self.manager)
        self.ctl = playback_sync.PlaybackSyncController(self.plugin)
        self.ctl._pending_seek_frame = None

    # ── 1. Shared trigger: safe alongside an outstanding structural request ──

    def test_view_recovery_request_while_a_structural_recovery_is_outstanding(self):
        """design task 1.2: request_state(reason="recovery") must behave
        safely if called for view-recovery while a structural recovery
        request is already outstanding (STATE_JOINING) — the existing
        buffering/replay path (structure-divergence-recovery) covers a
        second call the same way it covers a retry of the same one: it just
        re-sends STATE_REQUEST and re-arms the pending-request bookkeeping,
        which the eventual apply_snapshot resolves once, normally."""
        self.manager.structure_diverged = True
        self.manager.request_state(reason="recovery")  # structural trigger
        self.assertEqual(self.manager.status, "JOINING")
        first_sent_at = self.manager._recovery_request_sent_at

        # A view-divergence signal fires the same trigger while the
        # structural request is still outstanding.
        self.ctl._trigger_view_recovery("sustained-live-mismatch")

        self.assertEqual(self.manager.status, "JOINING")
        self.assertEqual(self.manager.state_request_reason, "recovery")
        self.assertGreaterEqual(self.manager._recovery_request_sent_at, first_sent_at)
        self.assertEqual(len(_state_requests(self.manager)), 2)

        # The master eventually answers once; both flags this peer diverged
        # over must clear from the single snapshot that lands.
        actual = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=1.0, playing=False)
        self.manager.apply_snapshot(actual)

        self.assertEqual(self.manager.status, STATE_SYNCED)
        self.assertFalse(self.manager.structure_diverged)

    # ── 2. Join-time trigger ────────────────────────────────────────────

    def test_not_confirmed_never_triggers_recovery(self):
        """Guards the exact distinction post-join-state-confirmation protects:
        an unsettled join must never be read as a confirmed mismatch."""
        payload = _snapshot_payload(tl_guid="tl-1", frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = None  # never reaches a checkable state

        self.ctl.confirm_join_state(attempt=self.ctl._JOIN_CONFIRM_MAX_ATTEMPTS)

        self.assertEqual(self.manager.join_confirmation["outcome"], NOT_CONFIRMED)
        self.assertNotEqual(self.manager.status, "JOINING")
        self.assertEqual(_state_requests(self.manager), [])

    def test_a_confirmed_match_does_not_trigger_recovery(self):
        payload = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=10.0, playing=False)
        self.manager.apply_snapshot(payload)
        self.plugin.active_playhead = _FakePlayhead(position=10, playing=False)
        self.ctl._last_viewed_clip_guid = "clip-a"

        self.ctl.confirm_join_state()

        self.assertEqual(self.manager.join_confirmation["outcome"], CONFIRMED)
        self.assertNotEqual(self.manager.status, "JOINING")
        self.assertEqual(_state_requests(self.manager), [])

    def test_a_mismatched_join_triggers_a_recovery_request(self):
        """A joiner landing on the wrong clip repairs itself: MISMATCHED
        issues one recovery request, and once the master answers it the peer
        ends up on the session's actual clip/frame."""
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
        self.assertEqual(self.manager.status, "JOINING")
        self.assertEqual(len(_state_requests(self.manager)), 1)
        self.assertEqual(self.manager.state_request_reason, "recovery")

        # The master answers with the session's actual current view.
        actual = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=200.0, playing=False)
        self.manager.apply_snapshot(actual)

        self.assertEqual(self.manager.status, STATE_SYNCED)
        self.assertEqual(self.manager.active_timeline_guid, "tl-1")
        self.assertEqual(self.manager.playback_state["current_time"]["value"], 200.0)

    # ── 3. Live-mismatch trigger ─────────────────────────────────────────

    def _mismatched_message(self, incoming="tl-other", local="tl-local", playing=False):
        self.ctl.get_local_viewed_timeline_guid = lambda: local
        return {
            "playing": playing,
            "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 5.0, "rate": 24.0},
            "timeline_guid": incoming,
        }

    def test_a_single_isolated_mismatch_does_not_trigger_recovery(self):
        self.manager.active_timeline_guid = "tl-active"
        self.plugin.active_playhead = _FakePlayhead(position=0, playing=False)

        self.ctl.apply_playback_state(self._mismatched_message())
        self.assertEqual(self.ctl._tl_mismatch_streak, 1)

        # A message that resolves cleanly against the active timeline.
        self.ctl.apply_playback_state({
            "playing": False,
            "current_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 5.0, "rate": 24.0},
            "timeline_guid": "tl-active",
        })

        self.assertEqual(self.ctl._tl_mismatch_streak, 0)
        self.assertNotEqual(self.manager.status, "JOINING")
        self.assertEqual(_state_requests(self.manager), [])

    def test_a_sustained_mismatch_triggers_a_recovery_request(self):
        """A peer stuck on the wrong timeline, with the host continuing to
        scrub/play, resyncs: enough consecutive mismatches trigger recovery,
        and once answered the peer follows the host's actual timeline."""
        self.manager.active_timeline_guid = "tl-active"
        self.plugin.active_playhead = _FakePlayhead(position=0, playing=False)

        for _ in range(self.ctl._TL_MISMATCH_THRESHOLD):
            self.ctl.apply_playback_state(self._mismatched_message())

        self.assertEqual(self.manager.status, "JOINING")
        self.assertEqual(len(_state_requests(self.manager)), 1)
        self.assertEqual(self.manager.state_request_reason, "recovery")

        # The master answers with the host's actual timeline.
        actual = _snapshot_payload(tl_guid="tl-host", clip_guids=("clip-a",), frame=42.0, playing=False)
        self.manager.apply_snapshot(actual)

        self.assertEqual(self.manager.active_timeline_guid, "tl-host")

    # ── 4. Rate limiting across both trigger paths ───────────────────────

    def test_recovery_cooldown_holds_across_join_and_live_paths(self):
        """A view-divergence repair that doesn't stick must not loop, and the
        cooldown paces both signal sources together — a join-mismatch
        recovery followed immediately by a sustained live mismatch must not
        double-fire."""
        t = [1000.0]
        monotonic_patch = lambda: t[0]  # noqa: E731
        real_monotonic = playback_sync.time.monotonic
        playback_sync.time.monotonic = monotonic_patch
        try:
            payload = _snapshot_payload(tl_guid="tl-1", clip_guids=("clip-a",), frame=10.0, playing=False)
            self.manager.apply_snapshot(payload)
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
            self.assertEqual(len(_state_requests(self.manager)), 1)

            # Immediately after (still within the cooldown), a sustained live
            # mismatch on the *other* signal path fires too.
            self.manager.active_timeline_guid = "tl-active"
            for _ in range(self.ctl._TL_MISMATCH_THRESHOLD):
                self.ctl.apply_playback_state(self._mismatched_message())

            self.assertEqual(
                len(_state_requests(self.manager)), 1,
                "the live-mismatch trigger must be suppressed by the same"
                " cooldown the join-mismatch trigger already armed",
            )

            # Once the cooldown has elapsed, the (still-unresolved) live
            # mismatch may trigger again.
            t[0] += RECOVERY_COOLDOWN + 0.01
            self.ctl._tl_mismatch_streak = 0
            for _ in range(self.ctl._TL_MISMATCH_THRESHOLD):
                self.ctl.apply_playback_state(self._mismatched_message())

            self.assertEqual(len(_state_requests(self.manager)), 2)
        finally:
            playback_sync.time.monotonic = real_monotonic


if __name__ == "__main__":
    unittest.main()
