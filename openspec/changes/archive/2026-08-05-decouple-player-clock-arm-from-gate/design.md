## Context

See proposal.md - Why, for the race this closes. Relevant current-state facts:

- `SyncPlayer.tick()`'s peer-join gate (`sync_recorder/player.py`) currently clears and arms the clock in the same step: once `len(self._peers_snapshotted) >= self._min_peer_count` and `post_snapshot_delay` has elapsed (or early peer activity is detected), it sets `self._wait_for_peer = False` and `self._play_start_time = time.time()` back to back, with nothing in between.
- `TestRunner.run_test` (`sync_test/runner.py`) calls `player.start_playback(wait_for_peer=True, ...)` *before* spawning any app (so the player claims master first), then separately calls `self._wait_for_snapshot(app_ports, timeout=30.0)`, which polls each app's HTTP `/state` endpoint until it reports a non-null clip. These two readiness checks run on different transports (RabbitMQ vs HTTP) against different app-side signals (message received vs. state actually applied and queryable), and nothing keeps them in step.
- `grep` confirms exactly one call site passes `wait_for_peer=True`: `sync_test/runner.py`. Every other caller (`tests/otio_sync/test_sync_recorder.py`, the CLI's blocking `player.play()`) either omits `wait_for_peer` or doesn't use `start_playback` at all, so they're unaffected by changing that gate's semantics.
- `pause()`/`resume()` (from `freeze-recording-during-validation`) already tolerate `_play_start_time is None` gracefully — `resume()`'s anchor shift only applies `if self._play_start_time is not None`.
- **The player is ticked on its own thread.** `runner.py` starts a daemon `player_thread` that calls `player.tick()` in a loop; `arm_clock()` will be called from the runner's main thread, exactly as `pause()`/`resume()` already are. So the new flag is written on one thread and read on another. A lone `bool` set/read is fine here for the same reason `_paused` is — a single unsynchronised store, no read-modify-write, no invariant spanning two fields — but this is a property of how simple the flag is, not a general licence. Anything later added to `arm_clock()` or its `tick()` check that reads *and* writes state, or that must stay consistent with `_play_start_time`, needs a lock; it cannot be bolted on in the same style.

## Goals / Non-Goals

**Goals:**
- The recording's logical clock cannot start before the runner's own readiness check (`_wait_for_snapshot`) has passed, closing the gap that lets a checkpoint's hold-window start elapsing before the runner is even watching.
- No change to peer-join gate semantics themselves (snapshot delivery, `min_peer_count`, `post_snapshot_delay`) — only to what happens once those conditions are met.
- No behavior change for the one other `start_playback` mode (`wait_for_peer=False`, the default) or for any existing caller that doesn't pass `wait_for_peer=True`.

**Non-Goals:**
- Replacing or removing `_wait_for_snapshot`'s HTTP polling, or the peer-join gate's RabbitMQ-side signal. Both readiness checks stay; this change only sequences them correctly instead of letting one race ahead of the other.
- Any change to `pause()`/`resume()` from the sibling change — this is a separate, earlier point in the same clock's lifecycle (before first arming vs. after).
- Retuning `_FRAME_HOLD_SAFETY_MARGIN` or other checkpoint-derivation margins (see `freeze-recording-during-validation` — Out of Scope; the same reasoning applies here).

## Decisions

### Require both conditions, not replace one with the other
`tick()`'s gate-clearing branch currently arms the clock as soon as its own conditions (snapshot delivered + `post_snapshot_delay`) are satisfied. The fix ANDs a new condition onto that: the clock arms only once the gate's own conditions **and** an explicit caller-side `arm_clock()` call have both happened, whichever comes last. This is deliberately not a *replacement* of the RabbitMQ-side gate with the HTTP-side one — bypassing the peer-join gate on `arm_clock()` alone would let the clock start before peers have even received their initial snapshot, an even worse race. Taking the later of the two signals is the maximally safe behavior regardless of which one lags in practice.

### `arm_clock()` as a new explicit method, gated by a flag
Add `SyncPlayer.arm_clock()`, which just sets `self._clock_arm_requested = True`. `start_playback()` resets that flag to `False` on every call, so reusing a player instance for a second run never inherits a stale arm. (This is not about `loop=True` — internal looping restarts in place inside `tick()` with `_wait_for_peer` already `False`, so it never re-enters `start_playback()` and never consults the flag.) `tick()`'s gate branch checks the flag after its existing conditions:
```
if not self._clock_arm_requested:
    return True
```
before the existing `self._wait_for_peer = False; self._play_start_time = time.time()` lines. No other line in that branch changes.

Alternative considered: have `arm_clock()` set `_play_start_time` directly and let the gate branch skip its own arming if already armed. Rejected — it duplicates the arming logic in two places (the gate branch and `arm_clock()`) and reintroduces exactly the "which one actually started the clock" ambiguity this change exists to remove. It would also mean two threads writing `_play_start_time` (see Context), turning a benign unsynchronised `bool` into shared mutable timing state. The flag approach adds no new writer: `_play_start_time` keeps exactly the writers it has today (`start_playback`'s synchronous non-gated arm, the gate branch, the loop restart, and `resume`'s anchor shift), all on the player's own thread.

### The gate-to-arm interval is logged, not just closed
The change's whole effect is to move the clock's t=0 later by some interval. That interval is also the only direct measurement of the race being fixed, so the player logs both ends of it: once when the gate's conditions first pass while unarmed, and again with the elapsed interval when the clock actually arms. This follows the precedent `_freeze_playback` already set in `runner.py` — time removed from real-time replay pacing is reported rather than silent.

Two things depend on this. First, verification: a ~1-in-4 flake needs many repeated runs before a pass rate says anything, whereas one instrumented run says whether the gap was 0.2s or 25s (see Migration Plan step 1). Second, diagnosis: the failure mode this design deliberately accepts is a caller that never arms and therefore stalls forever. Without a log line that stall is indistinguishable from a hung app; with one it names itself.

### `wait_for_peer=True` now requires an explicit `arm_clock()` call — no auto-arm fallback
Because exactly one call site passes `wait_for_peer=True` (`sync_test/runner.py`, confirmed by repo-wide grep), there's no ambiguous third-party caller to preserve a fallback for. `wait_for_peer=True` callers are now expected to call `arm_clock()`; a caller that never does simply never dispatches (network still serviced), which is the same "explicit is better than an implicit timeout" trade the sibling `pause()`/`resume()` change already made. `wait_for_peer=False` (the default, and every other current caller) is completely unaffected — `start_playback` still sets `_play_start_time = time.time()` synchronously in that mode, exactly as today.

### Runner calls `arm_clock()` unconditionally after `_wait_for_snapshot()`, timeout or not
`_wait_for_snapshot` already has a "proceed anyway" fallback on timeout (`logging.warning(...)`, no exception). `arm_clock()` is called right after that block regardless of outcome, mirroring the existing tolerance — a test that would have proceeded (degraded) before still proceeds the same way now; it just does so through the same explicit arming path as the success case, rather than a different implicit one.

## Risks / Trade-offs

- **[Risk]** A `wait_for_peer=True` caller that forgets to call `arm_clock()` hangs forever (network serviced, nothing ever dispatched) instead of eventually auto-arming. → **Mitigation**: exactly one caller exists today and is updated as part of this same change; the specs' "no arm, no dispatch" scenario makes this an explicit, testable contract rather than a silent trap. A future caller adding `wait_for_peer=True` without reading the docstring would hit an obvious, loud symptom (test hangs until its own outer timeout) rather than a subtle timing bug — arguably a better failure mode than today's silent race.
- **[Risk]** `pause()` called before the clock has ever been armed (`_play_start_time is None`). → **Mitigation**: already handled — `resume()`'s anchor shift is a no-op when `_play_start_time is None` (existing code from the sibling change), and this change doesn't touch that path. Worth an explicit unit test rather than assumed.
- **[Risk]** This is the second change in a row touching `SyncPlayer`'s timing internals; stacking changes increases the chance of an interaction bug between `arm_clock()`'s flag and `pause()`'s paused-state check in `tick()`. → **Mitigation**: the two checks are independent and ordered (paused-check returns early before the gate branch is ever reached, unchanged from the sibling change), so arming and pausing cannot both be "in progress" in a way that races each other within a single `tick()` call.

## Migration Plan

1. Add the gate-to-arm instrumentation first, and take one measurement on the *pre-change* code — the interval between the gate's conditions passing and `_wait_for_snapshot()` returning. Everything below assumes that gap is real and material; if it measures near zero, this change is aimed at the wrong thing and the remaining steps should not be taken on faith.
2. Add `_clock_arm_requested` flag and `arm_clock()` to `SyncPlayer`; reset the flag in `start_playback()`; gate the clock-arming lines in `tick()` on it. Unit-verify: gate-cleared-but-unarmed dispatches nothing, `arm_clock()` before vs. after gate conditions both end up armed correctly, network still serviced while unarmed, pausing before arming is safe.
3. Update `sync_test/runner.py` to call `player.arm_clock()` immediately after the `_wait_for_snapshot()` block (success or the existing timeout fallback).
4. Re-run the `xstudio_selects` regression case (`checkpoint_validation_delay: 3`, the same primary regression test `freeze-recording-during-validation` used) and confirm the "checked too late relative to the recording's burst" failure mode stops recurring. Draw the pre-change baseline from `sync_test/run_history.jsonl`, which holds per-test outcomes and `time_to_converge` across prior runs — not from recollection of the sibling change's session, which is not re-checkable. Repeated runs corroborate step 1's measurement; they are not a substitute for it.
5. Run the full suite and compare against `run_history.jsonl`, same bar as the sibling change: no test that previously passed now fails.
6. Rollback is clean: `arm_clock()` is additive and unused by any other caller; reverting `runner.py`'s one new call restores today's implicit-arm behavior exactly.
