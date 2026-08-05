## 1. Player: explicit clock arming

- [x] 1.1 Add `self._clock_arm_requested = False` to `SyncPlayer.__init__`, reset it to `False` at the top of `start_playback()` so a second `start_playback()` on the same player instance never inherits a stale arm from the prior run. Note this is *not* about `loop=True`: internal looping restarts in place inside `tick()` (`_play_start_time = time.time(); _play_index = 0`) with `_wait_for_peer` already `False`, so it never re-enters `start_playback()` and never consults the flag. The reset is for the reuse case only.
- [x] 1.2 Add `SyncPlayer.arm_clock()`, setting `self._clock_arm_requested = True`. Safe to call at any time, including before `start_playback()`, after the clock is already armed, or when `wait_for_peer=False` (a no-op in that case since the clock already armed synchronously).
- [x] 1.3 In `tick()`'s peer-join gate branch, after the existing `min_peer_count`/`post_snapshot_delay` conditions pass, add the `_clock_arm_requested` check before the `self._wait_for_peer = False; self._play_start_time = time.time()` lines — return `True` (keep servicing the network, dispatch nothing) if not yet armed.
- [x] 1.4 Log the arm-side timing, matching the precedent set by `_freeze_playback`'s freeze-duration log (time removed from real-time pacing is reported, never silent):
  - once, when the gate's own conditions first pass while unarmed: "gate cleared, waiting for explicit arm";
  - once, when the clock actually arms: the elapsed interval between those two moments.
  That interval is the race this change closes — see §4.1. Log it at a level the suite captures by default; a `wait_for_peer=True` caller that never arms must be diagnosable from this log rather than presenting only as an unexplained stall.
