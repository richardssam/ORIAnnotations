## Context

See `proposal.md` — Why, for the two traced failures that motivate this. Relevant current-state facts:

- `SyncPlayer` drives everything from a single wall-clock anchor: `current_offset = (now - self._play_start_time) * self._play_speed` (`player.py::tick`). There is no pause concept; `_playing` is a hard stop that also closes the network.
- `TestRunner.run_test` computes its own `current_offset = time.time() - player._play_start_time` to decide when a checkpoint is due. It reads the player's anchor directly rather than asking the player, so any correct manipulation of that anchor is automatically reflected on both sides.
- Checkpoint validation currently blocks the runner's main loop: the structural-checkpoint poll runs up to 10s, and the frame checkpoint samples inline. The recording keeps advancing throughout.
- `derive_checkpoints` / `derive_state_checkpoints` already filter for recorded silence (`_FRAME_HOLD_SAFETY_MARGIN`, `_SCP_SILENCE_MARGIN`) — these exist *solely* to buy enough window to survive the race this change removes.
- `sync-tests-tracking` had to forbid retrying point-in-time checkpoints because their target moved. That restriction is a symptom of this problem, not an independent design choice.

## Goals / Non-Goals

**Goals:**
- A checkpoint's expected value cannot go stale while that checkpoint is being evaluated.
- Validation outcomes become independent of machine speed and inspector RPC latency.
- Restore retry-eligibility for point-in-time checkpoints, since freezing makes "wait longer" meaningful again.

**Non-Goals:**
- Retuning or removing the recorded-silence filters (see proposal — Out of Scope). Freezing makes them largely redundant, but they still guard against sampling mid-burst, and narrowing them is separately verifiable work.
- Changing what any checkpoint asserts. This change alters *when* assertions are evaluated, never their content.
- Freezing anything for script-driven tests — they have no recording playing during assertions.

## Decisions

### Pause by shifting the clock anchor, not by tracking a separate paused-offset
`resume()` advances `_play_start_time` by the elapsed pause duration. `current_offset` is a pure function of that anchor, so both the player's dispatch loop and the runner's independent due-checkpoint computation stay consistent with no further changes. The alternative — accumulating a `_paused_total` and subtracting it in every offset computation — requires finding and updating every offset call site including the runner's, and silently breaks if one is missed. Shifting the anchor keeps a single source of truth.

### `tick()` still services the network while paused
Peer-join and `STATE_REQUEST` handling (`_process_network_requests`) must keep running: a peer that joins mid-freeze would otherwise hang waiting for a snapshot. Only event *dispatch* is suspended. This mirrors the existing `wait_for_peer` gate, which already services the network while dispatching nothing — so the pattern is established, not new.

### Freeze at the validation site, with guaranteed resume
Each validation block is wrapped so resume happens on every exit path, including exceptions and early `break`s. A leaked freeze would hang the whole test (the recording would never finish, and the runner loops until playback ends), so this is the one failure mode worth defending against structurally rather than by discipline — a context manager, not paired calls.

### Freezing applies to both checkpoint types, not just the slow one
It is tempting to freeze only the structural checkpoint (the one that blocks for 10s). But the frame checkpoint also samples app state over a non-zero interval, and the whole point is to stop reasoning about "how long is too long." Freezing both makes the invariant unconditional and therefore actually trustworthy.

### Retry restoration is deliberately staged after freezing is proven
The `sync-tests-tracking` restriction (no retries on point-in-time checkpoints) stays in place until freezing is verified working. Re-enabling retries is a one-line scoping change afterwards; doing both at once would make it impossible to attribute a behaviour change to the right cause.

## Risks / Trade-offs

- **[Risk]** Replay is no longer strictly 1:1 with real time — total runtime grows by the sum of freeze durations, and any app-side behaviour that depends on continuous event flow (timeouts, debounce windows, poll intervals in the plugins) sees a gap it would not see in a live session. → **Mitigation**: checkpoints are already chosen at recorded-quiet moments, so the gap lands where the session was idle anyway. Freeze durations are logged, so distortion is measurable rather than invisible. If a plugin timeout proves sensitive, that is itself worth knowing — but validate against the full suite before trusting this broadly.
- **[Risk]** Freezing the *player* does not freeze an app in local play mode: after a `playing=True` event an app free-runs on its own clock, and suspending the event stream will not stop its playhead. → **Mitigation**: frame comparisons are already gated on `frame_held`, which is derived from recorded playback silence, so frame assertions do not fire while an app is expected to be free-running. Worth an explicit assertion during implementation rather than assumed.
- **[Risk]** A leaked freeze hangs the test run entirely. → **Mitigation**: context-manager-scoped resume (see Decisions); plus the existing drain/`playing` loop bounds mean a hang is loud and immediate rather than subtle.
- **[Risk]** Pausing changes the timestamps the player rewrites via `_update_timestamps`, potentially making replayed events carry wall-clock times inconsistent with their logical offsets. → **Mitigation**: verify explicitly during implementation; if it matters, rewrite against the logical offset rather than raw wall clock.

## Migration Plan

1. Add `pause()`/`resume()` to `SyncPlayer` with unit-level verification (offset frozen across a pause, event ordering and spacing preserved, network still serviced) — no runner changes yet.
2. Wrap runner checkpoint validation in freeze/resume via a context manager. Verify `xstudio_selects` at `checkpoint_validation_delay: 3` — the setting that currently fails — now passes *with* its frame assertion active (`frame_held=True`), which is the real regression test for this change.
3. Run the full suite and compare against `run_history.jsonl` for tests that were previously flaky; confirm no new failures and ideally reduced variance.
4. Only then, re-enable retries for point-in-time checkpoints (revert the `sync-tests-tracking` scoping restriction).
5. Rollback is clean: the freeze wrapper is additive, and reverting it restores current behaviour exactly. `pause()`/`resume()` are unused-but-harmless if left in place.
