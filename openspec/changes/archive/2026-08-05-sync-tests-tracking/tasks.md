## 1. Failure kind classification

- [x] 1.1 Define the `fail_kind` enum (`state_mismatch`, `checkpoint_timeout`, `missing_media`, `log_error_signature`, `annotation_missing`, `structural_consensus`, `otio_export`) in `sync_test/python/sync_test/runner.py`.
- [x] 1.2 At each of the seven existing `fail_reason = ...` assignment sites in `run_test()` (missing media via `compare_states`, live state-mismatch after `MAX_DIVERGENCE_TIME`, frame-checkpoint mismatch via `validate_checkpoint`, annotation-presence check, structural consensus via `compare_full_states`, otio-export compare, known-bad log signature), attach the corresponding `fail_kind` alongside the existing message — do not infer kind from message text.
- [x] 1.3 Change `run_test()`'s return type from `bool` to a small result object/tuple carrying `(passed, fail_kind, message, converged_late, time_to_converge)`.
- [x] 1.4 Update `run_all()` and `cli.py` (the only in-repo callers) for the new return shape.

## 2. Bounded retry for convergence-timing failures

- [x] 2.1 Wrap the live state-mismatch check (`MAX_DIVERGENCE_TIME = 10.0`) so that on failure it retries once with the deadline doubled (20.0s) before returning a final failure.
- [x] 2.2 ~~Wrap frame-checkpoint validation with the same one-retry-at-2x pattern.~~ **Reverted** — a frame checkpoint asserts a point-in-time value against a still-advancing recording, so retrying past the recording's hold window cannot recover and the extra blocking cascades into later checkpoints. Left single-shot; see 2.6.
- [x] 2.3 Wrap terminal structural consensus polling (`compare_full_states`, hardcoded `deadline = 15.0`) with the one-retry-at-2x pattern. Valid here because it runs after playback stops — no moving target.
- [x] 2.4 Ensure `missing_media`, `log_error_signature`, `annotation_missing`, and `otio_export` failures skip the retry path entirely and fail immediately.
- [x] 2.5 When a retry converges, mark the result `converged_late: true` and count it as a pass; when a retry still fails, preserve the original `fail_kind` on the final failure.
- [x] 2.6 **Regression fix.** Scope the 2x retry to checks with no moving target (live peer-vs-peer mismatch watch; terminal structural consensus). Remove it from the two mid-playback oracle checkpoints — frame checkpoints (back to single-shot) and state checkpoints (back to a single 10s window, not 20s). Caught verifying `xstudio_selects`: the doubled 20s blocking state-checkpoint poll held the validation loop from replay-time ~3.2s to ~22.1s, so the frame checkpoint at t=9.5s — valid for a ~12s window (recording silent 9.54→21.51) — wasn't checked until t≈23s, after `playing=True` at 21.51 had put both apps into free-running playback. It reported frames 33/0 and failed despite being correct. Document the scoping rule at `RETRY_MULTIPLIER` and at both checkpoint sites.
- [x] 2.7 Log convergence timing on every timer-based check, pass or fail, as time-since-the-recording-event (directly comparable to `checkpoint_validation_delay`) rather than only retry-phase duration — so tuning that setting is a readable number instead of a bisection search.

## 3. Convergence margin capture

- [x] 3.1 Record `time_to_converge` (elapsed time against the applicable deadline) for every checkpoint/consensus/live-mismatch check that passes, not only ones that fail or retry.
- [x] 3.2 Thread `time_to_converge` through to `run_test()`'s result object alongside `fail_kind`/`converged_late`.

## 4. Run-history persistence

- [x] 4.1 Add a JSONL writer that appends one entry per test per run to `sync_test/run_history.jsonl`: `{test, timestamp, git_sha, result, fail_kind, converged_late, time_to_converge, recording}`.
- [x] 4.2 Call the writer from `run_test()`/`run_all()` on every completion, pass or fail.
- [x] 4.3 Resolve `git_sha` via the current repo HEAD at run start; tolerate a dirty/detached worktree without crashing the run.
- [x] 4.4 Git-track `sync_test/run_history.jsonl` (not gitignored) per design.md's rationale — verify it isn't caught by an existing `.gitignore` pattern for `sync_test/logs/` or similar.
- [x] 4.5 Record which `recording:` (if any) a test read from, on `TestResult` and in the history entry — `null` for a pure `commands:`/`fixtures:` script-driven test. Also log it live at test start (`Reading from recording: <path>` / `No recording — script-driven via explicit commands/fixtures.`).

## 5. Known-broken status handling and CLI reporting

- [x] 5.1 In `run_all()`'s summary, look up each test's `status` from config; a `known_broken` test that failed SHALL be excluded from the overall suite pass/fail exit code but still shown in the summary with its `fail_kind`.
- [x] 5.2 A `known_broken` test that unexpectedly passes SHALL be flagged distinctly in the summary (e.g. an XPASS-style marker), not printed as an ordinary pass.
- [x] 5.3 Update the summary table to show `fail_kind` (or the "converged late" marker) next to each non-clean-pass result instead of only ✅/❌.
- [x] 5.4 Add per-test wall-clock `duration` to `TestResult`/history, show it in the summary and the single-test CLI path; print total suite run time at the end of `run_all()`'s summary.
- [x] 5.5 Add `TestRunner.load_history()`/`_format_prev_result()`; snapshot history *before* a run starts and show each test's immediately-previous result plus a last-5 ✅/❌ trend strip in the summary (and the single-test CLI path).
- [x] 5.6 Add `[i/total]` suite-position to the `▶ RUNNING TEST` banner when running via `run_all()`; omitted for a single-test `--test <name>` invocation.

