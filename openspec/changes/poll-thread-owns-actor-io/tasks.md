## 1. Baseline and remaining audit

- [ ] 1.1 Record the pre-change baseline on **develop**: 10x `xstudio_selects_script_xstudio`, plus counts of `POLL-SLOW cmd:resolve_selection` and `Dequeue timeout` per run. (Expected ~9/10, 0 timeouts.)
- [ ] 1.2 Record the same baseline on the **PR** build. (Expected 0/10, 12-13 `Dequeue timeout`.) Identify each build by `xstudio.bin` mtime, not the source tree.
- [ ] 1.3 Capture current broadcast latency (event → `queuing playback state broadcast`) on develop, as the reference for design Decision 3.
- [ ] 1.4 Audit `_session_connect_worker` — the one path not yet inspected. If it performs actor I/O concurrently with the poll thread, add it to the scope; if it completes before the poll loop starts, record that and move on.

## 2. `on_selection_event` (smallest — do first)

- [ ] 2.1 Remove the `check_and_update_active_playhead()` call from the callback thread; enqueue it instead.
- [ ] 2.2 Delete the 1 Hz `_last_playhead_check` throttle and its comment, which exist only to ration off-thread I/O (design Decision 4).
- [ ] 2.3 Confirm selection resolution still runs promptly — `enqueue_selection_update()` already queues `resolve_selection`, so this must not double-enqueue.

## 3. `on_playhead_attribute_changed`

- [ ] 3.1 Stop reading `attr.name` on the callback thread; enqueue `(attr, role)` and resolve the name on the poll thread (design Decision 2).
- [ ] 3.2 Move the `active_playhead.playing` / `.position` reads to the poll thread.
- [ ] 3.3 Move the `_carry_over_playback_mode` call ("Current Media Uuid" branch) to the poll thread.
- [ ] 3.4 Keep the existing bounded reads and `_reacquire_active_playhead` recovery on the poll side — deferring makes a stale handle *more* likely, not less.
- [ ] 3.5 Remove `_carry_over_lock` and its `__init__`/`reset` state, now that only one thread calls it (design Decision 4).

## 4. `on_global_playhead_event` (largest)

- [ ] 4.1 Split the handler by what actually needs the poll thread, rather than rewriting it — keep event parsing and cheap local state in the handler.
- [ ] 4.2 Move `check_and_update_active_playhead`, `_read_pinned_source_mode_fresh`, `get_or_create_clip_timeline` and `get_viewed_container_safe` to the poll thread.
- [ ] 4.3 Measure broadcast latency against the 1.3 baseline and confirm no material regression, especially on the scrub path.

## 5. Tests

- [ ] 5.1 Add a unit test asserting each of the three handlers performs no actor I/O — e.g. a connection stub that raises on `request_receive`, so a handler that touches an actor fails loudly.
- [ ] 5.2 Add a test that the enqueued work still reaches the poll thread and produces the same broadcasts (behaviour preserved, thread changed).
- [ ] 5.3 Run `./run_tests_xstudio.sh` and confirm no regressions.

## 6. Verification

- [ ] 6.1 Re-run `xstudio_selects_script_xstudio` 10x on **develop**; confirm no `POLL-SLOW cmd:resolve_selection` and no `Dequeue timeout`.
- [ ] 6.2 Re-run 10x on the **PR** build; confirm it now passes (baseline 0/10). This is the load-bearing result — it is what unblocks that PR.
- [ ] 6.3 Run `debug/xstudio_request_receive_repro.py --concurrency-probe` on each build to confirm which listener topology was actually exercised, so a pass is not misattributed.
- [ ] 6.4 Run the sibling selection tests (`xstudio_selects`, `openrv_hosts_selection`, `contended_visibility_selection`, `contended_visibility_selection_rv`) on both builds.
- [ ] 6.5 Check `converged_late` in `run_history.jsonl` — a latency regression from deferring shows up there before it shows up as a failure (design Risks).

## 7. Follow-ups (not in this change)

- [ ] 7.1 Note the option of fixing `Connection` thread-safety upstream (per-connection lock around `request_receive`, or demultiplexing responses by request id) — this change works around the API as it is.
- [ ] 7.2 Once this lands and the PR is adoptable, revisit the deferred isolation/seek correctness fix in `fix-xstudio-selects-flakiness`, which needs prompt playhead adoption.
