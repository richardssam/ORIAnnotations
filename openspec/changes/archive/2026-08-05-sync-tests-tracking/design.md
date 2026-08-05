## Context

See `proposal.md` - Why. Relevant current-state facts that shape the approach:

- `run_test()` in `sync_test/python/sync_test/runner.py` already computes a detailed `fail_reason` string at seven distinct call sites (missing media, live state mismatch, checkpoint mismatch, annotation-count, structural consensus, otio-export, known-bad log signature) but discards it — the function returns a bare `bool`, consumed only by `run_all()` and `cli.py`.
- There are already three independent, uncoordinated "wait and see" mechanisms in the same file: `MAX_DIVERGENCE_TIME = 10.0` (live state-mismatch polling), a hardcoded `deadline = 15.0` (structural consensus polling), and `checkpoint_validation_delay` (per-test YAML override, default 1.5s, frame checkpoints only). None share a retry/backoff policy.
- `logs/<test_name>/` is overwritten in place on every run — there is currently no artifact, anywhere, that survives across runs.
- `frame_tolerance` (default 5, never overridden per-test today) is explicitly out of scope for this change (see proposal.md - Out of Scope) but the `fail_kind`/history machinery built here is what makes that follow-on change tractable later.
- This is a local-only test harness — there is no CI. Every run-history entry will reflect whichever machine ran it; the `git_sha` field exists so history can at least be correlated with what code was under test, not to distinguish machines.

## Goals / Non-Goals

**Goals:**
- Every test run leaves a durable, structured trace of what happened and why, whether it passed, failed, or converged late.
- A test that is already known-broken and blocked on other work does not show up as suite-failing noise, but is also never silently forgotten (still runs, still visible, XPASS-flagged if it starts passing).
- Timing-sensitive failures get exactly one bounded, cheap second look before being called a real failure — bounded specifically so a genuinely-broken test still fails fast rather than doubling its own wait time indefinitely.
- Every currently-defined test (and each of the 14 orphaned `logs/` directories) ends this change with a real `description`.

**Non-Goals:**
- Not building CI infrastructure — the run-history log is a local file, written by local invocations. Making it CI-aware is future work.
- Not touching `frame_tolerance` or any other assertion-correctness logic (see proposal.md - Out of Scope).
- Not implementing automatic `known_flaky` classification from history data yet — this change produces the data (`converged_late`, `time_to_converge` trends); a later change can add the classifier that reads it and proposes status changes.
- Not changing what gets asserted (checkpoint logic, structural diff, annotation checks) — only how failures are classified, retried, and recorded.

## Decisions

### `fail_kind` as a small closed enum, attached to the existing message, not replacing it
Each of the seven existing `fail_reason` call sites already knows exactly what kind of failure it is (the code path that sets `fail_reason = f"{name} reports missing media..."` is definitionally a `missing_media` failure). Rather than inferring the kind from the message text after the fact (fragile, breaks if wording changes), each call site is updated to set both the kind and the message together. `run_test()`'s return value becomes a small result object carrying `(passed, fail_kind, message, converged_late, time_to_converge)` instead of a bare `bool`.

Alternative considered: pattern-match the existing free-text `fail_reason` strings into kinds after the fact in `run_all()`. Rejected — it's strictly more fragile (any wording change silently breaks classification) for no savings, since every site already has the kind available for free at the point it's raised.

### Retry multiplier: fixed 2x, one attempt, only for timing-eligible kinds
Confirmed with the user: a fixed multiplier with a single extra attempt, not a step-up sequence. Kept simple and bounded — worst case, a genuinely-broken timing-eligible test costs 2x its original wait before failing, once, not an open-ended backoff. The three existing wait mechanisms (`MAX_DIVERGENCE_TIME`, the structural-consensus `deadline`, `checkpoint_validation_delay`) each get this same "if it fails, try again once at 2x" wrapper applied uniformly rather than being unified into one constant — they legitimately measure different things (live polling vs. one-shot checkpoint assertion vs. structural consensus polling) and forcing them into a single shared timeout would lose that distinction for no benefit.

