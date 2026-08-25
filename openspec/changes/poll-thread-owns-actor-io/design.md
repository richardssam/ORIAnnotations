## Context

Evidence and audit are in proposal.md. In short: the `Connection` is not
thread-safe, a collision costs the full `default_timeout_ms`, and three
handlers in `playback_sync.py` issue actor I/O from xStudio callback threads.

### The audit

Method: for every handler reachable from an xStudio callback thread, inspect
the body for actor-I/O markers (`request_receive`, attribute get/set, wrapper
property reads, and the helpers that wrap them), then confirm the "clean"
results by listing what each handler actually calls.

| module | handler | verdict |
| --- | --- | --- |
| playback_sync | `on_global_playhead_event` (349 ln) | **actor I/O** |
| playback_sync | `on_playhead_attribute_changed` (139 ln) | **actor I/O** |
| playback_sync | `on_selection_event` (29 ln) | **actor I/O** |
| structure_sync | `on_structure_event`, `on_test_container_event`, `on_timeline_item_event` | enqueue-only |
| annotation_sync | `on_bookmarks_event`, `on_draw_event`, `on_annotation_event` | cheap/local |
| annotation_sync | `on_core_annotation_event` | enqueue-only |
| ori_sync_plugin | `_dispatch_event_group` | pure fan-out |
| ori_sync_plugin | `_discovery_timeout_task` | enqueue-only |

Not audited in depth: `_session_connect_worker`, which runs once on its own
thread during connect and does set up subscriptions. It is out of the steady
state this change targets, but should be confirmed rather than assumed (task
1.4).

## Goals / Non-Goals

**Goals**
- No actor I/O on any xStudio callback thread.
- Remove the mitigations that exist only to survive off-thread I/O.
- Pass on both xStudio builds.

**Non-Goals**
- Fixing `Connection` thread-safety upstream. Plausible as a follow-up
  (`request_receive` could take a per-connection lock, or responses could be
  demultiplexed by request id), but this change makes the plugin correct
  against the API as it is.
- The deferred isolation/seek correctness fix from
  `fix-xstudio-selects-flakiness`. This unblocks it; it is not it.
- Changing which events the plugin subscribes to.

## Decisions

### Decision 1: Defer, do not lock

The fix is to move calls, not to guard them. A lock makes the collision
orderly but still parks a callback thread for up to the full timeout, and
xStudio's dispatch threads are not ours to block. `3e21954` added
`_carry_over_lock` for exactly this and it did not prevent the 100s stalls —
that lock should be removed by this change, not extended.

### Decision 2: Enqueue handles, resolve on the poll thread

`on_playhead_attribute_changed(attr, role)` currently reads `attr.name` — an
actor round-trip — purely to choose a branch. Enqueue `(attr, role)` and let
the poll thread read the name and dispatch.

This is safe because a handle is a value, not a read. The risk is staleness:
by the time the poll thread resolves it, the attribute or its playhead may be
gone. That is already the normal case this code handles (bounded reads plus
`_reacquire_active_playhead`), so it needs no new machinery — but the poll-side
resolution must keep those bounds.

### Decision 3: Accept added latency, measure it

Deferring adds one queue hop (poll timeout is 0.1s) between an event and its
effect. For position broadcasts that is a real change in responsiveness.

Accepted because the alternative is a stall of up to
`default_timeout_ms`, and because the poll loop already coalesces bursts
(`_pending_scrub_state`). But it must be measured, not assumed: task 4.3
compares broadcast latency before and after, and the scrub path is the one to
watch.

### Decision 4: Delete the mitigations, do not leave them

Two exist solely because of off-thread I/O:

- the 1 Hz throttle on `check_and_update_active_playhead` in
  `on_selection_event` — its comment states the reason ("blocking reads stack
  into multi-second stalls")
- `_carry_over_lock`

Leaving them would preserve the impression that off-thread I/O is acceptable
if rationed. The spec delta explicitly rejects that reading, and the code
should match.

## Risks / Trade-offs

- **`on_global_playhead_event` is 349 lines** and the largest single piece of
  work. It should be split by *what needs the poll thread* rather than
  rewritten wholesale; a behaviour change smuggled in here would be hard to
  distinguish from the intended one.
- **Latency regressions may be subtle** — a slower view switch shows up as a
  sync test that converges late rather than fails. Watch `converged_late` in
  `run_history.jsonl`, not just pass/fail.
- **Two-build verification doubles the cost** of every measurement and needs a
  rebuild between runs. Identify the live build by binary mtime — the source
  tree has been wrong three times across two sessions.

## Verification

Primary signal is the absence of the stall, not just the pass rate: after the
change, `POLL-SLOW cmd:resolve_selection` and `Dequeue timeout` should not
appear in a plugin log on **either** build.

Baselines: develop 9/10 (post-`3e21954`), PR 0/10. Success is a suite that
passes on both. Also re-run
`debug/xstudio_request_receive_repro.py --concurrency-probe` to confirm the
listener topology each build actually has, so a passing run cannot be
misattributed to the wrong build.
