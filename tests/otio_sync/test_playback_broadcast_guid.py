"""The timeline guid RV puts on a playback broadcast names the displayed view.

The broadcast frame is expressed relative to whatever RV is showing — a
timecode source view starts at 96899, an OTIO sequence at 0, a normal view at
1 — so ``timeline_guid`` is the only thing telling a peer which coordinate
space the position belongs to.  Labelling a clip-local position with the
*sequence's* guid passed the receiver's timeline check and moved its sequence
playhead to a frame from a different space (observed against an xStudio host,
2026-08-09).

The stub models the RV display the same way ``test_playback_view_dispatch``
does: ``viewNode()`` is backed by a value the tests move, because what is being
asserted is a function of what RV is *displaying*.
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
_display = types.SimpleNamespace(node="seq1", start=1)
_fake_cmds.viewNode = lambda: _display.node
_fake_cmds.setViewNode = lambda n: setattr(_display, "node", n)
_fake_cmds.frameStart = lambda: _display.start
_fake_cmds.frame = lambda: _display.start + 7
_fake_cmds.setFrame = lambda f: None
_fake_cmds.fps = lambda: 24.0
_fake_cmds.isPlaying = lambda: False
_fake_cmds.play = lambda: None
_fake_cmds.stop = lambda: None
_fake_cmds.playMode = lambda: 0
_fake_cmds.setPlayMode = lambda m: None
_fake_cmds.selection = lambda: []


def _node_type(v):
    name = str(v)
    if name.startswith("sourceGroup"):
        return "RVSourceGroup"
    if name.startswith("stack"):
        return "RVStackGroup"
    return "RVSequenceGroup"


_fake_cmds.nodeType = _node_type
_fake_rv.commands = _fake_cmds
sys.modules.setdefault("rv", _fake_rv)
sys.modules.setdefault("rv.commands", _fake_cmds)

try:  # pragma: no cover - exercised only when the real utils imports cleanly
    import utils  # noqa: F401
except Exception:  # pragma: no cover
    _stub = types.ModuleType("utils")
    _stub._log = lambda *a, **k: None
    _stub._log_exc = lambda *a, **k: None
    _stub._media_path = lambda x: x
    _stub._clip_effective_range = lambda *a, **k: None
    sys.modules["utils"] = _stub

import playback_sync as _ps  # noqa: E402
from playback_sync import PlaybackSyncController, STATE_SYNCED  # noqa: E402

#: The shared sequence, and a clip inside it with its own single-clip timeline.
_SEQ_NODES = {"seq1": "tl-sequence"}
_CLIP_TIMELINES = {"clip-car": "cliptl-car"}


class _FakeManager:
    def __init__(self):
        self.status = STATE_SYNCED
        # Set throughout: the whole point is that it must NOT be substituted
        # when the displayed view has no shared timeline of its own.
        self.active_timeline_guid = "tl-sequence"
        self._clip_timelines = dict(_CLIP_TIMELINES)
        self.sent = []

    def claim_category(self, channel):
        pass

    def broadcast_playback_state(self, state, timeline_guid=None):
        self.sent.append(state)
        return "SENT"


class _FakePlugin:
    def __init__(self):
        self._rv_updating = False
        self.sync_manager = _FakeManager()
        self.sequence = types.SimpleNamespace(
            _rv_node_to_timeline_guid=dict(_SEQ_NODES),
            _otio_guid_to_root={"tl-otio": "stack1"},
            _get_sequence_inputs=lambda root: (
                ["seqInner"] if root == "stack1" else []
            ),
        )


class BroadcastTimelineGuidTest(unittest.TestCase):
    def setUp(self):
        self._saved_rv = _ps.rv
        _ps.rv = _fake_rv
        self.plugin = _FakePlugin()
        self.ctrl = PlaybackSyncController(self.plugin)

    def tearDown(self):
        _ps.rv = self._saved_rv

    def _show(self, node, start=1):
        _display.node = node
        _display.start = start

    def _broadcast(self):
        self.ctrl._broadcast_playback()
        self.assertTrue(self.plugin.sync_manager.sent, "nothing was broadcast")
        return self.plugin.sync_manager.sent[-1]

    # ── source views ────────────────────────────────────────────────────
    def test_isolated_clip_is_labelled_with_its_own_timeline(self):
        self._show("sourceGroup000000", start=96899)
        self.ctrl._cur_view_mode = "source"
        self.ctrl._cur_clip_guid = "clip-car"

        state = self._broadcast()

        self.assertEqual(state["timeline_guid"], "cliptl-car")
        # The bug: the sequence's guid on a clip-local position.
        self.assertNotEqual(state["timeline_guid"], "tl-sequence")

    def test_clip_and_timeline_guid_describe_the_same_clip(self):
        self._show("sourceGroup000000", start=96899)
        self.ctrl._cur_view_mode = "source"
        self.ctrl._cur_clip_guid = "clip-car"

        state = self._broadcast()

        self.assertEqual(state["clip_guid"], "clip-car")
        self.assertEqual(
            state["timeline_guid"],
            self.plugin.sync_manager._clip_timelines[state["clip_guid"]],
        )

    def test_unresolvable_isolation_carries_no_timeline_guid(self):
        """Media with no clip in the shared session — the `warp` case."""
        self._show("sourceGroup000009", start=98499)
        self.ctrl._cur_view_mode = "source"
        self.ctrl._cur_clip_guid = None  # _forget_current_clip cleared it

        state = self._broadcast()

        self.assertIsNone(state["timeline_guid"])
        self.assertNotEqual(state["timeline_guid"], "tl-sequence")

    def test_clip_without_a_registered_timeline_carries_no_guid(self):
        self._show("sourceGroup000001", start=100)
        self.ctrl._cur_view_mode = "source"
        self.ctrl._cur_clip_guid = "clip-unregistered"

        self.assertIsNone(self._broadcast()["timeline_guid"])

    def test_a_position_is_still_sent_from_an_unshared_view(self):
        """Only the guid is withheld; the peer still learns play state."""
        self._show("sourceGroup000009", start=98499)
        self.ctrl._cur_view_mode = "source"
        self.ctrl._cur_clip_guid = None

        state = self._broadcast()

        self.assertIn("current_time", state)
        self.assertEqual(state["current_time"]["value"], 7.0)

    # ── sequence views ──────────────────────────────────────────────────
    def test_sequence_view_carries_the_sequence_guid(self):
        self._show("seq1", start=1)
        self.ctrl._cur_view_mode = "sequence"
        self.ctrl._cur_clip_guid = None

        self.assertEqual(self._broadcast()["timeline_guid"], "tl-sequence")

    def test_otio_stack_inner_sequence_still_resolves(self):
        """An OTIO-origin timeline displays its stack's inner sequence group."""
        self._show("seqInner", start=0)
        self.ctrl._cur_view_mode = "sequence"
        self.ctrl._cur_clip_guid = None

        self.assertEqual(self._broadcast()["timeline_guid"], "tl-otio")

    def test_unmapped_sequence_view_carries_no_guid(self):
        self._show("someOtherSequence", start=1)
        self.ctrl._cur_view_mode = "sequence"
        self.ctrl._cur_clip_guid = None

        self.assertIsNone(self._broadcast()["timeline_guid"])

    # ── view_mode vs displayed view ─────────────────────────────────────
    def test_broadcast_during_switch_to_source_does_not_report_sequence(self):
        self._show("sourceGroup000000", start=96899)
        self.ctrl._cur_view_mode = "sequence"
        self.ctrl._cur_clip_guid = None

        state = self._broadcast()

        self.assertEqual(state["view_mode"], "source")
        self.assertIsNone(state["clip_guid"])

    def test_settled_view_broadcasts_unchanged(self):
        self._show("seq1", start=1)
        self.ctrl._cur_view_mode = "sequence"
        self.ctrl._cur_clip_guid = None

        state = self._broadcast()

        self.assertEqual(state["view_mode"], "sequence")


if __name__ == "__main__":
    unittest.main()
