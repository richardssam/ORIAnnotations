"""Regression coverage for the view-state dispatch in RV's ``_apply_playback``.

Focus: the change added by ``fix-xstudio-selection-and-playhead-sync`` — a
sequence-mode clip-only selection (same mode, same timeline) must surface the
peer's clip by switching to that clip's *source* view, while genuine sequence
switches and source-mode changes keep their existing behaviour, and a deselect
or a repeated identical clip must not re-switch.

The controller is host-coupled (imports ``rv.commands``), so we stub the RV
module. The stub models **what RV is displaying** — ``viewNode()`` is backed by
a value ``setViewNode()`` moves — because the controller now decides whether an
instruction needs action by reading the display rather than by consulting a
record of what it last adopted. A stub that could not diverge its display from
its history could not test the thing that broke.
"""
import os
import sys
import types
import unittest

import opentimelineio as otio

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
# What RV is showing.  Tests move this directly to model the user changing the
# view locally — the event that has no message and that a history-based
# comparison cannot see.
_display = types.SimpleNamespace(node="bootSequence")
_fake_cmds.viewNode = lambda: _display.node
_fake_cmds.setViewNode = lambda n: setattr(_display, "node", n)
def _node_type(v):
    """Node kinds by name, matching how the real graph is shaped.

    ``tracks`` is what ``create_rv_node_from_otio`` names the Stack it returns,
    and the switch only follows an OTIO root when it really is a stack.
    """
    name = str(v)
    if name.startswith("sourceGroup"):
        return "RVSourceGroup"
    if name in ("tracks", "stack") or name.startswith("stack"):
        return "RVStackGroup"
    return "RVSequenceGroup"


_fake_cmds.nodeType = _node_type
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

import playback_sync as _ps  # noqa: E402
from playback_sync import PlaybackSyncController, STATE_SYNCED  # noqa: E402


class _FakeRvMixin:
    """Bind the controller module's ``rv`` to this file's stub, per test.

    ``sys.modules.setdefault`` above only wins if this module imports first —
    several test modules install their own ``rv`` stub, and whichever runs
    first owns it for the session. That made these tests pass alone and fail in
    a full run. Rebinding ``playback_sync.rv`` per test is order-independent,
    and restoring it afterwards leaves the other modules' stubs untouched.
    """

    def setUp(self):
        super().setUp()
        self._saved_rv = _ps.rv
        _ps.rv = _fake_rv
        _fake_cmds.setFrame = lambda f: None
        _show("bootSequence")

    def tearDown(self):
        _ps.rv = self._saved_rv
        super().tearDown()


#: Two sequences and two clips, wired the way the real maps are.
_SEQ_NODES = {"seq1": "tl1", "seq2": "tl2"}
_CLIP_MEDIA = {"clipA": "/m/car.mov", "clipB": "/m/graphic.mov"}
_MEDIA_SG = {"/m/car.mov": "sourceGroup000000", "/m/graphic.mov": "sourceGroup000001"}
_SG_MEDIA = {sg: path for path, sg in _MEDIA_SG.items()}


def _make_clip(url):
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=url)
    return clip


class _FakeManager:
    def __init__(self):
        self.status = STATE_SYNCED
        self.active_timeline_guid = None
        self._object_map = {g: _make_clip(u) for g, u in _CLIP_MEDIA.items()}
        self._clip_timelines = {}
        self.announced = []

    def get_or_create_clip_timeline(self, clip_guid):
        return self._clip_timelines.setdefault(clip_guid, f"cliptl-{clip_guid}")

    def broadcast_clip_timeline(self, tl_guid):
        """Announced once each, as the real manager does — callers never gate."""
        if tl_guid in self.announced:
            return "SUPPRESSED"
        self.announced.append(tl_guid)
        return "SENT"


class _FakePlugin:
    def __init__(self):
        self._rv_updating = False
        self.sync_manager = _FakeManager()
        self.sequence = types.SimpleNamespace(
            _rv_node_to_timeline_guid=dict(_SEQ_NODES),
            _otio_guid_to_root={},
            _bin_guid_to_path={},
            _get_sequence_inputs=lambda root: [],
            _path_to_source_group_map=lambda: dict(_MEDIA_SG),
            # Answered per node, as the real one is — so a duplicate source
            # group still resolves rather than losing a dict collision.
            media_path_for_source_group=lambda sg: _SG_MEDIA.get(sg),
        )


