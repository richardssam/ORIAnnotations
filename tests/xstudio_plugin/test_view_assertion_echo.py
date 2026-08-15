#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Regression coverage for ``_view_assertion_is_echo``.

Property under test: a peer must keep asserting its **own** view while another
peer's message is still settling. The predicate this replaced
(``_provenance()``'s boolean) was true for *any* remote message inside the
manager's 5 s settle window, with no filter on what the message was or what it
did — so once a peer sent anything, this peer went quiet for up to five
seconds.

Observed live 2026-08-15 14:19:03-07: three consecutive genuine local
selections on the client were charged to one of the host's
``PLAYBACK_SETTINGS_1.0/SET`` messages at ages 1.76 s, 3.59 s and 4.90 s, and
two were suppressed outright. Each was provably local — the client reached
``seq_C``, ``seq_D`` and ``graphic`` *before* the host did.

A time window cannot separate those from real echoes: the false positives sat
at 1.76 s and the true echoes at 1.52-1.90 s. So the tests below pin the
discriminator to *what* is being asserted rather than *when*, and the central
case is `test_different_clip_inside_settle_window_is_not_an_echo` — the exact
shape of the live bug.

Requires the xStudio Python bindings; does **not** require a live session. Run
with the xStudio-bundled interpreter, e.g.::

    /path/to/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 -m pytest \\
        tests/xstudio_plugin/test_view_assertion_echo.py -v
"""
import os
import sys
import time
import types
import unittest

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(_repo_root, "python"))
sys.path.insert(0, os.path.join(_repo_root, "xstudio_plugin"))

_ori_sync_dir = os.path.join(_repo_root, "xstudio_plugin", "ori_sync")
_ori_sync_stub = types.ModuleType("ori_sync")
_ori_sync_stub.__path__ = [_ori_sync_dir]
sys.modules.setdefault("ori_sync", _ori_sync_stub)

from ori_sync import playback_sync  # noqa: E402

PlaybackSyncController = playback_sync.PlaybackSyncController

#: The clip the peer told us to adopt, and one the peer never mentioned.
ADOPTED = "clip-adopted-guid"
OTHER = "clip-other-guid"


def _ctrl(remote_ctx=None):
    """A controller carrying only the attributes the predicate reads.

    Built via ``__new__`` so no xStudio session is touched — the same approach
    ``test_scan_through_guard.py`` uses, and for the same reason.
    """
    ctrl = PlaybackSyncController.__new__(PlaybackSyncController)
    ctrl.plugin = types.SimpleNamespace(
        manager=types.SimpleNamespace(remote_apply_context=lambda: remote_ctx)
    )
    ctrl._applied_clip_echo_guid = None
    ctrl._applied_clip_echo_until = 0.0
    ctrl._applied_view_mode = None
    return ctrl


def _settling(seconds, schema="PLAYBACK_SETTINGS_1.0", event="SET"):
    """A remote apply that has returned and is *seconds* into its settle window."""
    return {
        "source": "peer-guid",
        "command_schema": schema,
        "event": event,
        "age": seconds,
        "settling_for": seconds,
        "in_apply": False,
    }


def _in_apply(schema="PLAYBACK_SETTINGS_1.0", event="SET"):
    return {
        "source": "peer-guid",
        "command_schema": schema,
        "event": event,
        "age": 0.1,
        "settling_for": None,
        "in_apply": True,
    }


class ViewAssertionEchoTests(unittest.TestCase):

    def test_different_clip_inside_settle_window_is_not_an_echo(self):
        """The live bug: leading with a clip the peer never mentioned.

        At 1.76 s into a peer's settle window the old predicate said "remote"
        and dropped the broadcast. A peer's message cannot make this peer
        switch to a clip that message never named, so this is local by
        construction however recently the peer spoke.
        """
        ctrl = _ctrl(_settling(1.76))
        ctrl._applied_clip_echo_guid = ADOPTED
        ctrl._applied_view_mode = "source"
        ctrl._applied_clip_echo_until = time.monotonic() + 3.0

        is_echo, _ = ctrl._view_assertion_is_echo(OTHER, "source")
        self.assertFalse(is_echo)

    def test_same_clip_inside_the_applied_window_is_an_echo(self):
        ctrl = _ctrl(_settling(1.76))
        ctrl._applied_clip_echo_guid = ADOPTED
        ctrl._applied_view_mode = "source"
        ctrl._applied_clip_echo_until = time.monotonic() + 3.0

        is_echo, why = ctrl._view_assertion_is_echo(ADOPTED, "source")
        self.assertTrue(is_echo)
        self.assertIn("echo", why)

    def test_same_clip_after_the_applied_window_is_not_suppressed(self):
        """Re-selecting the same clip later is a fresh local action."""
        ctrl = _ctrl(_settling(1.0))
        ctrl._applied_clip_echo_guid = ADOPTED
        ctrl._applied_view_mode = "source"
        ctrl._applied_clip_echo_until = time.monotonic() - 0.01

        is_echo, _ = ctrl._view_assertion_is_echo(ADOPTED, "source")
        self.assertFalse(is_echo)

    def test_same_clip_but_different_mode_is_not_an_echo(self):
        """Adopting a clip in source view, then pinning back to the sequence."""
        ctrl = _ctrl(_settling(0.5))
        ctrl._applied_clip_echo_guid = ADOPTED
        ctrl._applied_view_mode = "source"
        ctrl._applied_clip_echo_until = time.monotonic() + 3.0

        is_echo, _ = ctrl._view_assertion_is_echo(None, "sequence")
        self.assertFalse(is_echo)

    def test_sequence_mode_echo_is_matched_without_a_clip(self):
        """A mode transition carries no clip, so the mode has to be recorded."""
        ctrl = _ctrl(_settling(0.5))
        ctrl._applied_clip_echo_guid = None
        ctrl._applied_view_mode = "sequence"
        ctrl._applied_clip_echo_until = time.monotonic() + 3.0

        is_echo, why = ctrl._view_assertion_is_echo(None, "sequence")
        self.assertTrue(is_echo)
        self.assertIn("echo", why)

    def test_in_apply_is_an_echo_whatever_the_clip(self):
        """``in_apply`` is a scope, not a guess — trust it."""
        ctrl = _ctrl(_in_apply())

        is_echo, why = ctrl._view_assertion_is_echo(OTHER, "source")
        self.assertTrue(is_echo)
        self.assertIn("inside remote apply", why)

    def test_annotation_inducer_still_suppresses(self):
        """The narrow inducer table keeps working for messages naming no clip.

        Applying a peer's stroke makes xStudio *show* the annotated clip; that
        must not be re-broadcast as a fresh isolation, and no clip guid in the
        message identifies it.
        """
        ctrl = _ctrl(_settling(0.5, schema="Annotation.1", event="PARTIAL"))

        is_echo, why = ctrl._view_assertion_is_echo(OTHER, "source")
        self.assertTrue(is_echo)
        self.assertIn("remote-induced", why)

    def test_annotation_inducer_expires_on_its_own_window(self):
        """Past its 1 s window the annotation no longer explains the view."""
        ctrl = _ctrl(_settling(2.0, schema="Annotation.1", event="PARTIAL"))

        is_echo, _ = ctrl._view_assertion_is_echo(OTHER, "source")
        self.assertFalse(is_echo)

    def test_no_remote_activity_at_all_is_local(self):
        ctrl = _ctrl(None)
        is_echo, _ = ctrl._view_assertion_is_echo(OTHER, "source")
        self.assertFalse(is_echo)

    def test_unreadable_manager_does_not_suppress(self):
        """A failed context read must not silence this peer's own view."""

        def _boom():
            raise RuntimeError("manager gone")

        ctrl = _ctrl(None)
        ctrl.plugin.manager.remote_apply_context = _boom

        is_echo, _ = ctrl._view_assertion_is_echo(OTHER, "source")
        self.assertFalse(is_echo)

    def test_provenance_boolean_is_no_longer_consulted(self):
        """Guards the regression directly.

        ``_provenance()`` still reports a remote apply in flight for logging.
        If any decision starts reading that boolean again, the live bug returns,
        so assert the two disagree exactly where they used to agree.
        """
        ctrl = _ctrl(_settling(4.90))
        ctrl._applied_clip_echo_guid = ADOPTED
        ctrl._applied_view_mode = "source"
        ctrl._applied_clip_echo_until = time.monotonic() + 3.0

        prov_says_remote, note = ctrl._provenance()
        self.assertTrue(prov_says_remote)
        self.assertIn("PROVENANCE", note)

        is_echo, _ = ctrl._view_assertion_is_echo(OTHER, "source")
        self.assertFalse(is_echo)


if __name__ == "__main__":
    unittest.main()