## 6. Test suite configuration schema

- [x] 6.1 Add `description`, `status` (`stable` default | `known_broken`), and `blocked_by` (required when `status: known_broken`) to the config loader in `sync_test/python/sync_test/config.py`.
- [x] 6.2 Enforce `description` as required, and `blocked_by` as required when `status: known_broken`, only when the loaded config is `sync_tests.yaml` — reject with a clear error identifying the offending test name. Other config files (`sync_tests_xstudio.yaml`, `sync_demos.yaml`) load without these fields, since they duplicate a subset of `sync_tests.yaml`'s entries rather than being independently authored.
- [x] 6.3 Update `sync_test/README.md`'s example config snippet to include the new fields, noting they apply to `sync_tests.yaml` specifically.

## 7. Backfill existing tests

- [x] 7.1 Add a `description` to every test entry currently in `sync_test/sync_tests.yaml`. We treat sync_tests.yaml as the main place descriptions are stored, the other yaml files are subsets of this.
- [x] 7.2 Confirm all three suites still load cleanly — `sync_tests.yaml` under the new required-field validation, and `sync_tests_xstudio.yaml`/`sync_demos.yaml` unaffected by it.

## 8. Orphaned `logs/` archaeology

- [x] 8.1 For each of the 14 undocumented directories under `sync_test/logs/` (`late_join_repro`, `late_join_repro_xs`, `manual_xs_pen_debug`, `manual_rv_capture_test`, `calibrate_text_scale`, `reorder`, `text_annotations`, `missing_media`, `delete_media`, `verify_pen_replay_noregress`, `verify_text_replay_fix`), reconstruct intent via directory mtime correlated against `git log --oneline -- sync_test/` around that date, the OpenSpec archive (`openspec/changes/archive/`), and existing memory entries. We only need to do this if the recording is in sync_tests.yaml
- [x] 8.2 For each directory: either promote it into a real, described entry in the appropriate `sync_tests*.yaml` if it represents coverage not already present, or delete the directory if its scenario is confirmed superseded/dead (e.g. `reorder`/`text_annotations`/`missing_media`/`delete_media`, already replaced by `*_v2`/`*_notc` entries).
- [x] 8.3 Record the resolution (promoted vs. deleted, and why) for each of the 14 directories somewhere reviewable (PR description or a short note in this change's notes) so the archaeology isn't itself lost the way the original tests were. See `archaeology-notes.md` — resolution: none needed promotion, since none of the 11 remaining directories' recordings are currently referenced in `sync_tests.yaml`.

## 9. Verification

- [x] 9.1 Ran `delete_media_openrv` twice end-to-end against real spawned OpenRV instances: the new machinery runs without crashing on both a real failure and a real pass, correct exit codes (1 then 0), and the new "Result: FAILED (fail_kind) — message" line in `cli.py`. Those runs were script-driven, so they exercised only the final-coherence and structural-consensus retry sites; the JSONL-replay frame-checkpoint path was left unverified against real apps.

  **JSONL-replay path now verified (2026-08-05, `xstudio_selects`, recording-driven).** The frame checkpoint at t=9.5s failed its first attempt and the bounded retry fired end to end, logging `Checkpoint t=9.5s not yet matching after 5.0s — retrying up to 10.0s (playback stays frozen) before failing` — which also demonstrates the "Entering a retry is logged, not just its outcome" scenario against real apps rather than a unit test. The run was classified `checkpoint_timeout` and appended to `run_history.jsonl` with `time_to_converge: 10.05`, exercising Failure Kind Classification and Convergence Margin Reporting on the replay path too.

  **Full-suite run still owed — deliberately deferred, not dropped.** It is the same single run now owed by this change, `decouple-player-clock-arm-from-gate` (§4.3) and `fix-playback-position-echo-loop` (§7.4). Running it per-change is not possible anyway: the tree contains all three changes' edits and results cannot be attributed test-by-test. Note the `run_history.jsonl` baseline is no longer like-for-like — the harness changes committed the same day deliberately convert former false passes into failures — so that run needs reading as "which failures are corrections", not as a regression diff.
- [x] 9.2 Confirmed: two separate `./run_tests.sh` process invocations both appended to `sync_test/run_history.jsonl` (2 lines, not overwritten) — accumulation across process boundaries works.
- [x] 9.3 Not manually induced — happened for real. The first `delete_media_openrv` run hit a genuine structural-consensus mismatch; the retry-at-2x fired and ran the full 30s window (`CONSENSUS_BASE_DEADLINE=15 * RETRY_MULTIPLIER=2`) before giving up, recorded as `time_to_converge: 30.429, converged_late: false`. Confirms the retry path is real and correctly bounded.
- [x] 9.4 Verified via direct unit-level call (no app spawn needed): `compare_states` classifies a `media_exists: False` state as `fail_kind: missing_media` in <0.05s with no sleep/retry inside it; confirmed the caller-side loop's `if diff_kind == FailKind.MISSING_MEDIA: ... break` (runner.py:887) bypasses the retry-tracking branch entirely, matching the non-timing-eligible design.
