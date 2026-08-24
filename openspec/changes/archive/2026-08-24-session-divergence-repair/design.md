## Context

Three existing pieces this design composes rather than replaces:

- **Post-join confirmation** (`playback_sync.confirm_join_state` → `manager.confirm_join`) already produces a `CONFIRMED` / `MISMATCHED` / `NOT_CONFIRMED` outcome and stores it in `manager.join_confirmation`. It is report-only today.
- **Live timeline mismatch detection** (`apply_playback_state`, `playback_sync.py:2510-2532`) already computes `_tl_mismatch` per incoming message, but the value is local to that call — nothing accumulates it, and the only effect is dropping that message's seek.
- **Structure divergence recovery** already has the full repair shape this needs: a diverged flag, a settle deadline, a cooldown-gated call to `manager.request_state(reason="recovery")` from `manager.tick()` (`manager.py:3989-4009`, `RECOVERY_COOLDOWN = 5.0` at `manager.py:157`). Recovery requests are answered identically to a join's `STATE_REQUEST` — same snapshot, same delta buffering/replay.

## Goals / Non-Goals

**Goals:**
- Route both signals (join `MISMATCHED`, sustained live `_tl_mismatch`) through the same `request_state(reason="recovery")` call structure-divergence-recovery already uses.
- Keep `NOT_CONFIRMED` strictly inert — never a trigger, never conflated with `MISMATCHED`.
- Require *sustained* live mismatch, not a single message, before triggering.
- Pace repeated recovery attempts so a repair that doesn't stick can't loop.

**Non-Goals:**
- Not touching `structure_diverged` / structural recovery's own flag, deadline, or cooldown state — view-divergence recovery gets its own parallel state, not a shared one, since the two are triggered by unrelated conditions and must not block or starve each other.
- Not building one unified "any divergence" detector — join confirmation and live `_tl_mismatch` remain two distinct signal sources feeding the same repair action.
- RV (`rvplugin/ori_sync/`) is unchanged — the reported bugs are xStudio-specific.

## Decisions

**1. Two signal sources, one repair action, xStudio-side only.**

Both `confirm_join_state` (on a `MISMATCHED` result) and the extended `_tl_mismatch` handling in `apply_playback_state` (on a sustained mismatch) call the same small trigger — a plugin- or manager-level method that gates on its own cooldown and, if clear, calls `manager.request_state(reason="recovery")`. One trigger point keeps the pacing and the "which signal fired" logging in one place instead of duplicated in two call sites.

**2. View-divergence recovery gets its own cooldown state, reusing the existing `RECOVERY_COOLDOWN` value.**

Track a separate `_last_view_recovery_attempt` timestamp (parallel to `_last_recovery_attempt`), paced by the same `RECOVERY_COOLDOWN = 5.0` constant, rather than sharing structural recovery's timestamp. A structural rebuild that also leaves the view wrong is a real case where both should be able to fire without one blocking the other.

*Alternative considered:* reuse `_last_recovery_attempt`/`structure_diverged` directly. Rejected — the two failure modes are independent (a session can have correct structure and a wrong view, or vice versa), and coupling their pacing would mean a recent structural recovery attempt silently suppresses a needed view recovery, or vice versa.

**3. Sustained live mismatch = multiple consecutive `_tl_mismatch` detections, reset on a clean match.**

A counter increments on each `_tl_mismatch` and resets to zero the moment a message resolves cleanly against the active timeline. The trigger fires once the counter crosses a small threshold. The existing comment at `playback_sync.py:2516-2518` already documents *why* a single mismatch is not trustworthy (the local guid can be mid-transition during a view switch) — the counter is the direct fix for that: a transitional mismatch self-clears within a message or two, a real divergence does not.

**4. `NOT_CONFIRMED` is excluded by construction, not by a secondary check.**

The join-side trigger is placed where `confirm_join`'s return value is a plain string equality check against `MISMATCHED` — not "anything other than `CONFIRMED`" — so `NOT_CONFIRMED` cannot reach the repair call through a refactor that forgets to exclude it explicitly.

## Risks / Trade-offs

- **Threshold too low → repair fires on transient view-switch artifacts, causing visible resync churn.** → Mitigation: start conservative, tune from logs the same way `DEFAULT_FRAME_TOLERANCE` was — empirically, from what real sessions show, not guessed up front (open question below).
- **Two independent recovery paths (structural, view) firing close together could both call `request_state`.** → Mitigation: `request_state` already sets `STATE_JOINING` and the buffering/replay path already handles a request landing while one may still be outstanding for the structural case; a task verifies this holds when the second caller is the view-recovery path instead of a retry of the same one.
- **Narrowing a requirement written two days ago for a specific, twice-paid-for reason.** → Mitigation: the spec and implementation both gate on the `MISMATCHED` string constant specifically, and a test asserts `NOT_CONFIRMED` never reaches the repair trigger, so the distinction the earlier design protected stays enforced in code, not just in the spec text.

## Migration Plan

Additive; no persisted-state or wire-format changes. Rollback is reverting the touched files; `RECOVERY_COOLDOWN` and `request_state`'s existing behavior are unchanged.

## Open Questions

- Exact consecutive-mismatch threshold for the live-divergence trigger. Deferred to implementation/testing (task 2) — the spec's contract ("single doesn't trigger, sustained does") holds for any reasonable small threshold, so picking it doesn't change the spec, the approach, or the task breakdown.
