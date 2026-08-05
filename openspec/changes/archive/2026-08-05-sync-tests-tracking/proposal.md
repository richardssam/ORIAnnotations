## Why

The sync test suite currently has no memory. Every run's pass/fail result is printed to stdout and discarded — `run_test()` computes a detailed failure reason (`fail_reason`) but never returns or persists it, and `logs/<test_name>/` is overwritten in place on every run. This makes three real problems impossible to solve today:

1. **Occasional/timing failures can't be told apart from real breaks.** There's no history to distinguish "fails 1 in 5 runs, always in the same convergence step" from "just broke." Classifying a test as flaky currently relies on someone's memory of past runs.
2. **Known-broken tests (e.g. waiting on another OpenSpec change) have no declared status.** A red test today looks identical whether it's a fresh regression or an already-understood, already-tracked issue blocked on other work.
3. **Test intent isn't recorded anywhere.** `sync_tests.yaml` entries carry only mechanical fields (`recording`, `apps`, `script_driven`). 14 log directories under `sync_test/logs/` (`verify_fix`, `verify_fix2`, `late_join_repro`, `manual_xs_pen_debug`, ...) already have no corresponding YAML entry and no record of what bug they were chasing — proof this has already cost real knowledge, recoverable now only via git-log/OpenSpec-archive archaeology.

## What Changes

- Add per-test metadata fields to `sync_tests.yaml`, the canonical suite definition: `description` (required, human-readable intent — what scenario this exercises and why it exists), `status` (`stable` | `known_broken`, default `stable`), and `blocked_by` (an OpenSpec change name, only meaningful when `status: known_broken`). `sync_tests_xstudio.yaml` and `sync_demos.yaml` duplicate a subset of `sync_tests.yaml`'s entries for convenience and are not required to carry their own copy of these fields.
- Backfill `description` on every currently-defined test in `sync_tests.yaml`, and run an archaeology pass on the 14 orphaned `sync_test/logs/` directories that have no YAML entry — reconstruct intent from git history / the OpenSpec archive / existing memory where possible, then either promote each into a real (described) entry in `sync_tests.yaml` or delete its log directory as confirmed dead.
- Turn the runner's discarded `fail_reason` string into a structured `(fail_kind, message)` pair. `fail_kind` is one of: `state_mismatch`, `checkpoint_timeout`, `missing_media`, `log_error_signature`, `annotation_missing`, `structural_consensus`, `otio_export`.
- For failures whose `fail_kind` is convergence-timing-related (`state_mismatch`, `checkpoint_timeout`, `structural_consensus`), retry once at 2x the original wait/deadline before declaring a hard failure. Failures whose `fail_kind` can never be fixed by waiting longer (`missing_media`, `log_error_signature`, `otio_export`, `annotation_missing`) fail immediately, with no retry.
- Record whether a retried failure ultimately passed ("converged late") vs. still failed at 2x — this distinction is what separates timing flakiness from a real regression, and gets written to history rather than only logged to stdout.
- Add an append-only, git-tracked run-history log (JSON lines) that `run_all()`/`run_test()` write to on every run, pass or fail: `{test, timestamp, git_sha, result, fail_kind, converged_late, time_to_converge, recording, duration}`. `time_to_converge` is captured on passes too, not just failures, so a test converging close to its deadline is visible as an early warning before it ever goes red. `recording` names the `.jsonl` file a test replayed/derived commands from (or `null` for a pure `commands:`/`fixtures:` script-driven test), so history entries are self-describing without cross-referencing `sync_tests.yaml`. `duration` is the test's total wall-clock time (launch through teardown), distinct from `time_to_converge` (which measures a single check against its own deadline).
- `run_all()`'s summary table gains, per test: `fail_kind` (or "converged late" marker) instead of only ✅/❌, wall-clock duration, and a compact prior-results trend (the last up-to-5 recorded outcomes as a ✅/❌ strip, sourced from `run_history.jsonl` as it stood *before* this run) — so a test that's been flapping is visible at a glance in the same summary a developer already reads. The summary also prints the total suite run time. The single-test (`--test <name>`) CLI path reports the same duration/prior-result information for that one test.
- Each test's `▶ RUNNING TEST` banner shows its position in the suite (`[3/10]`) when run via `run_all()`, so a developer watching a long run knows how far through it is. Omitted (not `[None/None]`) for a single-test `--test <name>` invocation, where it wouldn't mean anything.
- A `known_broken` test still runs; if it unexpectedly passes, the runner flags it distinctly (XPASS-style) rather than silently reporting green, since that's a signal to go check whether `blocked_by` can be closed out.

**BREAKING**: `run_test()`'s return type changes from `bool` to a result object/tuple carrying `fail_kind`; any external caller relying on the bare boolean return needs updating. (In-repo: only `run_all()` and `cli.py` call it today.)

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `ui-sync-testing`: `sync_tests.yaml` gains `description`/`status`/`blocked_by` fields as the canonical suite definition; the CLI runner persists a run-history log and reports `fail_kind` in its summary output instead of a bare pass/fail.
- `sync-test-state-validation`: checkpoint/structural-consensus failures are classified by `fail_kind`; convergence-timing-eligible failures get one bounded retry at 2x delay before failing, with the outcome recorded rather than discarded.

## Out of Scope

`frame_tolerance` (currently a blanket default of 5 frames, applied uniformly regardless of app or whether the checkpoint is genuinely held/parked) is confirmed to be masking real failures once tightened — an empirical probe already shows more tests turning red at lower tolerance. That is deliberately sequenced *after* this change: fixing it well requires distinguishing real drift from an unmodeled per-app offset, and doing that investigation is far more tractable once `fail_kind` classification and run-history exist to tell "converges late" apart from "never converges." Tightening `frame_tolerance` is expected to be a follow-on change once this one lands.

## Impact

- `sync_test/python/sync_test/runner.py`: `run_test()` return type, `fail_reason` → `(fail_kind, message)`, retry-at-2x logic for timing-eligible failures, run-history log writer.
- `sync_test/python/sync_test/cli.py`: adapt to the new `run_test()`/`run_all()` return shape.
- `sync_test/sync_tests.yaml`: new `description`/`status`/`blocked_by` fields on every entry. `sync_tests_xstudio.yaml`/`sync_demos.yaml` are unaffected — they remain undescribed subsets of `sync_tests.yaml`.
- `sync_test/logs/`: 14 orphaned directories reviewed; each promoted into a described YAML entry or deleted.
- New run-history file (path TBD in design.md) — git-tracked, append-only.
- No changes to `otio_sync_core` (production sync library) — this change is confined to the test harness.