def _show(node):
    """Model a local view change — the user switching the view in RV."""
    _display.node = node


class ApplyPlaybackDispatchTest(_FakeRvMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()  # display starts on neither test sequence
        self.ctrl = PlaybackSyncController(_FakePlugin())
        self.calls = []
        # Wrap rather than replace: the real helpers run, so the display moves
        # and the outcome is recorded exactly as in the plugin.
        _real_source = self.ctrl._switch_to_source_view
        _real_sequence = self.ctrl._switch_to_sequence_view

        def _source(g):
            self.calls.append(("source", g))
            return _real_source(g)

        def _sequence(g):
            self.calls.append(("sequence", g))
            return _real_sequence(g)

        self.ctrl._switch_to_source_view = _source
        self.ctrl._switch_to_sequence_view = _sequence

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


class ViewOutcomeRecordTest(_FakeRvMixin, unittest.TestCase):
    """Every remote view instruction leaves a record of what became of it.

    Reporting only hard failures left "received the host's view and quietly did
    nothing" looking identical to "complied" — the 2026-08-06 soak has a peer
    ignoring ``mode=sequence`` for six seconds with zero records at either end.
    These assert the record exists on the paths that perform no switch, which
    are precisely the paths that used to be silent.
    """

    def setUp(self):
        super().setUp()
        self.ctrl = PlaybackSyncController(_FakePlugin())

    def _apply(self, **msg):
        self.ctrl._apply_playback(msg)

    def _last_outcome(self):
        return (self.ctrl.view_outcome or {}).get("outcome")

    def test_sequence_clip_change_is_recorded_as_declined(self):
        """Correct to decline — but it must be distinguishable from a bug."""
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid="clipA")
        self.assertEqual(self._last_outcome(), PlaybackSyncController.VIEW_DECLINED)
        self.assertIn("playhead", self.ctrl.view_outcome["reason"])
        # A decline is not a mirror failure: it must not fail the suite.
        self.assertIsNone(self.ctrl.mirror_failure)

    def test_unchanged_instruction_is_recorded_as_already_displayed(self):
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.assertEqual(
            self._last_outcome(), PlaybackSyncController.VIEW_ALREADY_DISPLAYED
        )

    def test_unknown_view_mode_is_declined_not_ignored(self):
        self._apply(view_mode="nonsense", timeline_guid="tl1", clip_guid=None)
        self.assertEqual(self._last_outcome(), PlaybackSyncController.VIEW_DECLINED)
        self.assertIn("nonsense", self.ctrl.view_outcome["reason"])

    def test_a_raising_switch_is_recorded_as_failed(self):
        def boom(_):
            raise RuntimeError("no such node")

        self.ctrl._switch_to_sequence_view = boom
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.assertEqual(self._last_outcome(), PlaybackSyncController.VIEW_FAILED)
        self.assertIn("no such node", self.ctrl.mirror_failure)

    def test_a_failure_survives_a_later_decline(self):
        """A failure then a decline is still a peer that cannot show the view."""
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self._apply(view_mode="source", timeline_guid="tl1", clip_guid="clipZZ")
        self.assertEqual(self._last_outcome(), PlaybackSyncController.VIEW_FAILED)
        self.assertIsNotNone(self.ctrl.mirror_failure)

        # Still showing the sequence, so the next sequence instruction declines
        # its clip part — which must not be read as the failure being resolved.
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid="clipA")
        self.assertEqual(self._last_outcome(), PlaybackSyncController.VIEW_DECLINED)
        self.assertIsNotNone(self.ctrl.mirror_failure)

        # Agreement — the host reports the sequence RV is showing, with no clip
        # part to decline — is what clears it.
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.assertEqual(
            self._last_outcome(), PlaybackSyncController.VIEW_ALREADY_DISPLAYED
        )
        self.assertIsNone(self.ctrl.mirror_failure)

    def test_a_message_carrying_no_view_mode_records_nothing(self):
        """A follower's stripped broadcast is position-only, not an instruction."""
        self._apply(timeline_guid="tl1", clip_guid="clipA")
        self.assertIsNone(self.ctrl.view_outcome)