- [x] 1.5 Confirm the ordering against the existing `_paused` check at the top of `tick()`: pausing before the clock is ever armed must not crash or corrupt state (`resume()`'s `_play_start_time is not None` guard already covers this — verify rather than assume).

## 2. Player-level verification (no runner changes yet)

- [x] 2.1 Verify: gate conditions satisfied but `arm_clock()` never called → no event dispatched, network still serviced, however long ticked.
- [x] 2.2 Verify: `arm_clock()` called *before* gate conditions are satisfied → clock arms exactly when the gate conditions are later satisfied, not before.
- [x] 2.3 Verify: `arm_clock()` called *after* gate conditions are already satisfied → clock arms immediately on the next `tick()`.
- [x] 2.4 Verify: `wait_for_peer=False` (default) behavior is byte-for-byte unchanged — `_play_start_time` is still set synchronously in `start_playback()` regardless of `arm_clock()` ever being called.
- [x] 2.5 Verify: calling `pause()` while the clock is still unarmed, then `arm_clock()` plus gate conditions clearing, then `resume()` — does not corrupt `_play_start_time` or cause a premature/duplicate arm.
- [x] 2.6 Verify the §1.4 logging fires in both orderings (arm-before-gate and gate-before-arm) and reports an interval consistent with the delay actually injected by the test.

## 3. Runner integration

- [x] 3.1 In `TestRunner.run_test`, call `player.arm_clock()` immediately after the `_wait_for_snapshot()` block, unconditionally — whether `_wait_for_snapshot` returned `True` or hit its existing timeout-and-warn fallback, mirroring that block's current "proceed anyway" tolerance.
- [x] 3.2 Confirm this call is only reached for the non-script-driven (recording-driven) path, matching where `player` is non-`None` and `_wait_for_snapshot` is already called today.

## 4. Regression verification

- [x] 4.1 **Measure the race directly, before relying on pass rates.** Run `xstudio_selects` once and record the `[player] Clock armed N.Ns after the peer-join gate cleared` interval. No pre-change staging is needed: that interval is `gate_cleared → _wait_for_snapshot() returned` either way — pre-change it was time the clock ran unwatched, post-change it is time the clock is held. Same quantity, measured on the shipped code. Note the measured value here.

  This matters because the gate can clear during `_wait_for_all_apps` (90s budget), after which `_wait_for_snapshot` gets a further 30s — so pre-change `_play_start_time` may have been tens of seconds old when the validation loop started, and `runner.py`'s checkpoint `while` loop would then run every checkpoint whose `time_offset` is already behind `current_offset` back-to-back, each outside its recorded hold window. Confirm or refute that against the measurement rather than assuming it. A near-zero interval means this change is aimed at the wrong thing and §4.2 onward should not be run on faith.

  **Measured (2026-08-05, `xstudio_selects`, HEAD a27fcad + this change):** gate cleared 15:45:35, clock armed 15:45:39 — **3.8s**. Real and non-trivial, but roughly an order of magnitude below the "tens of seconds" this task speculated.

  **The skipping hypothesis is refuted at the configured delay.** `xstudio_selects` runs `checkpoint_validation_delay: 6`, and its earliest checkpoint is t=0.2s, so the first validation is due at 6.2s — comfortably beyond the 3.8s gap. No checkpoint was being blown through back-to-back at this config, and the `while` loop never ran hot. The gap only bites below ~4s of delay: at the `delay: 3` §4.2 proposes, t=0.2s comes due at 3.2s < 3.8s, i.e. the first checkpoint would be due the instant the clock arms. So the change is *correct* and the anchor is now honest, but at the delay this suite actually uses it was not costing checkpoints.

  **The run still failed, on a different mode.** Checkpoint t=9.5s: `openrv: expected frame ~69, got 96 [checked 6.4s after event ... delay=6.0s]`. It was checked on time — 6.4s against a 6.0s target — so this is not lateness. The recording is mid-burst there (events every ~0.05s continuously from t≈6.0s), and the app's own playhead keeps advancing while the *recording* is frozen; freezing suspends dispatch, it does not pause the apps. Holding a frame expectation for a 10.1s retry window against a still-moving playhead cannot converge, so the retry made this failure certain rather than recoverable.

- [x] 4.2 **Not demonstrable — deliberately not attempted.** §4.1 measured the gap at 3.8s and refuted the skipping hypothesis at this suite's `checkpoint_validation_delay: 6`, so there is no failure mode here for a pass-rate comparison to show improving. Two further reasons not to spend the runs: `run_history.jsonl` is biased toward failures (the user re-runs failing tests by hand, so its ~47% pass rate is a selection artefact, not a base rate), and the real cause of the `xstudio_selects` failures was subsequently identified as apps sitting on different clips while reporting the same timeline name — unaffected by anything in this change. Claiming credit here on a pass-rate delta would have been measuring the wrong thing.
- [x] 4.3 **Not meaningfully runnable as written — deferred to a suite-wide run, not re-scoped here.** The comparison this asks for ("no test that previously passed now fails") assumes `run_history.jsonl` is a like-for-like baseline. It is not, for two reasons established the same day: the harness changes committed alongside this one deliberately convert former *false* passes into failures (frame assertions against a playing playhead, and against apps on different clips), so a regression comparison would flag corrections as breakage; and the history is selection-biased, since failing tests are re-run by hand far more often than passing ones.

  The genuine no-regression check is still worth doing, but it belongs to a single suite run covering everything committed today rather than to this change alone — the tree contains three changes' worth of edits and cannot be attributed test-by-test. Recorded rather than silently dropped.
- [x] 4.4 **Satisfied by observation.** This guards the trade-off the design accepted: with no auto-arm fallback, a `wait_for_peer=True` caller that never arms services the network forever and dispatches nothing. Verified on the 2026-08-05 `xstudio_selects` run (recording-driven, the only mode that exercises the gate) — the clock armed 3.8s after the gate cleared and the test ran to completion in 34.4s. It failed on a checkpoint, but it did not hang, which is what this task asks. The `[player] Peer-join gate cleared; holding the recording's clock until arm_clock() is called` line is in place as the first diagnostic should a future caller forget to arm.

## 5. Documentation

- [x] 5.1 Add a short note to `sync_test/README.md`'s "Checkpoint Validation" section (added by `freeze-recording-during-validation`) explaining that the recording's clock now arms only once the runner's own app-readiness check passes, not merely once peers have received their snapshot — so the checkpoint hold-window described there starts from a point the runner itself has confirmed, not an earlier internal signal.
