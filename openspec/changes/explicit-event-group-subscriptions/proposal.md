## Why

> **STATUS 2026-08-25: premise disproved — do not implement as written.**
> This change was created on the hypothesis that the plugin receives events via
> a routing bug and must subscribe explicitly. A trace diff has since shown the
> problem is a **synchronous `request_receive` timeout under
> `xstudio/pr/python-per-subscription-listeners`**, which explicit subscriptions
> would not fix. Retained as the record of that investigation. The live work is
> the minimal repro described below; if it confirms an upstream regression this
> change should be closed in favour of an xStudio bug report.

The xStudio sync plugin stops syncing when
`xstudio/pr/python-per-subscription-listeners` is applied. Measured 2026-08-25
with identical plugin code (`3e21954`) and scenario, only the xStudio build
differing:

| | develop | pr_1 | pr_2 | pr_3 |
| --- | --- | --- | --- | --- |
| `xstudio_selects_script_xstudio` | 9/10 pass | fail | fail | fail |
| `Dequeue timeout` | **0** | 1 | 12 | 13 |
| `TimeoutError` | 0 | 0 | 6 | 4 |
| `POLL-SLOW` | 0 | 1 | 5 | 16 |

The build was confirmed live by its own signature (`parent_atom` warnings 6 → 0).

The failing call is a **plain synchronous `request_receive`** —
`ModuleAttribute.name` reading an attribute's Title role inside
`ModuleBase.__init__`, i.e. ordinary `Playhead(conn, actor)` construction. It
raises `RuntimeError: Dequeue timeout` after ~4 s. That call involves no event
groups, so which groups the plugin subscribes to cannot explain it.

Downstream: `Playhead` construction fails → the plugin cannot read the playhead
→ view-state broadcasts stop → peers diverge ("apps are showing different
media"). The whole plugin then degrades (`display.poll took 8.0s`).

Two hypotheses were tested and **disproved**:

1. *The plugin receives `show_atom` via crosstalk.* `show_atom` is emitted on
   `PlayheadGlobalEventsActor::event_group_`
   (`playhead_global_events_actor.cpp:266-284`) and the plugin explicitly
   subscribes to that group via `subscribe_to_global_playhead_events`
   (`python/src/xstudio/api/module.py:360`).
2. *Listener actors accumulate per `Playhead` construction.* An instrumented run
   showed a single construction taking 0.010 s.

## What Changes

- ~~Build a minimal repro~~ **DONE**: `debug/xstudio_request_receive_repro.py`.
  Result: serial use is clean (phase 1/2/3), but **4 threads constructing
  concurrently gives mean 15.8 s / max 100.05 s / 2 timeouts** — exactly
  `Connection.default_timeout_ms`. Concurrency is the trigger; subscriptions
  are not.
- **Run the repro on `develop`** to decide ownership. Strong prior that this is
  pre-existing: two threads on one `Connection` already produced ~2 s stalls on
  develop (`fix-xstudio-selects-flakiness` §2 was largely about avoiding that).
- If pre-existing: the fix is plugin-side — stop issuing `request_receive` from
  xStudio callback threads, which the plugin's own threading invariant already
  forbids ("event handlers SHALL only mutate cheap local state or enqueue onto
  `_cmd_queue`"). That invariant is currently violated by
  `_adopt_playhead` -> `_carry_over_playback_mode` ->
  `refresh_playhead_attributes`.
- Either way, establish that the suite passes on **both** builds before the PR
  lands, so it is not a flag day.

## Capabilities

No spec deltas. The remaining work is investigation (build a repro, determine
which side is at fault); `skip_specs: true` is set accordingly. If the outcome
is plugin-side work, the requirements will be written then — writing them now
would encode the premise this change has already disproved.

## Impact

- Blocks `xstudio/pr/python-per-subscription-listeners` from landing.
- Blocks the deferred correctness half of `fix-xstudio-selects-flakiness`.
- Possible upstream bug report against `src/python_module/src/py_context.cpp`.
- No plugin code changed yet; no wire-protocol change.