class DisplayedViewComparisonTest(_FakeRvMixin, unittest.TestCase):
    """The comparison is against what RV displays, not what it last adopted.

    Both ways the old record went stale were seen in the same session:
    the user changing the view locally (no message records it), and a switch
    that *failed* still being written to the record — after which the identical
    instruction reported "already displayed", cleared the failure, and became a
    no-op the host could not override (2026-08-08 21:43:47).
    """

    def setUp(self):
        super().setUp()
        self.ctrl = PlaybackSyncController(_FakePlugin())
        self.switches = []
        _real = self.ctrl._switch_to_sequence_view

        def _seq(g):
            self.switches.append(g)
            return _real(g)

        self.ctrl._switch_to_sequence_view = _seq

    def _apply(self, **msg):
        self.ctrl._apply_playback(msg)

    def _last_outcome(self):
        return (self.ctrl.view_outcome or {}).get("outcome")

    def test_locally_isolated_clip_does_not_block_a_later_sequence_instruction(self):
        """Spec: a locally isolated clip does not block a later sequence instruction."""
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.assertEqual(_display.node, "seq1")
        self.switches.clear()

        _show("sourceGroup000000")  # the user isolates a clip locally
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)

        self.assertEqual(self.switches, ["tl1"], "host's instruction was ignored")
        self.assertEqual(self._last_outcome(), PlaybackSyncController.VIEW_ADOPTED)
        self.assertEqual(_display.node, "seq1")

    def test_instruction_matching_the_displayed_view_is_a_no_op(self):
        """Spec: an instruction matching the displayed view is still a no-op."""
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.switches.clear()
        self._apply(view_mode="sequence", timeline_guid="tl1", clip_guid=None)
        self.assertEqual(self.switches, [])
        self.assertEqual(
            self._last_outcome(), PlaybackSyncController.VIEW_ALREADY_DISPLAYED
        )

    def test_a_failed_switch_is_retried_not_reported_as_agreement(self):
        """The 21:43:47 regression: failure must not be recorded as applied."""
        self._apply(view_mode="source", timeline_guid="tl1", clip_guid="clipZZ")
        self.assertEqual(self._last_outcome(), PlaybackSyncController.VIEW_FAILED)

        # The identical instruction again: RV still is not showing the clip, so
        # this must NOT come back as already-displayed, and must not clear the
        # failure.  That is precisely what made the divergence permanent.
        self._apply(view_mode="source", timeline_guid="tl1", clip_guid="clipZZ")
        self.assertEqual(self._last_outcome(), PlaybackSyncController.VIEW_FAILED)
        self.assertIsNotNone(self.ctrl.mirror_failure)

    def test_source_instruction_already_shown_does_not_reseek(self):
        """Delegating every source message must not re-run setFrame(1)."""
        frames = []
        _fake_cmds.setFrame = lambda f: frames.append(f)
        try:
            self._apply(view_mode="source", timeline_guid="tl1", clip_guid="clipA")
            self.assertEqual(_display.node, "sourceGroup000000")
            frames.clear()
            self._apply(view_mode="source", timeline_guid="tl1", clip_guid="clipA")
            self.assertEqual(
                self._last_outcome(), PlaybackSyncController.VIEW_ALREADY_DISPLAYED
            )
            self.assertNotIn(1, frames, "re-seeked a view it was already showing")
        finally:
            _fake_cmds.setFrame = lambda f: None


class StrippedPositionTest(_FakeRvMixin, unittest.TestCase):
    """A message with no position fields must not move the playhead.

    Position is stripped on the way out when the sender does not hold the
    lease, so "absent" means "not mine to say". Reading it as zero stopped
    playback and seeked to the start of the view every time the host reported
    its view while the follower was driving position.
    """

    def setUp(self):
        super().setUp()
        self.ctrl = PlaybackSyncController(_FakePlugin())
        self.frames = []
        self.stopped = []
        _fake_cmds.setFrame = lambda f: self.frames.append(f)
        _fake_cmds.stop = lambda: self.stopped.append(True)
        # The playhead must sit somewhere other than the frame a stripped
        # message would compute (value 0 + base 1), or the erroneous seek is a
        # no-op and the test passes for the wrong reason.  Playing, likewise, so
        # that an erroneous playing=False is observable as a stop.
        _fake_cmds.frame = lambda: 100
        _fake_cmds.isPlaying = lambda: True

    def tearDown(self):
        _fake_cmds.stop = lambda: None
        _fake_cmds.frame = lambda: 1
        _fake_cmds.isPlaying = lambda: False
        super().tearDown()

    def test_view_only_message_does_not_seek_or_stop(self):
        self.ctrl._apply_playback(
            {"view_mode": "sequence", "timeline_guid": "tl1", "clip_guid": None}
        )
        self.assertEqual(_display.node, "seq1", "the view should still be applied")
        self.assertEqual(self.frames, [], "seeked on a position-stripped message")
        self.assertEqual(self.stopped, [], "stopped on a position-stripped message")

    def test_a_message_carrying_position_still_applies_it(self):
        self.ctrl._apply_playback({
            "view_mode": "sequence", "timeline_guid": "tl1", "clip_guid": None,
            "playing": False, "playback_mode": "loop",
            "current_time": {"value": 42.0},
        })
        self.assertIn(43, self.frames, "frame = value + base(1) was not applied")


