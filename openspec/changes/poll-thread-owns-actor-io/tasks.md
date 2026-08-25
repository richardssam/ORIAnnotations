## 1. Baseline and remaining audit

- [x] 1.1 Record the pre-change baseline on **develop**: 10x `xstudio_selects_script_xstudio`, plus counts of `POLL-SLOW cmd:resolve_selection` and `Dequeue timeout` per run. (Expected ~9/10, 0 timeouts.)
      → Measured 2026-08-24, recorded in proposal.md: **9/10**, 0 `Dequeue timeout`.
      Not re-run — the tree is unchanged since that measurement.
- [x] 1.2 Record the same baseline on the **PR** build. (Expected 0/10, 12-13 `Dequeue timeout`.) Identify each build by `xstudio.bin` mtime, not the source tree.
      → Measured 2026-08-24: **0/10**, 12-13 `Dequeue timeout`. Builds now live side by
      side: `build/` = develop (bin mtime Aug 17 16:30), `build_select/` = PR (Aug 24 22:41).
      `sync_tests*.yaml` point at `build/`, so develop is live.
- [x] 1.3 Capture current broadcast latency (event → `queuing playback state broadcast`) on develop, as the reference for design Decision 3.
      → Pre-change run on develop, 2026-08-25 11:11 (log archived): 6 `queuing playback
      state broadcast`, runner convergence `valid after` 0.0s ×3 / 0.1s ×2 / 0.2s ×1,
      0 `POLL-SLOW`, 0 `Dequeue timeout`, wall 32.8s. The event→queue delta itself is
      ~0 by construction pre-change (the handler queued inline), so the comparable
      figure is the runner's end-to-end `valid after`.
- [x] 1.4 Audit `_session_connect_worker` — the one path not yet inspected. If it performs actor I/O concurrently with the poll thread, add it to the scope; if it completes before the poll loop starts, record that and move on.
      → **Not concurrent with the poll thread.** It calls `connect_to_session`, whose
      *last* statement starts the poll thread (`ori_sync_plugin.py:557-560`); every actor
      read it makes (`check_and_update_active_playhead`, `acquire_annotations_ui`,
      `get_viewed_container_safe`, attribute `set_value`) happens before that thread exists.
      It *is* briefly concurrent with xStudio callback threads, because subscriptions are
      registered part-way through connect — but that window closes as a consequence of
      tasks 2-4, so no separate work. Also checked: the RabbitMQ consumer thread only
      `put`s onto `_incoming_queue` (`rabbitmq_network.py:176`); `manager.tick()` drains it
      on the poll thread, so no manager callback runs off-thread either.

## 2. `on_selection_event` (smallest — do first)

- [x] 2.1 Remove the `check_and_update_active_playhead()` call from the callback thread; enqueue it instead.
      → New `enqueue_playhead_check()` → `check_active_playhead` command, queued *ahead*
      of `resolve_selection` so the FIFO preserves "refresh, then resolve against it".
- [x] 2.2 Delete the 1 Hz `_last_playhead_check` throttle and its comment, which exist only to ration off-thread I/O (design Decision 4).
      → Gone, along with the `_last_playhead_check` field. Worth recording *why* it was
      so clearly the wrong shape: the poll loop was already making the identical call at
      the identical 1 Hz cadence (`ori_sync_plugin.py` step 6.15). The throttle was not
      rationing a needed read, it was rationing a duplicate of one already being done on
      the right thread.
      → Replaced with coalescing, which the spec delta does *not* rule out and which is
      not the same thing: nothing is dropped or delayed past the next poll tick, and the
      check the poll thread runs reflects every event that arrived before it. The flag is
      cleared before the check, so an event landing mid-check queues a fresh one.
- [x] 2.3 Confirm selection resolution still runs promptly — `enqueue_selection_update()` already queues `resolve_selection`, so this must not double-enqueue.
      → Confirmed: one `resolve_selection` put in the file, and `check_active_playhead`
      does not enqueue one. Test: `test_selection_storm_queues_one_playhead_check_not_one_each`
      asserts 20 events → 20 resolutions but 1 check.

## 3. `on_playhead_attribute_changed`

- [x] 3.1 Stop reading `attr.name` on the callback thread; enqueue `(attr, role)` and resolve the name on the poll thread (design Decision 2).
      → Done, but **better than the design assumed**, and the difference matters. Design
      Decision 2 accepted one queue hop per event to learn the attribute's identity. In
      fact `ModuleAttribute.uuid` is a plain local field (only `.name` round-trips), and
      `ModuleBase.__init__` has *already* built `attrs_by_name_` — the names were paid
      for when the wrapper was constructed and are sitting in memory. So
      `_seed_attr_names(ph)` copies the inverse map at adoption for free, and the steady
      state is a dict lookup: **zero** round-trips and **zero** extra hops, not one hop.
      → `resolve_playhead_attr_name` remains as the fallback for an attribute the map has
      not seen, so a miss is deferred rather than dropped. Re-seeded in
      `refresh_playhead_attributes`, which replaces the uuids and would otherwise leave
      the map stale for the next ~60 scrub events.