Alternative considered: step-up sequence (1x → 1.5x → 2x). Rejected for this change per user preference — more data points per retry, but more implementation surface and slower failures, and there's no history data yet to justify the extra granularity. Revisit once `time_to_converge` data exists.

Each retry-eligible site logs a `WARNING` the moment it enters its retry window (e.g. "Structural consensus not yet reached after 15.0s — retrying up to 30.0s before failing"), not just the final pass/fail. This was missed in the first implementation pass — the retry itself worked correctly (verified via a direct `_poll_until` test showing 0.5s-interval polling for the full deadline), but with only a final result logged, a human reading the log had no way to tell "it retried and still failed" apart from "it never retried at all." Silent retries in a tool whose entire purpose is making timing behavior legible would have been a self-defeating gap.

### Correction: the 2x retry must not apply to mid-playback point-in-time checkpoints
The first implementation applied the 2x retry uniformly to all three "wait and see" sites. That was wrong, and it caused a real regression caught during verification of `xstudio_selects`.

Frame checkpoints and recorded-snapshot state checkpoints assert a *point-in-time* expectation against a recording that is still playing. That expectation is only true during the window the recording holds that state — the window `derive_checkpoints` already guarantees via `_FRAME_HOLD_SAFETY_MARGIN`. Retrying past it cannot recover: the playhead has moved on, so the retry either fails regardless or passes by coincidence when the state recurs.

Worse, these polls *block* the validation loop while the recording keeps advancing. Concretely, in `xstudio_selects`: the state checkpoint at t=0.2s missed its first check and entered its (doubled, 20s) blocking poll, holding the main loop from replay-time ~3.2s to ~22.1s. The frame checkpoint at t=9.5s — whose expected frame 69 was valid for a full ~12s window (the recording is silent from t=9.54 to t=21.51) — did not get its first look until t≈23s, by which point the `playing=True` event at t=21.51 had fired and both apps were legitimately free-running. It reported frames 33 and 0 and failed, despite nothing being wrong with it. Doubling the deadline is what pushed that check past the window; at the original 10s it would have landed inside.

So: the 2x retry is scoped to checks with no moving target — the live peer-vs-peer mismatch watch (non-blocking, tracked across loop iterations) and the terminal structural-consensus check (runs after playback stops). The two mid-playback oracle checkpoints are single-window and never extended.

This is a mitigation, not a cure: the underlying hazard is that any blocking validation consumes the recording's timeline. The real fix is to freeze playback for the duration of a check — tracked as the separate `freeze-recording-during-validation` change.

### `converged_late` passes still count as suite-green
A convergence-timing failure that passes on retry is, by definition, a case where the state was eventually correct — the assertion was right, the fixed wait was just too short this one time. Failing the suite for that would defeat the purpose of separating timing flakiness from real breakage: every timing-sensitive test would still redden the suite exactly as often as before, just with better logging. Instead it counts as a pass for exit-code purposes but is recorded distinctly (`converged_late: true`) in history and flagged in the summary, so a test that's *frequently* converging late — a leading indicator of a test about to start failing outright — stays visible without being disruptive on any single run.

### Run-history format: git-tracked JSON Lines, one file
Append-only JSONL keeps writes trivial (one line per run, no read-modify-write, safe under concurrent test runs against different exchanges) and diffable in a PR if anyone wants to eyeball trends. Git-tracked (not gitignored) because this is a single-developer-machine harness today (no CI) — history only accumulates at all if it survives across `git pull`/branch switches, and a gitignored file would silently reset to empty the moment someone clones fresh or the file gets swept up in a clean. Location: `sync_test/run_history.jsonl`, sibling to the existing `sync_tests.yaml`.

Each entry also carries `recording`: the `recording:` path from the test's yaml entry (`null` for a pure `commands:`/`fixtures:` script-driven test with no recording at all). This makes a history entry self-describing — a reader doesn't have to cross-reference `sync_tests.yaml` (which can itself change over time) to know what a given historical run actually replayed.