class CurrentClipTracksOnlySourceModeTest(_FakeRvMixin, unittest.TestCase):
    """`_cur_clip_guid` means "the clip we are showing", so sequence mode has none.

    In sequence mode the sender's clip_guid is its playhead position. Recording
    it as the clip we are showing made a later *local* isolation of that same
    clip look like a no-op, and it was dropped without a broadcast.
    """

    def setUp(self):
        super().setUp()
        self.plugin = _FakePlugin()
        self.ctrl = PlaybackSyncController(self.plugin)

    def test_sequence_mode_clip_guid_is_not_recorded_as_current(self):
        self.ctrl._apply_playback({
            "view_mode": "sequence", "timeline_guid": "tl1", "clip_guid": "clipA",
            "playing": False, "current_time": {"value": 0.0},
        })
        self.assertIsNone(self.ctrl._cur_clip_guid)

    def test_source_mode_clip_guid_is_recorded_as_current(self):
        self.ctrl._apply_playback({
            "view_mode": "source", "timeline_guid": "tl1", "clip_guid": "clipA",
            "playing": False, "current_time": {"value": 0.0},
        })
        self.assertEqual(self.ctrl._cur_clip_guid, "clipA")

    def test_isolating_the_clip_the_host_playhead_is_over_still_broadcasts(self):
        """The 09:54:19 regression, end to end."""
        sent = []
        self.ctrl.broadcast_view_state = lambda g, m: sent.append((g, m))
        # Host reports sequence view, playhead over clipA.
        self.ctrl._apply_playback({
            "view_mode": "sequence", "timeline_guid": "tl1", "clip_guid": "clipA",
            "playing": False, "current_time": {"value": 0.0},
        })
        # User isolates that same clip locally.
        _show("sourceGroup000000")
        self.ctrl.on_view_changed(types.SimpleNamespace(reject=lambda: None))

        self.assertEqual(sent, [("clipA", "source")], "local isolation was dropped")


