## Context

`xstudio_selects_script_xstudio` fails ~50% of runs. Commit `b73b60c` fixed one
instance of the underlying stall; this change fixes the mechanism.

### Evidence

Two measured batches on 2026-08-24, after `b73b60c`:

| Batch | Runs | Failed | Failure kind |
| --- | --- | --- | --- |
| A | 10 | 5 | all `checkpoint_timeout` |
| B | 6 | 1 | `checkpoint_timeout` |

All six failures carry the identical signature in
`sync_test/logs/xstudio_selects_script_xstudio/xstudio_plugin_19000.log`:

```
[POLL-SLOW] cmd:resolve_selection took 2.1s        (range 2.1–2.2s)
[SEL] broadcast_view_state: new source-clip isolation … forcing frame=0 (discarding position N)
```

where `N` is the frame the test had just seeked to and verified. Zero of the
six show `refresh_playhead_attributes: timed out` — the guard added in
`b73b60c` is holding; the cost moved, it did not go away.

Instrumenting `resolve_and_broadcast_selection` localised the ~2.1s to a single
window, between the `Playlist.playhead_selection changed` log and the
`container=` log. Timers placed on the Pinned-Source-Mode block and the
guid-lookup block did **not** fire, excluding both. The only actor work in the
remaining window is:

```python
self._carry_over_playback_mode(self._viewport_playhead())
```

### Root cause

`_viewport_playhead()` returns `Playhead(conn, ph_actor)`. `Playhead` derives
from `ModuleBase`, whose `__init__`
(`xstudio/python/src/xstudio/api/module.py:198-218`) does:

```python
attr_uuids = request_receive(remote, attribute_uuids_atom())[0]
for attr_uuid in attr_uuids:
    attr_wrapper = ModuleAttribute(connection, remote, uuid=attr_uuid)
    self.attrs_by_name_[attr_wrapper.name] = attr_wrapper   # ← round-trip each
```

`ModuleAttribute.name` is a property performing its own `request_receive`, so
constructing one `Playhead` is ~46 synchronous round-trips — the *same* loop
`refresh_playhead_attributes` performs. `b73b60c` guarded the copy inside
`_carry_over_playback_mode`, but `_viewport_playhead()` is the **argument** to
that call, so Python evaluates it before the guard can decline.

Two aggravating factors make this reliably slow rather than merely wasteful:

1. **It runs on every poll tick regardless of need.**
   `check_and_update_active_playhead` constructs a full `Playhead` (~46
   round-trips, ~1 Hz) purely to read `_remote_key(ph)`, and discards it on
   both the "first sighting" path and whenever `_adopt_playhead` finds the
   playhead unchanged — which is the overwhelmingly common case.
2. **Two threads do it at once against a just-spawned actor.** The poll thread
   and xStudio's callback thread both react to the same new playhead. A
   freshly-spawned playhead actor is still initialising, so the requests
   queue and drain serially rather than in parallel.

### Why the performance fix alone is insufficient

Selection resolution is queued onto `_cmd_queue` and processed
asynchronously. *Any* delay between the selection event and the resolution
running is a window in which a seek can legitimately land. Today
`broadcast_view_state`'s `_new_source_clip` branch resets to frame 0
unconditionally — its docstring explicitly argues the reset must be
unconditional so that the frame moved to and the frame announced agree. That
reasoning is sound about *announcing*, but it assumes the isolation is the
newest intent, which a queued resolution cannot assume.

So the durable fix is ordering, not speed. Speed is still worth fixing: a 2s
poll stall delays every queued command behind it, and leaving it in place
means the race is merely narrowed, and will resurface under load.

## Goals / Non-Goals

**Goals**
- An isolation never discards a position established after its own selection event.
- Identity-only playhead checks stop costing ~46 round-trips.
- Regression coverage that fails on the discarded seek specifically.

**Non-Goals**
- Changing the wire protocol or what peers exchange.
- Changing the intended UX: isolating a clip still opens it at frame 0 and
  loops, in the ordinary no-intervening-seek case.
