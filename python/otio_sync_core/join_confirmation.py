# SPDX-License-Identifier: Apache-2.0
"""Post-join state confirmation: did joining actually work?

A joiner adopts a ``STATE_SNAPSHOT`` and then displays whatever its own session
build produced.  Nothing compares the two, so a joiner that lands on the wrong
clip or the wrong frame believes it is synchronised and says so.  This module
answers "did joining work?" at the one moment the expected value is known, by
reusing :mod:`otio_sync_core.state_projection` — the session's own definition
of "in sync" — rather than inventing a second one (see
``openspec/changes/post-join-state-confirmation``, design D1).

Report-only.  Nothing here mutates a manager, requests state, or broadcasts;
see design D5 for why.
"""

from __future__ import annotations

from typing import Any

from .state_projection import diff_states, project_state

__all__ = [
    "CONFIRMED",
    "MISMATCHED",
    "NOT_CONFIRMED",
    "DEFAULT_FRAME_TOLERANCE",
    "confirm_join_state",
]

#: The state this peer ended up displaying matched the snapshot it was sent.
CONFIRMED = "confirmed"
#: The state this peer ended up displaying differed from the snapshot.
MISMATCHED = "mismatched"
#: The join never reached a checkable state (build never settled, or the
#: adoption exhausted its retry budget).  Distinct from both of the above —
#: "not checked" and "checked and matching" are different facts.
NOT_CONFIRMED = "not_confirmed"

#: Starting point for the frame tolerance (design's Open Questions): whether a
#: joiner should be stricter than ``diff_states``' own default is answerable
#: from the logs this capability produces, not from guessing up front.
DEFAULT_FRAME_TOLERANCE = 5


def confirm_join_state(
    expected_payload: "dict[str, Any]",
    actual_payload: "dict[str, Any]",
    *,
    frame_tolerance: int = DEFAULT_FRAME_TOLERANCE,
) -> "tuple[str, list[str]]":
    """Compare what a peer ended up displaying against the snapshot it joined with.

    Both arguments are ``StateSnapshot``-shaped payloads (the same shape
    ``project_state`` already accepts): *expected_payload* is the snapshot as
    received — retained for the duration of the join, not reconstructed from
    what was adopted (design D2) — and *actual_payload* is this peer's own
    state, sourced from what it is actually displaying rather than from its
    record of having applied the snapshot (design D1's first risk; see the
    host integration for how each field is sourced).

    Frame comparison is gated on the *snapshot's* ``playing`` flag, not this
    peer's: a snapshot's frame is a point in time, and a joiner adopting a
    still-playing host will legitimately have moved past it by the time it
    finishes building (design D4).

    :param expected_payload: The ``STATE_SNAPSHOT`` payload this peer was sent.
    :param actual_payload: This peer's own state payload, display-sourced.
    :param frame_tolerance: Allowed absolute frame difference; forwarded to
        :func:`~otio_sync_core.state_projection.diff_states`.
    :returns: ``(outcome, differences)`` — *outcome* is :data:`CONFIRMED` or
        :data:`MISMATCHED` (never :data:`NOT_CONFIRMED`: that outcome belongs
        to a join that never reached a checkable state, which this function is
        not in a position to know about — see the host integration's
        settling/retry logic). *differences* is empty for a confirmed join,
        else the itemised list from ``diff_states``.
    """
    expected = project_state(expected_payload)
    actual = project_state(actual_payload)
    snapshot_playing = bool((expected_payload.get("playback_state") or {}).get("playing"))
    differences = diff_states(
        expected,
        actual,
        frame_tolerance=frame_tolerance,
        compare_frame=not snapshot_playing,
    )
    outcome = MISMATCHED if differences else CONFIRMED
    return outcome, differences
