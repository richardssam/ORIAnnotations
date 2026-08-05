## 1. Player pause/resume

- [x] 1.1 Add `pause()` / `resume()` to `sync_recorder/player.py::SyncPlayer`, recording the pause instant and shifting `_play_start_time` forward by the elapsed pause duration on resume, so `current_offset` is unchanged across the pause.
- [x] 1.2 Make `tick()` suspend recorded-event dispatch while paused, while still calling `_process_network_requests()` so joining peers are answered (mirroring the existing `wait_for_peer` gate).
- [x] 1.3 Make `pause()`/`resume()` idempotent — pausing while paused, or resuming while not paused, is a no-op that cannot corrupt the logical timeline.
- [x] 1.4 Confirm the pause interacts correctly with the `wait_for_peer` gate (pausing before the gate clears must not arm the event clock early) and with the drain window (`_drain_deadline` must not expire while frozen).
- [x] 1.5 Check whether `_update_timestamps` rewrites event timestamps against raw wall clock; if so, ensure a pause does not make replayed timestamps inconsistent with their logical offsets.

## 2. Player-level verification (no runner changes yet)

- [x] 2.1 Verify the reported playback offset is identical immediately before a pause and immediately after the corresponding resume.
- [x] 2.2 Verify no event is dispatched while paused, however long the pause lasts.
- [x] 2.3 Verify event ordering and inter-event spacing after resume match the recorded spacing (no skips, duplicates, or bursts of "catch-up" events).
- [x] 2.4 Verify a peer requesting state mid-pause is still answered.

## 3. Runner freeze integration

- [x] 3.1 Add a context manager in `runner.py` that freezes playback on entry and resumes on **every** exit path (normal, early break, exception) — a leaked freeze hangs the entire run, so this must be structural rather than paired calls.
- [x] 3.2 Wrap frame-checkpoint validation in the freeze context.
- [x] 3.3 Wrap structural state-checkpoint validation (the up-to-10s poll) in the freeze context.
- [x] 3.4 Log each freeze with its duration, so time removed from real-time replay pacing is visible.
- [x] 3.5 Confirm the runner's own `current_offset = time.time() - player._play_start_time` computation stays correct across freezes (it should, since it reads the same anchor — verify rather than assume).
- [x] 3.6 Leave freezing off for script-driven tests, which have no recording playing during their assertions.

## 4. Regression verification

- [x] 4.1 **Primary regression test**: run `xstudio_selects` at `checkpoint_validation_delay: 3` — currently fails with `frame_held=True` (apps at frame 68 vs snapshot's 0). It SHALL now pass *with the frame assertion still active*, not by falling below the `frame_held` threshold as `delay: 4` does.
- [x] 4.2 Confirm the t=9.5s frame checkpoint (expected frame 69) passes, and is validated inside its recorded hold window rather than after `playing=True` at t=21.51.
- [x] 4.3 Run the full suite; compare per-test results against `run_history.jsonl` and confirm no test that previously passed now fails.
- [x] 4.4 Compare `time_to_converge` / `duration` history before and after for previously-flaky tests — freezing should reduce run-to-run variance, and the history log is exactly the instrument for checking that.
- [x] 4.5 Sanity-check total suite runtime growth from accumulated freezes; flag it if it is disproportionate rather than accepting silently.

## 5. Restore retry-eligibility for point-in-time checkpoints

- [x] 5.1 Only after §4 passes: revert the `sync-tests-tracking` restriction that excluded frame/state checkpoints from the bounded retry, since a frozen target no longer goes stale.
- [x] 5.2 Update `sync-tests-tracking`'s `Bounded Retry For Convergence-Timing Failures` requirement (or supersede it here) so the two changes do not leave contradictory statements in the spec set.
- [x] 5.3 Re-run §4.1 and §4.3 with retries re-enabled to confirm no regression.

## 6. Documentation

- [x] 6.1 Update `sync_test/README.md` to explain that playback freezes during checkpoint validation, and what `checkpoint_validation_delay` now means (how long after the event to *start* checking — no longer a race against the recording).
- [x] 6.2 Note in the README that `frame_held` / recorded-silence filters remain in place but are now belt-and-braces rather than load-bearing, with a pointer to the follow-on work to retune them.
