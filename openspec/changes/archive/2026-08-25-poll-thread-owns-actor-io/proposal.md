## Why

The xStudio Python `Connection` is not thread-safe for concurrent
`request_receive`. Measured 2026-08-25 with
`debug/xstudio_request_receive_repro.py` (standalone, no plugin), four threads
issuing requests on one connection:

| build | mean | p50 | max | timeouts |
| --- | --- | --- | --- | --- |
| develop | 5.28s | 0.0142s | **200.01s** | 2 |
| PR | 15.81s | 0.0147s | **100.05s** | 2 |

Serial use is clean on both (0.0002s bare, 0.0057s for a `Playhead`
construction, flat with 40 subscriptions held). Only concurrency breaks it, and
a stalled request waits the *full* `default_timeout_ms`.

The plugin issues `request_receive` from its poll thread **and** from xStudio
callback threads, so it collides with itself. That is what produced the ~2s
`resolve_selection` stalls behind the `xstudio_selects_script_xstudio` flake —
`fix-xstudio-selects-flakiness` (commit `3e21954`) took it 5/10 → 9/10 purely
by removing concurrent traffic, without addressing the cause.

It also decides the fate of `xstudio/pr/python-per-subscription-listeners`.
Today one shared listener per connection serialises *all* Python callbacks —
measured: 5 subscriptions, 45 invocations, **1 thread, peak overlap 1**. Under
that PR each subscription gets its own listener: **5 threads, peak overlap 5**.
Concurrency against the connection therefore goes from 2-way (poll thread vs.
the single callback thread) to N-way, which is why the suite goes 9/10 → 0/10
on that build. The PR does not introduce the defect; it removes an accidental
serialisation the plugin has been relying on without knowing.

So this is not only a flakiness fix. Until it is done, that PR cannot land —
and the deferred correctness work in `fix-xstudio-selects-flakiness` (an
isolation discarding a seek) stays blocked, because its fix needs prompt
playhead adoption, which is only safe once that PR can land.

## What Changes

An audit of every handler reachable from an xStudio callback thread found the
problem confined to **three handlers, all in `playback_sync.py`**:

| handler | lines | actor I/O it performs |
| --- | --- | --- |
| `on_global_playhead_event` | 349 | `check_and_update_active_playhead`, `_read_pinned_source_mode_fresh`, `get_or_create_clip_timeline`, `get_viewed_container_safe` |
| `on_playhead_attribute_changed` | 139 | reads `attr.name`, `active_playhead.playing`, `.position`; `_carry_over_playback_mode` |
| `on_selection_event` | 29 | `check_and_update_active_playhead` |

Everything else already complies: all three `structure_sync` handlers and
`on_core_annotation_event` are enqueue-only; the other `annotation_sync`
handlers are cheap/local; `_dispatch_event_group` is pure fan-out;
`_discovery_timeout_task` enqueues.

- Move all actor I/O out of those three handlers onto the poll thread, using
  the existing `_cmd_queue` — no new queue is introduced.
- Restructure the reads that currently produce a *value the handler branches
  on* (notably `attr.name`), since enqueuing cannot return a value.
- Remove the workarounds that exist only to make off-thread I/O survivable —
  the 1 Hz throttle on `check_and_update_active_playhead`, and the
  `_carry_over_lock` added in `3e21954`.
- Verify on **both** xStudio builds, since the fix should make the plugin
  correct regardless of listener topology.

## Capabilities

### Modified Capabilities

- `xstudio-plugin-module-structure`: strengthens "Threading invariant
  preserved". That requirement already says handlers "SHALL only mutate cheap
  local state or enqueue onto `_cmd_queue`", but states it as a structural rule
  about where code lives. It needs to be an enforceable rule about *actor I/O
  specifically*, with the reason (the connection is not thread-safe; a
  violation costs up to the full timeout, not a slow call), so that a future
  reader cannot satisfy it by throttling or locking a violating call instead of
  moving it.

## Impact

- `xstudio_plugin/ori_sync/playback_sync.py` — the three handlers, plus the
  helpers they call off-thread.
- `xstudio_plugin/ori_sync/ori_sync_plugin.py` — possibly new `_cmd_queue`
  command kinds for the deferred work.
- Unblocks `xstudio/pr/python-per-subscription-listeners` and, through it, the
  deferred correctness half of `fix-xstudio-selects-flakiness`.
- No wire-protocol change; no change to what peers exchange.