Alternative considered: SQLite. Rejected as overkill for what will realistically be a few hundred to a few thousand lines from manual local runs; JSONL is trivially greppable and needs no schema migration story.

### `description` is a hard-required field, enforced at config load — for `sync_tests.yaml` only
Backfilling every existing test (proposal.md - What Changes) only stays true over time if new tests can't be added without one. Config loading SHALL reject a test entry missing `description`, per the `ui-sync-testing` delta spec — but only when loading `sync_tests.yaml`. `sync_tests_xstudio.yaml` and `sync_demos.yaml` duplicate a subset of `sync_tests.yaml`'s entries by name/recording for convenience; they are not independently authored, so requiring them to also carry a description would just be copy-paste duplication with no new information, and a second place for the text to drift out of sync. `sync_tests.yaml` is treated as the single source of truth for a test's intent. This is a deliberate strict gate on the canonical file, not a lint warning, because a warning is exactly the kind of thing that accumulates silently (see: the 14 orphaned `logs/` directories this change is already cleaning up).

### Orphaned-log archaeology is a one-time task, not new system behavior
The 14 undocumented `logs/` directories (`verify_fix`, `verify_fix2`, `verify_fix3`, `late_join_repro`, `late_join_repro_xs`, `manual_xs_pen_debug`, `manual_rv_capture_test`, `calibrate_text_scale`, `reorder`, `text_annotations`, `missing_media`, `delete_media`, `verify_pen_replay_noregress`, `verify_text_replay_fix`) are reviewed once, using git log correlation (mtimes cluster tightly around specific commits — see proposal.md), the OpenSpec archive, and existing memory, then either promoted into a described `sync_tests.yaml` entry or the log directory is deleted as confirmed superseded/dead. This is captured entirely in tasks.md; it has no corresponding spec requirement since it's a cleanup action, not an ongoing behavior.

## Risks / Trade-offs

- **[Risk]** A test whose *design* is flaky (not just occasionally slow) will now pass more often (via the 2x retry), potentially masking a real intermittent bug behind "converged late" noise instead of surfacing it as a hard failure. → **Mitigation**: `converged_late` is recorded distinctly, not merged into an ordinary pass; a test converging late repeatedly in the history log is exactly the signal a future `known_flaky` classifier is meant to act on. This change makes that pattern visible instead of invisible (today it's just silently red or silently green with no record either way).
- **[Risk]** `run_test()`'s return-type change is a breaking API change for anything calling it directly. → **Mitigation**: confirmed only `run_all()` and `cli.py` call it today (both updated as part of this change); no other consumers exist in-repo.
- **[Risk]** Doubling the wait on every timing-eligible failure adds real wall-clock time to a run that's already going to fail (a genuinely broken test now takes ~2x as long to report red). → **Mitigation**: accepted cost — bounded to exactly one retry, and only for the three kinds where waiting longer is a meaningful question at all; missing-media/log-signature/otio-export/annotation-missing fail immediately with no added delay.
- **[Risk]** Requiring `description` at config-load time could block someone from quickly hacking together a throwaway test entry the way the 14 orphaned ones were created. → **Mitigation**: that ease-of-creation is exactly what produced 14 undocumented, unrecoverable-except-by-archaeology test runs; a one-line description costs seconds and is the entire point of this change.

## Migration Plan

1. Land the `fail_kind`/retry/history-log changes to `runner.py` and `cli.py` first (no YAML schema changes yet) — verify existing tests still pass unchanged.
2. Add `description` (and `status`/`blocked_by` where applicable) to every entry in `sync_tests.yaml`, then flip on the required-field validation for that file. `sync_tests_xstudio.yaml`/`sync_demos.yaml` are left as-is.
3. Run the orphaned-`logs/` archaeology pass, promoting or deleting each of the 14 directories.
4. No rollback complexity: this is additive to a local dev tool with no external consumers; reverting the commit fully reverts behavior, and `run_history.jsonl` simply stops growing (existing entries are harmless to leave in place).