class UnresolvableIsolationForgetsTheClipTest(_FakeRvMixin, unittest.TestCase):
    """Showing something we cannot name must stop us naming the last thing we could.

    ``_cur_clip_guid`` is written only where a view change resolves, so an
    isolation that resolves to nothing used to leave the previous clip standing
    — and every position message afterwards carried it. Observed 2026-08-09
    12:27, isolating bin-only ``seq_C``::

        SEND playback ... view=sourceGroup000005 ... mode=source clip=7b7fd1f4

    ``sourceGroup000005`` is seq_C; ``7b7fd1f4`` is laser, isolated before it.
    Only the follower visibility strip kept that off the wire.
    """

    #: A source group for media that is in RV's bin but on no shared clip.
    BIN_ONLY_SG = "sourceGroup000005"

    def setUp(self):
        super().setUp()
        self.plugin = _FakePlugin()
        self.plugin.sequence.log_source_group_inventory = lambda why: None
        _sg_media = dict(_SG_MEDIA)
        _sg_media[self.BIN_ONLY_SG] = "/m/seq_C.mov"  # deliberately not in _CLIP_MEDIA
        self.plugin.sequence.media_path_for_source_group = _sg_media.get
        self.ctrl = PlaybackSyncController(self.plugin)
        self.sent = []
        self.ctrl.broadcast_view_state = self._record

    def _record(self, guid, mode):
        """Stand in for the real one, which is what writes ``_cur_*``."""
        self.sent.append((guid, mode))
        self.ctrl._cur_view_mode = mode
        self.ctrl._cur_clip_guid = guid or None

    def _isolate(self, node):
        _show(node)
        self.ctrl.on_view_changed(types.SimpleNamespace(reject=lambda: None))

    def test_unnameable_isolation_clears_the_previous_clip(self):
        """The regression, in the order it happened."""
        self._isolate("sourceGroup000000")
        self.assertEqual(self.ctrl._cur_clip_guid, "clipA")

        self._isolate(self.BIN_ONLY_SG)
        self.assertIsNone(
            self.ctrl._cur_clip_guid,
            "still naming the clip it stopped showing",
        )

    def test_it_reports_source_mode_so_the_peer_declines_rather_than_follows(self):
        """``source`` + no clip is a state the apply path already refuses; a
        stale ``sequence`` would send the peer to the sequence instead.

        Entered from the sequence view, which is both the realistic path and
        the one where the two answers differ — starting from an isolation would
        leave the mode at ``source`` whether or not anything set it.
        """
        self._isolate("seq1")
        self.assertEqual(self.ctrl._cur_view_mode, "sequence")

        self._isolate(self.BIN_ONLY_SG)
        self.assertEqual(self.ctrl._cur_view_mode, "source")
        self.assertIsNone(self.ctrl._cur_clip_guid)

    def test_a_source_group_with_no_media_path_also_clears(self):
        self._isolate("sourceGroup000000")
        self._isolate("sourceGroup999999")  # in no map at all
        self.assertIsNone(self.ctrl._cur_clip_guid)

    def test_switching_to_an_unclassifiable_node_clears_the_clip(self):
        self._isolate("sourceGroup000000")
        self._isolate("someStack")  # neither a known sequence nor a source group
        self.assertIsNone(self.ctrl._cur_clip_guid)

    def test_an_unclassifiable_node_does_not_claim_source_mode(self):
        """We cannot tell what it is, so guessing ``source`` would trade one
        wrong answer for another.

        The mode is forced to something other than ``source`` first, or this
        would pass on the branch it is meant to rule out.
        """
        self._isolate("sourceGroup000000")
        self.ctrl._cur_view_mode = "sequence"
        self._isolate("someStack")
        self.assertEqual(self.ctrl._cur_view_mode, "sequence")

    def test_nothing_is_broadcast_by_forgetting(self):
        """Clearing is bookkeeping. The next position message carries the truth;
        the unresolvable view itself is not an event worth sending."""
        self._isolate("sourceGroup000000")
        self.sent.clear()
        self._isolate(self.BIN_ONLY_SG)
        self.assertEqual(self.sent, [])

    def test_a_later_resolvable_isolation_still_broadcasts(self):
        """Clearing must not leave state that swallows the next real switch."""
        self._isolate("sourceGroup000000")
        self._isolate(self.BIN_ONLY_SG)
        self.sent.clear()
        self._isolate("sourceGroup000001")
        self.assertEqual(self.sent, [("clipB", "source")])

    def test_re_isolating_the_forgotten_clip_broadcasts_again(self):
        """The dedupe at the top of the source branch compares against
        ``_cur_clip_guid``; having cleared it, returning to clipA is a real
        change again — where before it read as "already the current clip"."""
        self._isolate("sourceGroup000000")
        self._isolate(self.BIN_ONLY_SG)
        self.sent.clear()
        self._isolate("sourceGroup000000")
        self.assertEqual(self.sent, [("clipA", "source")])


class AmbiguousTimelineNodeTest(_FakeRvMixin, unittest.TestCase):
    """One GUID must resolve to one node — and if it doesn't, say so.

    A timeline first built natively and later rebuilt by `apply_otio_snapshot`
    ended up registered in both maps at once (2026-08-08 22:35: RVSequenceGroup
    'Sequence 1' and Stack 'tracks' both answering to c744cb45). The switch
    resolved the native entry while the display sat on the root, so the two
    disagreed about the same GUID.
    """

    def setUp(self):
        super().setUp()
        self.plugin = _FakePlugin()
        self.ctrl = PlaybackSyncController(self.plugin)

    def test_otio_root_wins_over_a_stale_native_entry(self):
        seq = self.plugin.sequence
        seq._otio_guid_to_root["tl1"] = "tracks"
        seq._get_sequence_inputs = lambda root: ["Video"]
        # The stale native entry the rebuild failed to clear.
        seq._rv_node_to_timeline_guid["seq1"] = "tl1"

        self.ctrl._switch_to_sequence_view("tl1")

        self.assertEqual(_display.node, "Video", "switched to the stale node")

    def test_native_entry_is_still_used_when_there_is_no_otio_root(self):
        self.ctrl._switch_to_sequence_view("tl1")
        self.assertEqual(_display.node, "seq1")


if __name__ == "__main__":
    unittest.main()
