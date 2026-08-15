## Why

A peer that has joined a session has no way of knowing whether it ended up
looking at what it was told. Three separate defects in a single day's testing
(2026-08-15) all shared that shape, and every one was found by a person looking
at two screens rather than by anything in the system noticing:

- the joiner broadcast its own first clip at frame 0 and moved the host onto it;
- the joiner landed on the right clip at the wrong frame, because the snapshot
  carried `current_time` and no `view_mode`/`clip_guid`;
- the joiner landed on the right clip at frame 0, because the seek died with the
  playhead its own view switch had just replaced.

Each was silent. The joiner applied a snapshot, believed itself synchronised,
and displayed something else — and the session's only evidence was two people
comparing monitors.

The comparison this needs already exists and is already sanctioned for it.
`sync-state-projection` defines `project_state` as "the one source of truth for
what 'in sync' means", covering the active timeline, per-track clip order, the
current frame and the display target, and its spec requires it to be
"importable by both the record/test side **and the OpenRV/xStudio client
integrations**". `diff_states` returns the differences in readable form, and
`export_state()` produces a peer's own state "without touching the network" and
"works on any peer, not only the master". Today only `sync_test` uses any of it.

Every one of those three defects would have produced a non-empty diff.

## What Changes

- **A joining peer checks what it ended up displaying against what it was sent**,
  once its session build has settled, using the existing projection and diff.
- **The result is reported, not repaired.** A mismatch is surfaced to the user
  and recorded in the log with the specific differences named.
- **The check is bounded to the join.** It runs after adopting a snapshot, not
  continuously, so it is a statement about one known moment rather than
  open-ended drift detection.
- **Detection is separated from any response.** Repair is deliberately excluded;
  see Impact.

## Capabilities

### New Capabilities

- `post-join-state-confirmation`: after adopting a session snapshot, a peer
  SHALL compare what it is displaying against what it was sent, and report any
  difference rather than assuming the adoption worked.

### Modified Capabilities

- `session-state-ui`: the session panel reports whether this peer confirmed the
  state it joined with, so a user can tell a verified view from an unverified
  one.

## Impact

- **Repair is an explicit non-goal.** A confirmation that reports is
  diagnostics; one that repairs is reconciliation, with its own failure modes.
  The project has twice concluded that acting on an uncertain signal is worse
  than the condition it detects — a timed-out identity read must not be read as
  a deletion, an unreadable position must not be read as frame 0. The detector
  earns confidence first; a follow-up change may act on it.
- **Distinct from `structure-divergence-recovery`, and deliberately so.** That
  capability covers *structure* a peer was not permitted to broadcast, declares
  divergence "from the attempt, not from later evidence of mismatch", and repairs
  by rebuilding from the master. Its objection to mismatch-detection is that a
  peer "cannot distinguish 'I edited something I may not edit' from 'I have not
  caught up yet'". Neither ambiguity exists here: the snapshot *is* the expected
  value, and the moment of comparison is known.
- `python/otio_sync_core/state_projection.py` — used, not changed, unless the
  frame-comparison policy below needs a scoped variant.
- Both plugins — the check after the join settles, and its report.
- `python/otio_sync_core/session_state.py` and both panels — the outcome.
- **Risk: a false mismatch is worse than no check.** A peer that reports
  divergence it does not have teaches users to ignore the indicator, which costs
  more than the indicator was worth. `diff_states` already carries
  `frame_tolerance` and `compare_frame` for exactly this reason.
- **Risk: a legitimately-advancing frame.** `compare_frame` exists because "a
  snapshot's frame is a point-in-time, and live playback advances past it by
  validation time even when both clients stay in lockstep". A joiner adopting a
  *playing* host will differ legitimately.
- **Risk: checking before the join has settled.** `export_state()` on a peer
  mid-build reports a real but meaningless mismatch. The check must run after
  the same settling point the join adoption waits for.
- No wire-format change and no new message: both sides of the comparison are
  already available locally.
