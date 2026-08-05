## Context

See proposal.md — Why. The implementation-relevant shape of the current code:

- `SyncManager` has no election method. `start_session()` sets `STATE_DISCOVERING` and broadcasts `WHO_IS_MASTER`; the class docstring explicitly delegates the timeout and the election to "the caller".
- There are **four** self-election sites, three of them outside the manager:
  1. `xstudio_plugin/ori_sync/ori_sync_plugin.py:496` `_discovery_timeout_task` — runs on its own daemon thread (`threading.Thread(..., daemon=True)` started in `connect_to_session`), sleeps `DISCOVERY_TIMEOUT`, then registers timelines and does the four-line ritual (`is_master`, `master_guid`, `broadcast_master_response()`, `_set_status(STATE_SYNCED)`).
  2. `ori_sync_plugin.py:805` `state_request_timeout` branch of `_handle_manager_event` — same ritual, already on the poll thread.
  3. `rvplugin/ori_sync/plugin.py:356` `_init_as_master` — sets `is_master` and `_set_status(STATE_SYNCED)` only; it does **not** set `master_guid`, and the `broadcast_master_response()` happens later, at the end of `_deferred_master_init` (`plugin.py:406`), after the initial timelines are built.
  4. `manager.py:1719` master-failover inside `tick()` — sets `is_master`, `master_guid`, `broadcast_master_response()`; no status change because the peer is already `SYNCED`.
- `_set_status(STATE_SYNCED)` fires the `on_synced` callbacks. In xStudio `_on_synced` reads `manager.is_master` and enqueues `load_timelines` for clients; in RV it rebuilds the session. Ordering therefore matters: `is_master`/`master_guid` must be set *before* the status transition, which is what sites 1 and 2 already do.
- xStudio's poll thread drains `_cmd_queue` through `_execute_command` (`ori_sync_plugin.py:642`), a flat `if/elif` chain on the command name. The manager tick and every manager-touching command run there.
- The only mutation the discovery thread makes that isn't election is `register_timeline(tl)` for each timeline from `self.builder.build_otio_timelines()` — and `build_otio_timelines` reads live xStudio actors, so it is doing actor I/O off the poll thread too.

## Goals / Non-Goals

**Goals:**

- One election operation on `SyncManager`, used by all four sites.
- xStudio's discovery-timeout thread reduced to: sleep, check status, enqueue.
- Preserve RV's ordering, where mastership is claimed locally first and announced only after the initial timelines exist.
- Keep the wire sequence byte-identical, so a peer running unmodified code cannot tell the difference.

**Non-Goals:**

- Role-aware election (observer/participant eligibility) — that is `session-roles` Phase 2, which builds on this API.
- Changing `DISCOVERY_TIMEOUT`, the failover threshold, or any timing.
- Moving the discovery *timer* into the manager. The timeout stays a host concern; only the election it triggers moves.
- Making `SyncManager` thread-safe in general. The fix restores the single-writer invariant rather than adding locks.

## Decisions

**`elect_self_as_master(broadcast: bool = True)` on `SyncManager`, ordered flag → guid → broadcast → status.**
The order matches sites 1 and 2 exactly, and the status transition goes last so the `on_synced` callbacks observe a fully-elected manager. Alternative considered: firing the broadcast after the status change. Rejected — it would reorder the wire relative to the `on_synced` side effects (xStudio's popup, RV's rebuild), which is a behavioural change the proposal rules out.

**A `broadcast` flag rather than splitting into two methods or making RV broadcast early.**
RV must claim mastership synchronously (otherwise `poll_network`, which re-checks `status == STATE_DISCOVERING` every 33 ms tick, would call `_init_as_master` again on the next tick and repeat for the whole deferral window — up to 10 s of `_deferred_master_init` retries), but it must announce late (`_deferred_master_init` builds the timelines that a joiner's `STATE_REQUEST` needs, and `send_state_snapshot` returns early when `_timelines` is empty). One method with a flag keeps the state transitions in one place while letting RV keep its existing announce point. Alternative considered: `elect_self_as_master()` plus a separate `_claim_master()`; rejected as two names for one concept, which is what this change is removing.

**RV starts setting `master_guid`, which it never did.**
`master_guid` is only consumed by `request_state()` (guarded on being non-`None`, and a master never calls it) and by the `master_found` event, so setting it on an RV master is inert today. It is a latent-bug fix and makes both hosts' post-election state identical, which matters for `session-roles`. Flagged rather than hidden: this is the one state difference the change introduces.

**xStudio enqueues `("self_elect", {})`; the poll thread does registration *and* election.**
Timeline registration has to move with the election — it mutates `manager._timelines`, and it calls `build_otio_timelines()`, which reads xStudio actors. Splitting them would leave half the race. The handler in `_execute_command` re-checks `self.manager and self.manager.status == STATE_DISCOVERING` before acting, so a master discovered during the queue latency (or a `leave_session` drained ahead of it) cancels the election. The timeout thread keeps its own cheap status check purely to avoid enqueuing in the common case; the drain-time check is the authoritative one.

**Keep the timeout on a `time.sleep` thread rather than folding it into the poll loop.**
A deadline check in `_poll_loop` would be strictly less code, but it entangles discovery with the poll loop's existing timing sections and changes when the election fires relative to a slow tick (`[POLL-SLOW]` shows multi-second ticks are real). The sleep-then-enqueue thread preserves current timing while satisfying the invariant. Worth revisiting if `session-roles` needs a cancellable discovery deadline.

**Migrate the manager-internal failover path too.**
It is the fourth copy of the same ritual. `_set_status(STATE_SYNCED)` early-returns when the status is unchanged, so calling the shared operation from a peer that is already `SYNCED` will not re-fire `on_synced` — the "re-electing an existing master is inert locally" scenario pins that. `_last_who_is_master_time = None` stays at the call site; it is failover bookkeeping, not election state.

## Risks / Trade-offs

- **Election latency grows by up to one poll-queue turnaround.** The poll loop blocks on `_cmd_queue.get(timeout=0.1)` and drains immediately, so the added delay is sub-100 ms in the normal case — but a tick already inside a slow section (`load_otio`, a bounded actor read) can push it to seconds. → Acceptable: `DISCOVERY_TIMEOUT` is already the dominant term, and the drain-time status re-check makes a late election safe rather than merely slow.
- **Two peers starting simultaneously could both elect.** That race exists today and is unchanged in kind, but the extra queue latency widens the window slightly. → The drain-time `STATE_DISCOVERING` re-check narrows it back, since an `I_AM_MASTER` processed by an intervening `manager.tick()` cancels our election — which the current code, electing straight off the timer thread, cannot do.
- **RV's newly-set `master_guid` reaching an unexamined consumer.** → Consumers were enumerated (`request_state`, `_h_i_am_master`, `master_found`); none is reachable on a master. Covered by the two-peer master/client suite scenario.
- **`on_synced` ordering regression.** The callbacks are where the real work hangs (xStudio's `load_timelines` enqueue, RV's `rebuild_rv_session`), so an ordering slip in the new method would surface as a client that never loads. → Unit-test the operation's ordering directly (flag and guid visible from inside an `on_synced` callback), not just its end state.

## Migration Plan

None — no persisted state, no schema, no wire change. The manager method lands first and is additive; the three call-site migrations are independent of each other and each is revertable on its own. Rollback is reverting the files.