- [x] 3.2 Move the `active_playhead.playing` / `.position` reads to the poll thread.
      → `sample_and_broadcast_playhead`. Coalesced, because the poll thread reads *live*
      state: one queued marker covers every event that arrived before it runs, so a
      60 Hz scrub costs one sample per poll tick instead of 60 × 3 reads per second.
      Reading live rather than event-carried state is what makes that safe.
- [x] 3.3 Move the `_carry_over_playback_mode` call ("Current Media Uuid" branch) to the poll thread.
- [x] 3.4 Keep the existing bounded reads and `_reacquire_active_playhead` recovery on the poll side — deferring makes a stale handle *more* likely, not less.
      → `resolve_playhead_attr_name` keeps `bounded_timeout(_PLAYHEAD_TIMEOUT_MS)` and
      swallows the failure without poisoning the cache; the poll-thread methods carry
      `@bounded(_PLAYHEAD_TIMEOUT_MS)`. Covered by
      `test_a_stale_attribute_is_contained_on_the_poll_thread_too`.
- [x] 3.5 Remove `_carry_over_lock` and its `__init__`/`reset` state, now that only one thread calls it (design Decision 4).
      → Removed, with the `threading` import it was the last user of. Note this could
      only be done *after* §4: until `handle_viewport_playhead` existed, `_adopt_playhead`
      was still reachable from a callback thread.

## 4. `on_global_playhead_event` (largest)

- [x] 4.1 Split the handler by what actually needs the poll thread, rather than rewriting it — keep event parsing and cheap local state in the handler.
      → 349 lines → 71. The two actor-heavy branches moved wholesale and *unedited*
      (`handle_show_atom_media`, `handle_viewport_playhead`) — deliberately a
      dedent-and-relocate, not a rewrite, so nothing in that logic changed under cover of
      the thread move. The annotation/bookmark branch stayed put: it only stamps local
      state.
      → Also dropped `@bounded(_PLAYHEAD_TIMEOUT_MS)` from the handler rather than keeping
      it as a safety net. It sets `default_timeout_ms`, which is process-wide and not
      thread-local, so arming it on a callback thread reshaped the timeout of whatever the
      poll thread had in flight — the decorator was itself part of the problem. It now
      sits on the two poll-thread methods.
- [x] 4.2 Move `check_and_update_active_playhead`, `_read_pinned_source_mode_fresh`, `get_or_create_clip_timeline` and `get_viewed_container_safe` to the poll thread.
      → Plus the one the audit had not counted: `Playhead(connection, ph_remote)` in the
      Form-2 branch, which is ~46 round-trips on its own.
- [x] 4.3 Measure broadcast latency against the 1.3 baseline and confirm no material regression, especially on the scrub path.
      → No regression: post-change runs 27-29s wall against a 32.8s baseline run, and
      `POLL-SLOW` / `Dequeue timeout` stayed at 0 across all 10.
      → The one-hop cost Decision 3 accepted is mostly not paid, for two reasons found
      during implementation: §3.1's seeding removes the hop on the hot path entirely, and
      `_drain_cmd_queue` now drains to empty rather than to a snapshot of `qsize`, so a
      `playhead_sample` that queues its own `broadcast_playback_state` is executed in the
      same poll iteration instead of waiting out a whole interval. Capped at 256 commands
      so a self-requeuing command cannot starve the manager tick.

## 5. Tests

- [x] 5.1 Add a unit test asserting each of the three handlers performs no actor I/O — e.g. a connection stub that raises on `request_receive`, so a handler that touches an actor fails loudly.
      → `tests/xstudio_plugin/test_callback_thread_no_actor_io.py`: a `TrapConnection`
      and a `TrapPlayhead` whose every actor-facing member raises
      `ActorIOOnCallbackThread`. Chosen over counting calls so a violation names the
      requirement instead of passing because a stub happened to answer.
- [x] 5.2 Add a test that the enqueued work still reaches the poll thread and produces the same broadcasts (behaviour preserved, thread changed).
      → `test_a_readable_frame_event_still_broadcasts` now runs both halves end to end
      (handler queues `playhead_sample` → `sample_and_broadcast_playhead` broadcasts), and
      `test_poll_thread_resolves_an_unseeded_name_and_dispatches_it` closes the fallback
      loop.
