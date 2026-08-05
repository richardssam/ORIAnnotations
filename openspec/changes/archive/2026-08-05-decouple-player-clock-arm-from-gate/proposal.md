## Why

Even with recording playback frozen during checkpoint validation (`freeze-recording-during-validation`), `xstudio_selects` still intermittently fails: `SyncPlayer`'s peer-join gate arms the recording's logical clock (`_play_start_time`) as soon as *it* observes a peer has been snapshotted over RabbitMQ, which can happen before `TestRunner._wait_for_snapshot()` — a separate, HTTP-based poll of each app's `/state` endpoint — confirms the apps are actually ready to be validated against. Freezing protects a checkpoint's hold window *during* evaluation; it cannot protect a window that already started elapsing before evaluation was possible.

**Measured, 2026-08-05: the gap is 3.8s** (gate cleared 15:45:35, runner ready 15:45:39, on `xstudio_selects`). Real, but roughly an order of magnitude below the "tens of seconds" this proposal originally asserted, and the assertion that whole checkpoints were being skipped is **refuted at the delay this suite uses**: `xstudio_selects` runs `checkpoint_validation_delay: 6` and its earliest checkpoint is t=0.2s, so the first validation is due at 6.2s — comfortably beyond 3.8s. Nothing was being blown through back-to-back.

The change is still correct: anchoring the recording's t=0 to the runner's own readiness signal, rather than to an earlier RabbitMQ-only one, is the honest thing for the clock to mean, and it removes a silent dependency on which of two unrelated signals happens to win. But it should not be credited with fixing `xstudio_selects`. The gap only begins to cost checkpoints below ~4s of validation delay; at `delay: 3` the t=0.2s checkpoint would come due the instant the clock arms.

The actual cause of the `xstudio_selects` failures was found later the same day and lies elsewhere entirely — the apps were on different clips while both reported the same timeline name, so the frame comparisons were meaningless (see `host-owned-visibility`). This change is a correctness improvement to the harness's timing model, not a fix for that.

## What Changes

- `SyncPlayer.start_playback(..., wait_for_peer=True)` currently auto-arms `_play_start_time` internally the instant its own peer-join gate clears (snapshot delivered to `min_peer_count` peers + `post_snapshot_delay` elapsed). Split "gate cleared" from "clock armed": the gate keeps tracking peer-snapshot delivery exactly as today, but starting the logical clock becomes an explicit, separate action.
- Add an explicit arming call (e.g. `SyncPlayer.arm_clock()`) that sets `_play_start_time = time.time()`. While `wait_for_peer` is set and the clock has not yet been armed, `tick()` continues to service the network (as it does today) but dispatches no recorded events — mirroring the freeze semantics already established for `pause()`.
- `TestRunner.run_test` calls `arm_clock()` only after its own `_wait_for_snapshot()` confirms every app is genuinely ready, so the recording's t=0 anchor is defined by the same readiness signal the runner itself validates against, rather than an earlier, RabbitMQ-only one.
- Preserve existing behavior for callers that don't need this finer control (the CLI's blocking `player.play()`, and any `wait_for_peer=False` use). Settled in design.md: no auto-arm fallback. The explicit call is scoped to `wait_for_peer=True`, whose only caller today is `runner.py` and is updated here; `wait_for_peer=False` still arms synchronously in `start_playback()`, unchanged.
- Log both ends of the gate-to-arm interval. It is the quantity this change removes and therefore the only direct measurement of the race — and without it, the stall this design accepts as a trade-off (a `wait_for_peer=True` caller that never arms) is indistinguishable from a hung app.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `sync-recorder-state-capture`: the player's peer-join gate SHALL NOT itself start the logical playback clock; clock-arming SHALL be an explicit action available to the caller, so the recording's timeline can be anchored to a caller-defined readiness signal instead of the gate's own.

## Impact

- `sync_recorder/player.py`: `start_playback`, the gate-clearing branch inside `tick()`, new `arm_clock()` (or equivalent) method.
- `sync_test/python/sync_test/runner.py`: call the new arming method after `_wait_for_snapshot()` returns, instead of relying on the gate to arm the clock implicitly.
- Builds on the same `SyncPlayer` file `freeze-recording-during-validation` touches (pause/resume) but is an independent, orthogonal mechanism — does not require that change to be archived first, though the two should be reviewed together for interaction effects (e.g. pausing before the clock has ever been armed).
- No production sync-library changes; confined to the test harness and the recorder's player.
