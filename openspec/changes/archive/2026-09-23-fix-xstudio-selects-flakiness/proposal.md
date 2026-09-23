## Why

`xstudio_selects_script_xstudio` (sync_test) still fails ~50% of runs after
commit `b73b60c` fixed one instance of a poll-thread stall. Measured on
2026-08-24: **5 of 10 runs failed, all `checkpoint_timeout`**, every one
carrying the identical signature — a single `[POLL-SLOW] cmd:resolve_selection
took ~2.1s` immediately followed by `broadcast_view_state: new source-clip
isolation … forcing frame=0 (discarding position N)`, where `N` is the frame
the test had just successfully seeked to.

This is the *same* defect `b73b60c` addressed, reached by a different call
site. That commit added a dedup guard *inside* `_carry_over_playback_mode`,
but the expensive work is `self._viewport_playhead()` evaluated as its
**argument** — so it still runs unconditionally on every selection change,
before the guard can short-circuit anything.

The underlying cost is systemic, not local to that one line. Constructing an
xStudio `Playhead` wrapper runs `ModuleBase.__init__`, which enumerates and
individually fetches every one of the playhead's ~46 attributes — one
synchronous actor round-trip apiece. The plugin constructs these wrappers
freely: `_viewport_playhead()` builds a fresh one on every call, from six call
sites, on both the poll thread and xStudio's callback threads. Two of those
sites construct a wrapper *solely to read its identity* and then discard it —
`check_and_update_active_playhead` does this on every poll tick (~1 Hz). When
two threads do this concurrently against a freshly-spawned playhead actor that
is still initialising, the requests serialise and the poll thread stalls ~2s.

Two consequences, and the fix needs to address both:

- **Correctness**: the isolation's first-frame reset is applied whenever the
  resolution finally runs, with no check that the position it discards was
  established *after* the selection that triggered it. Any stall — this one or
  a future one — silently destroys a user's seek.
- **Cost**: routine poll work should not be doing ~46 actor round-trips to
  answer "is this the same playhead as last time?".

Fixing only the cost would leave the race latent; fixing only the correctness
would leave a 2s poll stall that degrades responsiveness and will resurface as
some other symptom.

## What Changes

- Make an isolation's frame-0 reset **conditional on the selection still being
  the latest position intent**, so a seek that lands after the triggering
  selection event is not discarded, regardless of how long resolution took.
  This is the durable fix; the performance work below reduces exposure but
  cannot by itself close a race.
- Make playhead **identity checks cheap**: resolving the viewport's playhead
  actor handle must not require constructing a full attribute-enumerating
  wrapper. Sites that only compare identity (notably the ~1 Hz
  `check_and_update_active_playhead` scan) must stop paying ~46 round-trips
  per tick.
- Avoid constructing a playhead wrapper on the selection path when the
  existing dedup guard would discard it anyway (the `b73b60c` guard, hoisted
  above the expensive argument evaluation).
- Extend `sync_test` coverage so the discarded-seek failure is asserted
  directly rather than only showing up as a flaky frame checkpoint.

**Explicitly out of scope** (observed once each across ~16 runs, not
reproduced in the 10-run and 6-run batches, and not root-caused): an auto-play
race reported as `"playback still active … a seek must park the playhead"`,
and a `structural_consensus` mismatch. Both were previously believed to be the
dominant residual failures; the measurements above show they are not. They are
recorded in design.md as open follow-ups so the evidence is not lost, but
chasing them is deferred rather than bundled into this change.

## Capabilities

### New Capabilities

None. Both aspects are gaps in existing, already-specced behavior.

### Modified Capabilities

- `xstudio-clip-selection-sync`: adds the guarantee that an isolation's
  first-frame reset does not discard a newer position, complementing the
  existing "Isolating a clip seeks both peers to the clip's first frame" and
  "Selection-path playhead operations must not freeze the app" requirements —
  neither of which currently constrains what happens when resolution is
  *slow* rather than *hung*.
- `xstudio-plugin-module-structure`: adds a cost constraint on routine
  poll-thread playhead work, alongside the existing "Threading invariant
  preserved" requirement, which governs *which thread* runs what but says
  nothing about how expensive that work may be.

## Impact

- `xstudio_plugin/ori_sync/playback_sync.py` — `_viewport_playhead`,
  `check_and_update_active_playhead`, `_carry_over_playback_mode` call site,
  and `broadcast_view_state`'s `_new_source_clip` reset branch.
- `sync_test/` — regression coverage for the discarded-seek behavior.
- No wire-protocol change; no change to what peers exchange.