- Patching xStudio's Python API. `ModuleBase.__init__`'s per-attribute fetch is
  upstream; this change avoids paying it needlessly rather than making it
  cheaper. Batching it upstream is a legitimate larger follow-up.

## Decisions

### Decision 1: Gate the reset on intent ordering, not on a time window

Compare the timestamp of the selection event that caused the isolation against
the timestamp of the last position change. If the position moved after the
selection, keep it. The controller already tracks both
(`_last_selection_change_at` / `_last_source_atom_at`, and
`_last_position_change_at`), so this is a comparison of existing state.

Rejected: a fixed "was a peer driving recently?" wall-clock window. This file's
history records that pattern failing repeatedly for exactly this class of
decision (see the `_new_source_clip` docstring), and it would guess at the
same question the timestamps answer directly.

Preserve the existing invariant in both branches: broadcast whatever position
the isolation settles on, so the frame moved to and the frame announced never
disagree.

### Decision 2: Split actor-handle resolution from wrapper construction

Give `_viewport_playhead()` a cheap sibling that returns the playhead's actor
handle (and thus its identity key) without constructing the wrapper — the
handle is already computed inside `_viewport_playhead()` before it wraps.

- `check_and_update_active_playhead` compares handles and constructs the
  wrapper only when it is actually going to adopt.
- The `_carry_over_playback_mode` call site resolves the handle, applies the
  `b73b60c` dedup guard against it, and constructs the wrapper only if the
  guard passes — hoisting the guard above the expensive argument.

This keeps the wrapper available where attributes are genuinely read/written
(the PSM block, the loop-mode write, the position write).

### Decision 3: Scope out the two rare failures

An auto-play race and a `structural_consensus` mismatch were each observed
once in earlier ad-hoc runs and **zero** times across the 16 measured runs
above. They were previously assumed to be the dominant residual failures; the
data contradicts that. Recorded here so the evidence survives:

- **Auto-play race** — runner reports `"playback still active on xstudio,
  openrv — a seek must park the playhead"`. Note that
  `sync_test/python/sync_test/xstudio_hook.py`'s `set_frame` sets
  `ph.position` but never pauses, while `runner.py:_verify_frame_sync`
  documents that "a seek is supposed to park the playhead" — so the harness's
  seek may simply not satisfy the contract the runner asserts. Additionally
  `broadcast_view_state` asserts `playing=True` on the wire (via
  `playing_override`) without starting local playback, so a peer's echo can be
  what starts playback locally. Untested hypotheses.
- **structural_consensus mismatch** — a `compare_full_states` projection diff
  after `add_media`; the failing run took 57.4s, i.e. it exhausted the bounded
  retry rather than converging late. No diff detail captured.

Both should be reproduced and root-caused on their own evidence rather than
folded in here on a guess.

## Risks / Trade-offs

- **Reduced first-frame resets.** If the ordering check is too permissive,
  isolations stop opening at frame 0 — a visible UX regression. Mitigated by
  the second and third scenarios in the spec delta, which assert the ordinary
  case still resets.
- **Handle-identity comparison must be stable.** `_remote_key` exists because
  raw handles are fresh wrapper objects that never compare equal; the cheap
  path must compare the same normalised string form, or every tick will look
  like a change and re-adopt — the exact churn `check_and_update_active_playhead`
  was built to avoid.
- **Residual flakiness will remain**, because the two scoped-out failures are
  untouched. Success here is the `checkpoint_timeout` signature going to zero,
  not the test reaching 100%.

## Verification

Primary signal is the log signature, not just the pass rate: after the fix, no
run should show `discarding position N` for a non-zero `N` that the test had
just seeked to, and `POLL-SLOW cmd:resolve_selection` should not appear.

Run `cd sync_test && ./run_tests.sh run --test xstudio_selects_script_xstudio`
in a batch of at least 10 (baseline: 5/10 and 1/6 failing). Also re-run
`./run_tests_xstudio.sh` (130 tests) and the sibling selection tests
(`xstudio_selects`, `openrv_hosts_selection`,
`contended_visibility_selection`) to confirm the reset change does not
regress them.
