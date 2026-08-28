## 1. Reproduce

- [x] 1.1 Reproduce the unresolved-patch stall directly, without depending on the election race: apply an `INSERT_CHILD` naming an absent target to a synced manager and assert the peer never requests state. This is the current behaviour and the test should fail after §3.
- [x] 1.2 Record the baseline pass rate of `contended_position_scrub` over 10 runs, with power/load/swap sampled alongside (see `feedback_suite_results_need_memory_check`). Recent history is `F P F F`, so a rate is needed before any claim that this change moved it.

## 2. Record the unresolved target

- [x] 2.1 Extend `record_unresolved_patch` to retain the unresolved target GUID and first-seen time, alongside the existing counter and log line, without changing either.
- [x] 2.2 Drop a retained target once it appears in the object map, so a patch that lands normally moments later leaves nothing behind.
- [x] 2.3 Collapse repeats: several patches naming one absent target retain one entry, keyed by target.
- [x] 2.4 Bound the retained set the way `unresolved_patches` is already bounded, so a peer failing continuously cannot grow it without limit.

## 3. Fire the request

- [x] 3.1 Add the `tick()` branch: a target still absent past `UNRESOLVED_SETTLE` requests state from the master via `request_state(reason="recovery")`.
- [x] 3.2 Share `RECOVERY_COOLDOWN` with the existing structural-recovery trigger so the two cannot together exceed the session's request rate.
- [x] 3.3 Handle the no-master and self-is-master dead ends the way structural recovery does: no request, record unreachable.
- [x] 3.4 Clear retained targets once a snapshot has been applied, so the answer to the request ends the condition.
- [x] 3.5 Confirm `structure_diverged` is untouched on every path added here.

## 4. Tests

- [x] 4.1 A patch against an absent target that stays absent requests state (spec scenario 1).
- [x] 4.2 A target that arrives within the settle window requests nothing (spec scenario 2) — the false-positive case `structure-divergence-recovery` warns about.
- [x] 4.3 A burst against one missing target produces exactly one request.
- [x] 4.4 A master holding an unapplied patch issues no request and records unreachable.
- [x] 4.5 Resync does not set `structure_diverged`, and does not clear it if already set.
- [x] 4.6 Applying the resulting snapshot clears the retained targets.
- [x] 4.7 `./run_tests_core.sh` passes.

## 5. Verification

- [x] 5.1 Re-run `contended_position_scrub` 10x; compare against the 1.2 baseline.
- [x] 5.2 Confirm from the plugin log that the `UNRESOLVED PATCH` line is now followed by a state request and that the peer ends up holding the media.
- [x] 5.3 Run the full 36-test suite and confirm no regression, re-running any failure once before attributing it (failures cluster within a run).
- [x] 5.4 Record the residual failure kinds. This change does not address the master-election conflict, so `contended_position_scrub` may still fail for that reason — say so explicitly rather than reporting a rate alone.
