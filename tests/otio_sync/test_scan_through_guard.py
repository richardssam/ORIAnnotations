"""Which ``show_atom`` events count as playback scanning through a sequence.

xStudio fires a ``show_atom`` whenever the on-screen media changes.  A playing
sequence changes it once per edit, and those events are not user selections —
broadcasting them would drag every peer along with our playhead.  The guard
that drops them is cheap to get subtly wrong, because a *bin* click on media
that also lives in the sequence looks, by the time the guard sees it, exactly
like the sequence arriving at that clip: the bin→sequence normalization has
already rewritten the clip guid to the sequence clip's.

Observed 2026-08-09 12:07 (xStudio host, OpenRV follower): every isolation
forces play, the forced play armed this guard, and the next bin click was
swallowed.  All 8 suppressions in that session were bin clicks; not one of the
26 Timeline ``show_atom``\\ s was ever caught.  The guard was firing only on the
events it existed to let through.
"""
import importlib
import os
import sys
import time
import types
import unittest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(repo_root, "python"))


def _stub_xstudio():
    """Enough of the xStudio API for ``playback_sync`` to import.

    The guard under test touches none of it, but the module binds these names
    at import time.
    """
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

# Load the module without running ``ori_sync/__init__.py`` (which pulls in the
# whole plugin), while keeping it a package so its ``from .utils import`` still
# resolves — and under a private package name, because the RV plugin ships a
# module of the same bare name.
_pkg = types.ModuleType("_xs_ori_sync")
_pkg.__path__ = [os.path.join(repo_root, "xstudio_plugin", "ori_sync")]
sys.modules.setdefault("_xs_ori_sync", _pkg)
playback_sync = importlib.import_module("_xs_ori_sync.playback_sync")


class ScanThroughGuardTest(unittest.TestCase):
    """``_is_scan_through`` separates the playhead moving from the user choosing."""

    def setUp(self):
        # __init__ wants a live plugin; the guard reads two attributes.
        self.ctl = playback_sync.PlaybackSyncController.__new__(
            playback_sync.PlaybackSyncController
        )
        self.ctl._last_polled_playing = True
        # Long ago, so the just-started race guard is not what answers.
        self.ctl._playing_started_at = time.monotonic() - 60.0

    def guard(self, **kw):
        args = dict(is_seq_media=True, is_playlist=False, in_single_clip=False)
        args.update(kw)
        return self.ctl._is_scan_through(**args)

    # ── the case that was broken ────────────────────────────────────────────

    def test_bin_click_during_playback_is_not_scan_through(self):
        """The 2026-08-09 regression: a bin click on media that is also in the
        sequence, while the forced play from the last isolation is still
        running."""
        self.assertFalse(self.guard(is_playlist=True))

    def test_timeline_event_during_playback_is_scan_through(self):
        """The case the guard exists for — and the only container that can
        produce it."""
        self.assertTrue(self.guard(is_playlist=False))

    def test_container_is_what_separates_the_two(self):
        """Stated as one assertion, because the pair is the whole point: same
        media, same playback state, opposite answers."""
        self.assertNotEqual(
            self.guard(is_playlist=True), self.guard(is_playlist=False)
        )

    # ── the other escapes, so a future edit cannot quietly drop one ─────────

    def test_media_outside_any_sequence_is_never_scan_through(self):
        self.assertFalse(self.guard(is_seq_media=False))

    def test_isolated_clip_is_never_scan_through(self):
        """A deliberate user clip-switch, whatever is playing."""
        self.assertTrue(self.guard())
        self.assertFalse(self.guard(in_single_clip=True))

    def test_not_playing_is_never_scan_through(self):
        self.ctl._last_polled_playing = False
        self.assertFalse(self.guard())

    def test_unknown_playing_state_is_not_treated_as_playing(self):
        """``_last_polled_playing`` is None until the first poll; an unpolled
        session must not start out swallowing selections."""
        self.ctl._last_polled_playing = None
        self.assertFalse(self.guard())

    def test_first_event_after_play_starts_is_allowed_through(self):
        """Race guard: the poll can set playing before the show_atom for the
        user's own click arrives."""
        self.ctl._playing_started_at = time.monotonic()
        self.assertFalse(self.guard())

    def test_the_race_guard_expires(self):
        """It is a 0.3 s window, not a licence — otherwise real scan-through
        just after play would leak."""
        self.ctl._playing_started_at = time.monotonic() - 0.5
        self.assertTrue(self.guard())

    def test_bin_click_survives_every_combination_that_would_suppress(self):
        """is_playlist is decisive: no other input can override it.

        The bug was that the *guid rewrite* reached the guard, so pin down that
        the container answers regardless of what the rest says.
        """
        for in_single_clip in (False, True):
            for started_ago in (0.0, 60.0):
                with self.subTest(single=in_single_clip, started=started_ago):
                    self.ctl._playing_started_at = time.monotonic() - started_ago
                    self.assertFalse(
                        self.guard(is_playlist=True, in_single_clip=in_single_clip)
                    )


if __name__ == "__main__":
    unittest.main()
