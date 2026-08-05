## Why

The sync test runner validates checkpoints against a recording that is **still playing**. Every checkpoint asserts a point-in-time expectation (a frame, a structural snapshot), but the assertion is evaluated some unbounded time later — `checkpoint_validation_delay` after the event, plus however long the inspector RPCs and polling loops take. By then the recording has moved the apps somewhere else, and the expectation is stale through no fault of the apps.

This is not theoretical. Two distinct failures were traced to it while verifying `sync-tests-tracking`, both in `xstudio_selects`:

1. **Cascading staleness.** A blocking structural-checkpoint poll held the validation loop from replay-time ~3.2s to ~22.1s. The frame checkpoint at t=9.5s — expecting frame 69, valid for a full ~12s window (the recording is silent from t=9.54 to t=21.51) — did not get its first look until t≈23s. By then the `playing=True` event at t=21.51 had fired and both apps were legitimately free-running, reporting frames 33 and 0. The checkpoint failed despite nothing being wrong.

2. **A validity guarantee computed against the wrong duration.** `derive_state_checkpoints` marks a snapshot `frame_held` when the recording holds the playhead for `validation_delay + 1.5s`. For the t=0.2s snapshot that gap is 5.09s. But validation starts at `t + delay` and can then poll for up to 10s — a ~13s window judged against a 5.09s guarantee. At `checkpoint_validation_delay: 3` the frame is compared and fails (apps at frame 68, snapshot expects 0); at `4` the same checkpoint falls below the `frame_held` threshold and the frame assertion is silently **switched off**, so the test passes. The setting that "works" is the one that asserts less.

Both collapse to one root cause: **validation races an advancing recording.** Every mitigation so far (retry windows, tolerance margins, tuning `checkpoint_validation_delay`) is compensating for that race rather than removing it, and each one either weakens an assertion or makes results depend on machine speed and RPC latency.

## What Changes

- Add `pause()` / `resume()` to `sync_recorder.player.SyncPlayer`. The player's clock is `current_offset = (now - _play_start_time) * speed`, so pausing records the pause instant and resuming shifts `_play_start_time` forward by the elapsed pause duration. `tick()` continues to service network requests while paused (joiners must still be answered) but dispatches no recorded events.
- The runner freezes playback for the duration of every checkpoint validation — frame checkpoints and structural state checkpoints alike — and resumes once the check reaches a verdict. The recording's logical timeline therefore never advances while an assertion is being evaluated.
- Because the target no longer moves while being checked, retrying a failed point-in-time checkpoint becomes meaningful again: the runner MAY poll a frozen checkpoint until a bounded deadline without the expectation going stale. (`sync-tests-tracking` had to forbid retrying these checks precisely because the target moved.)
- `frame_held` / `_FRAME_HOLD_SAFETY_MARGIN` and the `validation_delay`-based silence filters can be reconsidered once freezing lands: their entire purpose is to guarantee a window wide enough to survive a race that no longer exists. **Not removed in this change** — narrowing them is a follow-on once freezing is proven, since they currently also protect against genuine mid-burst sampling.
- Log every freeze/resume with its duration, so a slow check is visible as "playback frozen 4.2s" rather than silently distorting replay pacing.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `sync-test-state-validation`: checkpoint validation SHALL occur with recording playback frozen, so a checkpoint's expected value cannot go stale while it is being evaluated; consequently point-in-time checkpoints become retry-eligible again.
- `sync-recorder-state-capture`: the player SHALL support pausing and resuming procedural playback, preserving its logical event timeline across the pause and continuing to service peer state requests while paused.

## Out of Scope

- Removing or narrowing `_FRAME_HOLD_SAFETY_MARGIN`, `_SCP_SILENCE_MARGIN`, or the `validation_delay` silence filters in `derive_checkpoints` / `derive_state_checkpoints`. Freezing makes them largely redundant, but retuning them is a separate, independently-verifiable change.
- `frame_tolerance` (still deferred; see `sync-tests-tracking` — Out of Scope).
- Script-driven tests, which have no recording playing during their assertions and are therefore unaffected.

## Impact

- `sync_recorder/player.py`: new `pause()`/`resume()`, paused-state handling in `tick()`. `_play_start_time` remains the single source of truth for the logical clock, so the runner's independent `current_offset` computation stays consistent for free.
- `sync_test/python/sync_test/runner.py`: wrap the frame-checkpoint and state-checkpoint validation blocks in freeze/resume; revisit the retry scoping that `sync-tests-tracking` deliberately restricted.
- **Behavioral change to replay semantics for every recording-driven test.** Wall-clock replay is no longer strictly 1:1 with the original session — total runtime grows by the sum of freeze durations. Checkpoints are already chosen at quiet moments, so event *interleaving* is minimally affected, but this is the main risk to validate (see design.md).
- No production sync-library changes; confined to the test harness and the recorder's player.