- [x] 5.3 Run `./run_tests_xstudio.sh` and confirm no regressions.
      → 147 passed. Also `./run_tests_core.sh`: 738 passed.
      → **`test_playhead_attribute_read.py` needed rewriting, not just fixing.** It
      pinned the old contract explicitly ("the name is read once per event") — that was
      the correct property when a read was unavoidable, and this change removes the read.
      The incident it exists for (2026-08-13: `attr.name` raised `TimeoutError` into
      xStudio's dispatcher and silently deafened a peer holding the host seat) is kept as
      the file's rationale, and its properties are re-pinned where they now live: zero
      reads on the callback thread, one contained read on the poll-thread fallback.

## 6. Verification

- [x] 6.1 Re-run `xstudio_selects_script_xstudio` 10x on **develop**; confirm no `POLL-SLOW cmd:resolve_selection` and no `Dequeue timeout`.
      → **Primary signal clean: 0 `POLL-SLOW`, 0 `Dequeue timeout` across all 10.**
      → Pass rate, rebooted machine (0 swap, load ~5), 2026-08-25 13:53-14:00: **6/10**
      (3x `structural_consensus`, 1x `checkpoint_timeout`).
      → That is below the 9/10 recorded baseline, so it was A/B'd rather than explained
      away. **Control at `HEAD~1` on the same machine minutes later: 4/10.**
      → **Both batches ran on battery power**, which was only established afterwards. That
      is the likely reason neither version reached its own 9/10 baseline: macOS throttles
      CPU and reduces available cores on low power, and this test is timing-sensitive
      enough that three of its four failure modes here (`checkpoint_timeout`,
      `structural_consensus`) are latency-driven. Treat both absolute numbers as invalid.
      → The A/B *comparison* survives, because both batches were throttled identically:
      the change is no worse than the code it replaces, and on these figures better.
      That is the whole value of having run the control — a single 6/10 would have looked
      like a regression it is not.
      → Redo 6.1 and the 6.4 siblings on mains power before quoting any absolute rate.
      Record power state alongside swap/load in future runs; it belongs next to
      `feedback_suite_results_need_memory_check` as a thing that mimics timing races.
      → The first attempt at this ran during post-reboot churn and had to be discarded:
      macOS session-restore had relaunched a sync_test OpenRV (inspector on port 19000),
      an xStudio, and two orphaned `rvio` thumbnail jobs pinning 300-600% CPU. Kill any
      `rv_thumbnails` / `start_openrv_inspector` strays and wait for load < 5 before
      trusting a run.
- [ ] 6.2 Re-run 10x on the **PR** build; confirm it now passes (baseline 0/10). This is the load-bearing result — it is what unblocks that PR.
- [ ] 6.3 Run `debug/xstudio_request_receive_repro.py --concurrency-probe` on each build to confirm which listener topology was actually exercised, so a pass is not misattributed.
- [~] 6.4 Run the sibling selection tests (`xstudio_selects`, `openrv_hosts_selection`, `contended_visibility_selection`, `contended_visibility_selection_rv`) on both builds.
      → develop: `openrv_hosts_selection` PASS, `contended_visibility_selection` PASS,
      `contended_visibility_selection_rv` PASS, `xstudio_selects` FAIL
      (`structural_consensus` — the same recurring mode, present in the control too).
      → `contended_visibility_selection` passing is worth noting: it was failing 5/5
      before `3e21954`.
      → Also run on battery — see 6.1. The three passes stand (a pass under throttling is
      still a pass); the single `xstudio_selects` failure needs re-running on mains before
      it counts as anything.
      → PR build outstanding.
- [x] 6.5 Check `converged_late` in `run_history.jsonl` — a latency regression from deferring shows up there before it shows up as a failure (design Risks).
      → `converged_late` turned out to be useless as the signal: it is `True` on every
      run of this test, passing or failing, before and after. `time_to_converge` is the
      field that actually discriminates.
      → Convergence on *passing* runs, change vs control, same machine:
        median **0.219s vs 0.181s**, i.e. **+38ms** — inside the one-poll-hop (0.1s)
        budget Decision 3 accepted, and far short of the `default_timeout_ms` stall it
        buys out.
        (Raw change figures include a 1.498s first-run cold start; excluded from the
        median above, and called out rather than quietly dropped. Control's own first run
        failed, so there is no cold-start figure to compare it against.)

## 7. Follow-ups (not in this change)

- [ ] 7.1 Note the option of fixing `Connection` thread-safety upstream (per-connection lock around `request_receive`, or demultiplexing responses by request id) — this change works around the API as it is.
- [ ] 7.2 Once this lands and the PR is adoptable, revisit the deferred isolation/seek correctness fix in `fix-xstudio-selects-flakiness`, which needs prompt playhead adoption.
