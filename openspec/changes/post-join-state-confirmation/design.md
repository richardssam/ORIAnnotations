## Context

See `proposal.md` — Why. What already exists, since this change is mostly a new
caller rather than new machinery:

| Component | What it gives us | Used today by |
| --- | --- | --- |
| `project_state(payload)` | canonical form: timelines + clip order, active timeline, frame, display target | `sync_test` only |
| `diff_states(expected, actual, frame_tolerance=5, compare_frame=True)` | readable list of differences; empty means match | `sync_test` only |
| `manager.export_state()` | this peer's own state, `StateSnapshot`-shaped, no network | `sync_test` inspector |

`sync-state-projection` already requires `project_state` to be "the one source of
truth for what 'in sync' means" and "importable by both the record/test side and
the OpenRV/xStudio client integrations". The client half of that requirement has
simply never been taken up. `export_state()`'s docstring already notes it "works
on any peer, not only the master".

Two constraints shape the design:

- **`export_state()` reads the manager's reducer, not the screen.** That is the
  right source for timelines and active timeline, but the spec requires the
  peer's side to reflect *what it is displaying*. Where the manager's record and
  the display can disagree is exactly where the bugs were.
- **The join settles asynchronously.** `do_load_timelines` parks an on-screen
  source applied on a later tick; the playhead is acquired after that;
  `apply_join_playback_state` re-queues until it exists. Any check on a fixed
  delay races all three.

## Goals / Non-Goals

**Goals:**

- Answer "did joining work?" at the one moment the expected value is known.
- Reuse the session's existing definition of agreement rather than inventing a
  second one.
- Make the answer readable after the fact, and visible during.

**Non-Goals:**

- **Repair.** Explicitly excluded; see D5.
- Continuous drift detection. This is a bounded check, not a monitor.
- A new message or wire field. Both sides are available locally.
- Changing `project_state` / `diff_states` semantics. New caller, same rules.

## Decisions

### D1 — Compare the peer's own projection against the snapshot's, using the existing pair

```python
expected = project_state(snapshot_payload_i_was_sent)
actual   = project_state(manager.export_state())
differences = diff_states(expected, actual,
                          frame_tolerance=_FRAME_TOLERANCE,
                          compare_frame=not snapshot_says_playing)
```

Using the shared projection is not merely convenient: the spec requires the
comparison to cover what the session agrees "in sync" means. A bespoke check
would be a second definition of agreement, free to drift from the one
`sync_test` asserts against — so a peer could report itself synchronised while
the test suite called the same state divergent.

*Alternative rejected:* compare a hand-picked subset (clip + frame). Cheaper,
and it would have caught all three motivating bugs — but it answers a narrower
question than the one the panel would then be reporting, and the gap between
them is invisible until it matters.

### D2 — Keep the snapshot payload as received, and compare against that

The joiner must retain the payload it was sent, not reconstruct an expectation
from what it did with it. Reconstructing means comparing a stored intention
against itself, which confirms nothing — the failure being detected is precisely
an intention that did not take effect.

The manager already stores the pieces (`playback_state`, `display_state`,
`_timelines`) but stores them *as adopted*. This change keeps the raw payload
for the duration of the join, and drops it once the outcome is recorded.

### D3 — Sequence the check behind the join adoption, not behind a timer

`apply_join_playback_state` already re-queues itself until a playhead exists and
then applies the session's view. The confirmation is queued from the point that
apply *succeeds*, so it inherits that settling rather than guessing at it.

Where the adoption gives up — it has a bounded attempt count — the outcome is
recorded as **not confirmed**, which the spec distinguishes from both a match and
a mismatch. A peer that never became checkable has not passed a check.

*Alternative rejected:* a fixed delay after `STATE_SNAPSHOT`. It races the build,
the on-screen source, and the playhead acquisition independently, and the
observed spreads (1.6 s to the show_atom, 0.4 s more to the playhead) vary with
media load.

### D4 — Frame comparison is conditional on the snapshot's own play state

`diff_states` already carries `frame_tolerance` and `compare_frame` because "a
snapshot's frame is a point-in-time, and live playback advances past it by
validation time even when both clients stay in lockstep".

The rule: compare the frame when the snapshot describes a *paused* session; skip
it when it describes a playing one. Tolerance applies either way. The decision
reads the snapshot's `playing`, not this peer's — the question is whether the
expected value was still moving when it was captured.

This is the requirement most likely to make the indicator wrong in the ordinary
case if it is got wrong, which is why it is specified rather than tuned.

### D5 — Report only, and why that is the whole change

The confirmation writes an outcome and nothing else: no state request, no
broadcast, no local change.

Twice now this project has concluded that acting on an uncertain signal costs
more than the signal is worth — an identity read that timed out must not be read
as a deletion, a position that could not be read must not be read as frame 0. A
detector wired to a repair inherits its own false-positive rate as a new failure
mode, and a repair here means rebuilding a session under a user who is looking
at it.

Report-only also makes the change safe to ship while its own accuracy is
unknown: a wrong report costs a wrong indicator, and no session behaviour depends
on it. The follow-up that acts on it should be a separate change with its own
evidence — by which time the logs will say how often this fires and why.

### D6 — Outcome lives on the manager, surfaced through the shared projection

The outcome (confirmed / mismatched / not confirmed, plus the differences) is
recorded on the manager and exposed through `session_state_snapshot`, so both
panels read the same fact rather than each deriving it — the rule
`session-state-ui` already sets for role, master, host and the leases.

## Risks / Trade-offs

- **A false mismatch is worse than no check** → a user who learns to disregard
  the indicator is worse off than one who never had it. Mitigated by D4's
  conditional frame comparison, by `frame_tolerance`, and by report-only: a false
  positive costs a wrong label, not a wrong session.

- **`export_state()` reads the reducer, not the screen** → the manager can
  believe a view it did not achieve, which is the failure mode most worth
  catching and the one this is weakest at. Where the plugin holds the displayed
  view (`_cur_view_mode` / `_cur_clip_guid`, the playhead's position), the check
  should prefer it. Task 2 establishes which fields `export_state()` derives
  from the display and which from the record; if the answer is "all from the
  record", this change is worth less than it looks and should say so rather than
  ship a check that confirms itself.

- **The check runs once** → a peer that diverges later is not covered. That is
  the boundary between this and continuous reconciliation, and it is deliberate:
  the snapshot is the only expected value this peer is ever handed.

- **Overlap with `structure-divergence-recovery`** → both concern a peer whose
  state does not match the session. They differ in trigger (a known moment vs. a
  refused broadcast), domain (view and structure as displayed vs. structure as
  edited), and response (report vs. rebuild). If that change later gains a
  repair trigger, this outcome is a candidate input to it — which is an argument
  for recording the outcome in a shared place (D6), not for merging them now.

## Migration Plan

1. Establish what `export_state()` actually reflects (task 2). If it cannot see
   the display, decide the peer's side of the comparison before building on it.
2. Land the comparison and the log line, with no UI. Dark: the logs alone will
   say how often it fires and whether it is trustworthy.
3. Surface the outcome in the projection and both panels.
4. Review the collected outcomes before proposing any repair.

**Rollback:** the check writes an outcome and nothing else, so disabling it
removes an indicator and changes no behaviour.

## Open Questions

- The frame tolerance value. `diff_states` defaults to 5; whether a joiner
  should be stricter is answerable from step 2's logs and changes nothing
  structural.
- Whether the confirmation should also run after a mid-session snapshot
  re-request, if `structure-divergence-recovery` introduces one. Deferrable: the
  same check at a second known moment, and nothing here changes if the answer is
  yes.
