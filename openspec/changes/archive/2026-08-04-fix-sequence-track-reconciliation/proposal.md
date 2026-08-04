# fix-sequence-track-reconciliation

## Why

The xStudio plugin's drag-path sequence reconciliation never converges. It broadcasts
`INSERT_CHILD` for clips it has already inserted, on every poll, indefinitely — so the
peer's copy of a sequence is wrong, both sessions are slow and stuttery, and each peer's
media bin grows clips nobody added.

Measured on a two-xStudio session, 2026-08-03 20:05–20:08, with three clips in one
sequence:

| | |
|---|---|
| `INSERT_CHILD` broadcast by the host | **153** |
| …from the drag path (`sequence track new media`) | 152 |
| …from the bin path (`sequence new media`) | 1 |
| `REPLACE_TIMELINE` broadcast | 57 |
| `build_single_sequence_otio` rebuilds | 58 |
| plugin log size (was ~90 KB for a comparable session) | **2.6 MB** |

The reconciliation compares the clips read back from the xStudio timeline against the
clips on the manager's OTIO video track, and inserts whatever does not match. It never
converges because the insert does not appear in what it re-reads:

```
[2F] track path entry: tl=... manager_clips=0 bin_media=2     ×15 consecutive polls
[2F] track path entry: tl=... manager_clips=2 bin_media=3     × 8
[2F] track path entry: tl=... manager_clips=5 bin_media=7     ×18
[2F] track path entry: tl=... manager_clips=8 bin_media=7     × 2
```

Fifteen consecutive polls read the manager's video track back as **empty** immediately
after inserting into it, then insert the same clips again. `manager_clips` never settles
at the real clip count; it oscillates 0 → 2 → 5 → 8 → 5 → 3.

The mechanism is established in design.md — and it is **not** the stale-`track_guid` theory
this proposal originally carried. `sync_container` runs two writers against the same state
in one pass, each undoing the other, on a ~70 ms cycle:

```
manager_clips=0             ← manager's video track reads empty
sequence track new media    ← incremental reconciliation inserts + broadcasts INSERT_CHILD
sequence track new media
REPLACE_TIMELINE            ← wholesale rebuild re-registers the timeline, discarding them
manager_clips=0             ← …and the next pass re-inserts the same clips
```

The rebuild replaces the timeline object the inserts were written into, so the diff that
drives the inserts is computed against state the rebuild keeps resetting. Neither path is
wrong alone; running both in one pass cannot converge.

Three user-visible symptoms all reduce to this one defect:

- **The peer's sequence is stale or wrong.** It receives 153 contradictory inserts plus 57
  whole-timeline replacements and settles on an old state — a clip added at the end of the
  sequence never appears.
- **Both sessions stutter.** 153 inserts, 57 timeline replacements and 58 sequence rebuilds
  in three minutes, each round-trip re-triggering `item_atom` and the next rebuild.
- **The bin grows clips nobody added.** `bin_media` climbs 2 → 3 → 7 as the echoed inserts
  are applied; the peer's bin was observed holding 4 clips where 2 were expected.

Why now: this is pre-existing but was entirely invisible. Until
`fix-xs-playhead-attribute-subscription`, the timeline `item_atom` subscription went dead
two events after it was created, so this loop ran at most twice and looked like a
one-clip-behind sequence. With event delivery repaired, it runs continuously and is now
the dominant failure in structural sync.

## What Changes

- Sequence reconciliation SHALL converge: re-running it against an unchanged xStudio
  timeline SHALL broadcast nothing. Today it re-broadcasts the same insert forever.
- An insert SHALL be observable in the state the next reconciliation pass reads, so a clip
  is broadcast once rather than once per poll.
- Track identity SHALL survive xStudio restructuring the sequence's tracks. Reconciliation
  SHALL NOT depend on a track's name or ordinal position, which change when xStudio adds a
  `Dropped` track for drag-dropped media or collapses tracks afterwards.
- Reconciliation SHALL be observable: a bounded, greppable signal that a pass made no
  changes, so a non-converging loop is visible in a log rather than only as "it feels slow".

Not in scope: the annotation and playback paths, and the event subscription itself
(`fix-xs-playhead-attribute-subscription` owns that). No protocol or message-shape change
is anticipated — the messages are correct, there are simply far too many of them.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `xstudio-event-sync`: its "Event-Driven Sequence Mutation Sync" requirement says the
  plugin syncs timeline edits by subscribing to container events, and that a structural
  edit queues the corresponding OTIO mutation. It says nothing about **convergence** — so
  broadcasting the same insert 153 times for one clip satisfies the requirement as written.
  That silence is what let this regress undetected. The requirement gains an explicit
  idempotency and track-identity obligation.

## Impact

- `xstudio_plugin/ori_sync/structure_sync.py` — `poll_sequence_new_media`: the
  "Additions (direct track dragging)" reconciliation, the `source_ranges changed` rebuild,
  and the ordering between them. The bin-additions path in the same function is **not**
  implicated: it fired once, correctly.
- No change expected in `otio_sync_core` — `patcher.insert_child` and
  `manager.register_timeline` each behave correctly in isolation.
- No RV-side impact.
- **Test evidence**: any sync-test recording or result captured while this loop was running
  is untrustworthy for sequence structure. Recordings made before
  `fix-xs-playhead-attribute-subscription` are unaffected by *this* loop (it could not run),
  but are void for position for the reason that change documents.
