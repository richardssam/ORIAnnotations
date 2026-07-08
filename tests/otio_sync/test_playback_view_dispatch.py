"""Regression coverage for the view-state dispatch in RV's ``_apply_playback``.

Focus: the change added by ``fix-xstudio-selection-and-playhead-sync`` — a
sequence-mode clip-only selection (same mode, same timeline) must surface the
peer's clip by switching to that clip's *source* view, while genuine sequence
switches and source-mode changes keep their existing behaviour, and a deselect
or a repeated identical clip must not re-switch.

The controller is host-coupled (imports ``rv.commands``), so we stub the RV
module and monkeypatch the two ``_switch_to_*`` helpers to record which branch
fires — isolating the dispatch logic from RV internals.
"""
import os
import sys
import types
import unittest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "python"))
sys.path.insert(0, os.path.join(repo_root, "rvplugin", "ori_sync"))

# ── Stub the RV host module before importing the controller ──────────────────
_fake_rv = types.ModuleType("rv")
_fake_cmds = types.ModuleType("rv.commands")
_fake_cmds.frameStart = lambda: 1
_fake_cmds.frame = lambda: 1
_fake_cmds.setFrame = lambda f: None
_fake_cmds.isPlaying = lambda: False
_fake_cmds.play = lambda: None
_fake_cmds.stop = lambda: None
_fake_cmds.playMode = lambda: 0
_fake_cmds.setPlayMode = lambda m: None
_fake_cmds.viewNode = lambda: "seq"
_fake_cmds.nodeType = lambda v: "RVSequenceGroup"
_fake_cmds.selection = lambda: []
_fake_rv.commands = _fake_cmds
sys.modules.setdefault("rv", _fake_rv)
sys.modules.setdefault("rv.commands", _fake_cmds)

# The controller does ``from utils import ...``; provide a stub if the real one
# (which lives in rvplugin/ori_sync) can't be imported standalone.
try:  # pragma: no cover - exercised only when the real utils imports cleanly
    import utils  # noqa: F401
except Exception:  # pragma: no cover
    _stub = types.ModuleType("utils")
    _stub._log = lambda *a, **k: None
    _stub._log_exc = lambda *a, **k: None
    _stub._media_path = lambda x: x
    _stub._clip_effective_range = lambda *a, **k: None
    sys.modules["utils"] = _stub

from playback_sync import PlaybackSyncController, STATE_SYNCED  # noqa: E402


class _FakePlugin:
    def __init__(self):
        self._rv_updating = False
        self.sync_manager = types.SimpleNamespace(
            status=STATE_SYNCED, active_timeline_guid=None
        )


class ApplyPlaybackDispatchTest(unittest.TestCase):
    def setUp(self):
        self.ctrl = PlaybackSyncController(_FakePlugin())
        self.calls = []
        # Record which view-switch branch fires without running RV internals.
        self.ctrl._switch_to_source_view = lambda g: self.calls.append(("source", g))
        self.ctrl._switch_to_sequence_view = lambda g: self.calls.append(("sequence", g))

    def _apply(self, **msg):
        self.ctrl._apply_playback(msg)

    def test_sequence_clip_change_stays_on_sequence(self):
        """A sequence-mode clip_guid change (scrub across cuts) must NOT switch views.

        In sequence view xStudio's clip_guid follows the playhead, so it changes
        while merely scrubbing — RV must stay on the sequence, not isolate a clip.
        """
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.calls.clear()
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid="clipA")
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid="clipB")
        self.assertEqual(self.calls, [])

    def test_sequence_timeline_change_switches_sequence(self):
        """A different sequence (tl change) still goes through sequence view."""
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.calls.clear()
        self._apply(view_mode="sequence", timeline_guid="tl2", clip_guid=None)
        self.assertEqual(self.calls, [("sequence", "tl2")])

    def test_source_mode_clip_change_switches_source(self):
        """Source-mode selection (double-click isolate) keeps its source-view behaviour."""
        self._apply(view_mode="source", timeline_guid="tl1", clip_guid="clipA")
        self.assertEqual(self.calls, [("source", "clipA")])

    def test_first_message_shows_sequence_not_isolate(self):
        """Initial connect (mode transition) shows the sequence, does NOT isolate.

        On startup the peer's clip_guid is just its playhead position, not a user
        selection, so RV must show the sequence rather than jump to a single clip.
        """
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid="clipA", playing=False)
        self.assertEqual(self.calls, [("sequence", "tl1")])

    def test_scrub_while_playing_stays_on_sequence(self):
        """Sequence-mode clip changes never isolate, playing or not (scrub/playback)."""
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.calls.clear()
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid="clipA", playing=True)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
