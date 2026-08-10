## Why

`broadcast-ownership` (archived — write leases for position/structure/display)
set out to retire ~8 time-window echo guards in the xStudio plugin, on the
premise that a lease making a category single-writer makes the guard that used
to infer "was this echo mine?" redundant. A live two-app soak on 2026-08-10,
including two deliberately-contended test cases, confirmed the lease mechanism
itself converges cleanly and repeatably — but re-reading the candidate guards
found the premise no longer holds for most of them. The codebase absorbed
substantial hardening from `fix-visibility-authority-bypass` and related work
in the time between the replacement table being written and being acted on,
and several guards picked up second jobs that have nothing to do with
broadcast echo:

- **`_loop_mode_apply_suppress_until`** (`xstudio_plugin/ori_sync/playback_sync.py`)
  has three arm sites sharing one read site. Only one is a remote-apply echo
  case; the other two suppress echo from *local self-writes* — carrying loop
  mode onto a newly-acquired playhead, and forcing loop on an isolated clip.
  Deleting the guard outright breaks the two local cases.
- **`_structural_mutation_suppress_until`** (`xstudio_plugin/ori_sync/structure_sync.py`)
  has five arm sites that are all cleanly remote-apply-only, but its own
  comments describe a second job: giving xStudio's actor model time to settle
  after a `load_otio` / `remove_container` call before local structural polls
  re-scan it. That is actor-model consistency, not broadcast authority, and a
  lease does not touch it.
- **`_playback_apply_suppress_until`** and its close relatives
  (`_last_applied_frame` / `_last_polled_frame`, `_last_received_frame`, the
  throttled-flush recheck) are plausibly retirable but carry an unresolved
  timing gap: the guard's window is 0.4s, longer than the newer claim-horizon
  mechanism's 0.3s (`broadcast-ownership` D4). In that gap, a peer that
  already holds the lease from an earlier, unrelated claim could broadcast a
  stale echo of a *different* peer's just-applied frame as if it were a fresh
  local scrub. The contended soak checked eventual convergence, not this
  narrower during-handover window.

One guard (`_local_scrub_active_until`) turned out to be dead code — armed,
never read — and was deleted directly; no design work needed there. This
change is the deferred "dedicated future pass" `broadcast-ownership`'s
Group 3 called for, to actually retire what remains.

## What Changes

- **Split, don't delete, the mixed-purpose guards.** `_loop_mode_apply_suppress_until`
  and `_structural_mutation_suppress_until` each need their non-echo job (local
  self-write suppression; actor-model settling) separated into its own
  mechanism before the echo-suppression half can be safely removed. The split
  mechanism keeps working exactly as today; only the *echo* half becomes
  provably redundant and removable.
- **Close the claim-horizon timing gap** before treating
  `_playback_apply_suppress_until` and its relatives as retirable. Either
  shorten the guard's window to match the claim horizon, lengthen the claim
  horizon to match the guard, or establish why the mismatch is harmless in
  practice — and demonstrate it under contention (D5: a positive
  demonstration, not an absence of failures in the existing contended tests,
  which weren't built to probe this specific window).
- **Extend the contended test coverage** in `sync_test/sync_tests.yaml` to
  specifically exercise the during-handover window the current
  `contended_position_scrub` case doesn't probe: a peer holding the lease from
  an unrelated prior claim, receiving and applying a remote message, while its
  own async echo callback is in flight.
- Only once both of the above hold: delete the echo-suppression halves of
  `_playback_apply_suppress_until`, `_last_applied_frame` / `_last_polled_frame`,
  `_last_received_frame`, the throttled-flush recheck,
  `_loop_mode_apply_suppress_until`'s remote-apply arm, and
  `_structural_mutation_suppress_until`'s echo half.

## Capabilities

### Modified Capabilities
- `xstudio-plugin-module-structure`: the mixed-purpose guards this change
  splits are documented there today only as implementation detail; this adds
  a requirement that apply-scope/settling suppression and broadcast-echo
  suppression are distinct mechanisms, not one guard doing both jobs.

## Impact

- `xstudio_plugin/ori_sync/playback_sync.py` — `_loop_mode_apply_suppress_until`
  split; `_playback_apply_suppress_until` family retired once the timing gap
  closes.
- `xstudio_plugin/ori_sync/structure_sync.py` — `_structural_mutation_suppress_until`
  split.
- `sync_test/sync_tests.yaml`, `sync_test/python/sync_test/runner.py` — new
  contended case(s) for the during-handover window.
- `docs/visibility_authority_guards.md` — update the 2026-08-10 findings
  section once each guard's split/retirement lands.
- `openspec/changes/broadcast-ownership` (archived) — this change completes
  its deferred Group 3 remainder; no further action needed there.
- **Sequencing**: deliberately queued behind `session-roles`, not because of a
  technical dependency (broadcast-ownership's mechanism is independent of
  roles) but by product priority — picked up whenever `session-roles` closes
  or is paused.
- **Risk**: Low for the split (additive, no behavior change to the parts kept).
  The actual deletions are the same class of risk `broadcast-ownership`'s own
  Group 3 was gated on — D5's rule applies again: a guard cannot be shown
  unnecessary by an absence of firings, only by a positive demonstration under
  the specific contention it guards against.
