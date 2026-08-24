## 1. Recovery trigger (shared)

- [x] 1.1 Add a `_last_view_recovery_attempt` timestamp and a view-recovery trigger method (plugin- or manager-level) that gates on `RECOVERY_COOLDOWN` and calls `manager.request_state(reason="recovery")`, parallel to and independent of `structure_diverged`'s own cooldown state.
- [x] 1.2 Verify `request_state` behaves safely if called for view-recovery while a structural recovery request is already outstanding (`STATE_JOINING`) — confirm the existing buffering/replay path covers this, or add the missing guard if it doesn't.

## 2. Join-time trigger

- [x] 2.1 In `confirm_join_state`, after `manager.confirm_join(actual)` returns, call the view-recovery trigger when the outcome equals `MISMATCHED` specifically (not `!= CONFIRMED`) — `NOT_CONFIRMED` must not reach it.
- [x] 2.2 Add a test asserting `NOT_CONFIRMED` never reaches the repair trigger (guards the exact distinction `post-join-state-confirmation` was written to protect).
- [x] 2.3 Add a test/manual repro: a peer joins onto a stale build that lands on the wrong clip, confirms `MISMATCHED`, and ends up on the session's actual clip/frame after the recovery request is answered.

## 3. Live-mismatch trigger

- [x] 3.1 Add a consecutive-`_tl_mismatch` counter around the existing detection in `apply_playback_state` (`playback_sync.py:2510-2532`), incrementing on mismatch and resetting to zero on a clean match.
- [x] 3.2 Pick and apply a threshold (start conservative; see design's Open Questions) above which the view-recovery trigger fires.
- [x] 3.3 Add a test: a single mismatched message followed by a clean one does not trigger recovery.
- [x] 3.4 Add a test/manual repro: a peer stuck on the wrong timeline, with the host continuing to scrub/play, triggers recovery and ends up following the host's actual timeline.

## 4. Rate limiting

- [x] 4.1 Add a test: a repair that doesn't resolve the divergence (e.g. simulated repeated mismatch after the recovery request is answered) does not immediately re-trigger — confirms the cooldown holds across both trigger paths.

## 5. Logging and observability

- [x] 5.1 Log which signal triggered a given recovery request (join-mismatch vs. sustained-live-mismatch) so live sessions can be diagnosed the same way `[JOIN-CONFIRM]` logging already supports.

## 6. Validation

- [x] 6.1 Manually verify in xStudio: join a session already showing a different clip/frame on the host, confirm the joiner ends up on the host's actual clip/frame without user action.
- [x] 6.2 Manually verify in xStudio: from the mismatched-join state above, have the host scrub/play further and confirm the joiner catches up without user action.
- [x] 6.3 Run the existing sync test suites (`./run_tests_core.sh`, `./run_tests_xstudio.sh`) to confirm no regression in existing join/structural-recovery flows.
- [x] 6.4 `openspec validate session-divergence-repair --strict`
