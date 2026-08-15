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

**Found live, 2026-08-15 20:55:** deferring the check at all — even by one
event-loop turn (`QTimer.singleShot(0, ...)` on OpenRV) or one poll tick
(re-queued on xStudio while a seek settles) — opens a window in which a
*second* `STATE_SNAPSHOT` can land on this peer before the deferred check
runs. `apply_snapshot` always overwrites `_pending_join_snapshot`, so the
check, once it finally runs, silently compares the display against whichever
snapshot is sitting there *then* — which may not be the one whose settling it
was scheduled to verify. Observed: a joiner's rebuild transiently showed the
wrong sequence, a second snapshot arrived before the deferred check fired, and
the check went on to report "confirmed" against that second snapshot —
masking the very mismatch this capability exists to catch, in the join it was
actually checking.

Fixed by pinning: the host integration captures `manager.join_generation`
(bumped on every `apply_snapshot`) at the moment it schedules the check, and
the check compares that captured value against the manager's current
generation before doing anything else — comparing, requeuing, or giving up.
A mismatch means a later join has superseded this one, and the check
abandons outright (records nothing) rather than reporting on the wrong join.
This does not make the superseding join's *own* build get checked — if it
never reaches its own `_on_synced`/settle point (as it did not here — no
rebuild fired for the second snapshot on this occasion), no confirmation is
recorded for it either. Recording nothing is the correct failure mode here
(design D5): an absent report costs a missing indicator; a wrong one costs a
false sense of having checked.

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

### D7 — Task 2's finding: which fields `export_state()` derives from the record, and what to do about it

Established by reading both plugins' assignment sites for `active_timeline_guid`
and `playback_state`, not by inspection of `export_state()` alone:

- **`playback_state.current_time` (frame) is always record-only.** Every write
  site — `manager._h_playback_set`, `manager.receive_and_apply_all`'s
  snapshot-receipt branch, `manager.apply_snapshot` — assigns the field
  directly from a received message. Neither host integration writes the
  physical playhead's position back into it after applying one. For a joiner
  specifically, `apply_snapshot` sets it from the retained snapshot itself, and
  nothing ever updates it again — so comparing `export_state()`'s frame against
  the snapshot's frame would compare the snapshot against a copy of itself,
  confirming nothing, in exactly the scenario (bug 2/3 in the proposal) this
  capability exists to catch.

- **`active_timeline_guid` is dual-written, on both hosts.** It is set
  directly from a received message's intent (`manager.apply_snapshot`,
  `manager._h_playback_set`'s `tl_guid`) **and** from a display-confirmed
  event once one actually fires (xStudio: the real `show_atom` handler in
  `on_global_playhead_event`, gated "whoever caused it"; OpenRV:
  `on_view_changed`, gated on `not self.plugin._rv_updating`). Because both
  writers share one attribute, its value cannot by itself distinguish
  "intended and later confirmed" from "intended and never confirmed" — which
  is the exact distinction this capability exists to draw.

- **`timelines` (structure) and most of `display_state` are safe as record.**
  Structure is the manager's own maintained OTIO tree, not view state; the
  compared display keys (`channel`, `annotations_visible`, after
  `_DROPPED_DISPLAY_KEYS` removes `pan`/`zoom`/`exposure`) are written back
  synchronously from a live readback wherever they are applied
  (xStudio `apply_display_state`, OpenRV `_apply_display_state`), so the
  record/display gap that matters is narrower there. Still read live where a
  live read is one call away (see below), since nothing is gained by not.

**Decision (per the gate in 2.2/2.3): source, not "not confirmable".** Both
hosts already have exactly the live-read primitives this needs, because
`broadcast_playback_state`'s own outgoing message is built the same way:

- xStudio: `PlaybackSyncController.current_playback_state()` for frame/playing
  (reads `active_playhead.position` live), and a new
  `_sequence_timeline_for_clip(_last_viewed_clip_guid)` for the active
  timeline — `_last_viewed_clip_guid` is set only inside the real `show_atom`
  handler, never from an applied message, so it carries none of
  `active_timeline_guid`'s ambiguity. `read_xs_display_state()` for display.
- OpenRV: a new `PlaybackSyncController.current_playback_state()` (mirrors
  xStudio's, built from `rv.commands` the same way `_broadcast_playback`
  already is) for frame/playing, and `_displayed_timeline_guid()` — already
  display-sourced, per D1's investigation — for the active timeline.
  `_read_rv_display_state()` for display.

The confirmation therefore starts from `manager.export_state()` for structure,
and overwrites just `active_timeline_guid` and `playback_state.current_time`/
`playing` with these live reads before projecting. This is not the "checks
less than the session's own definition of agreement" the spec warns against —
`project_state` still runs over the full shape — it is sourcing two fields of
that shape from the display instead of the record, which is what the spec's
"derived from what it is actually displaying" requirement asks for.

**Gate (2.3) verdict: does not apply.** Not every compared field comes from
the record — frame and active-timeline identity, the two fields the three
motivating bugs actually broke, are display-sourced. The check is not
confirming the manager against itself.

### D8 — Never fall back to the record when the display isn't ready yet

**Found live, 2026-08-15 22:00, via task 6.3's disabled-adoption reproduction.**
Disabling `apply_playback_state` on the xStudio side should have made the
confirmation report a mismatch — the joiner never adopted anything. It
reported `confirmed` instead.

Cause: the initial implementation of D7's live-sourcing built the "actual"
payload by starting from `manager.export_state()` and *conditionally*
overwriting `active_timeline_guid`/`playback_state.current_time` only when a
live value was available (`if seq_tl_guid: ...`, `if live is not None: ...`).
When no real `show_atom` had fired yet — exactly the disabled-adoption case,
where nothing was displayed, or the more ordinary case of the confirmation
running before the join's own view switch has produced its confirming
`show_atom` — those conditions were false, and the code silently proceeded
with `export_state()`'s own value for that field. But that value is the
*record*: written by `apply_snapshot` directly from the very snapshot the
confirmation was about to compare against. Comparing it against itself always
matches, which is precisely the self-confirmation the task 2 gate (D7) exists
to rule out — reintroduced by a fallback the gate didn't anticipate.

Fixed by treating "no live value yet" as "not settled yet", not as "assume
the record": both hosts now require a genuine live reading before proceeding
at all —

- xStudio: `current_playback_state()` must succeed *and*
  `_last_viewed_clip_guid` must be non-``None`` (a real `show_atom` has fired
  since connecting). Missing either re-queues, using the same
  `_JOIN_CONFIRM_MAX_ATTEMPTS` budget already in place for the deferred-seek
  wait — one more condition on the same wait, not a new mechanism.
- Both hosts: once a live reading *is* available, `active_timeline_guid` is
  now always taken from it, including when the display-side resolution
  legitimately comes back empty (e.g. an isolated clip that belongs to no
  tracked sequence) — that is a real answer ("no active sequence timeline"),
  not a reason to fall back to the record's guess.

Where xStudio's retry budget still runs out, the outcome is `NOT_CONFIRMED` —
the same "never became checkable" case D3 already covers, now covering one
more way of never becoming checkable.

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
