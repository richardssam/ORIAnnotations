# Design

## Context

The proposal recorded the symptom (153 `INSERT_CHILD` for 3 clips, never converging) and
offered a hypothesis — a stale `track_guid` — explicitly flagged as unconfirmed. **That
hypothesis is wrong.** Tracing the code and re-reading the host log with the two paths
interleaved shows a different and simpler cause.

`sync_container` runs two writers against the same state, and each undoes the other. The
host log shows the cycle repeating on a ~70 ms period, in lockstep:

```
20:05:58.199  manager_clips=0 bin_media=2          ← manager's video track reads empty
20:05:58.212  sequence track new media             ← insert clip 1 (+ broadcast INSERT_CHILD)
20:05:58.218  sequence track new media             ← insert clip 2 (+ broadcast INSERT_CHILD)
20:05:58.232  REPLACE_TIMELINE                     ← rebuild from xStudio, register_timeline
20:05:58.270  manager_clips=0 bin_media=2          ← the two inserts are gone
20:05:58.283  sequence track new media             ← …insert them again
20:05:58.290  sequence track new media
20:05:58.303  REPLACE_TIMELINE
```

The two writers:

1. **Incremental reconciliation** — the "Additions (direct track dragging)" block in
   `poll_sequence_new_media` diffs the clips parsed out of `xs_tl.to_otio_string()` against
   the clips on the manager's OTIO video track, and calls `manager.insert_child` for each
   unmatched clip. `patcher.insert_child` appends to `object_map[track_guid]` — the live
   track object — so the insert really does land.

2. **Wholesale rebuild** — the `source_ranges changed` block calls
   `build_single_sequence_otio(playlist, xs_tl)` to construct a *fresh* OTIO timeline from
   xStudio, then `manager.register_timeline(new_otio)`, which does
   `self._timelines[guid] = timeline` and re-indexes. That replaces the timeline object the
   inserts were just written into, so the inserted clips are discarded, and broadcasts
   `REPLACE_TIMELINE`.

Neither is individually wrong; running both in one pass is. The rebuild resets the state
the diff is computed against, so the diff re-reports the same clips on the next pass,
forever. `manager_clips` never settles — 0 → 2 → 5 → 8 → 5 → 3 across the session.

Why it only surfaced now: until `fix-xs-playhead-attribute-subscription`, the timeline
`item_atom` subscription died two events after being created, so `sync_container` ran at
most twice per session. The loop existed but could not spin.

Two prior beliefs to discard:

- `object_map` staleness via `traverse_and_map_preserve`'s `setdefault` is **not** in play
  here — `register_timeline` uses the overwriting `_traverse_and_map`. The preserve variant
  is used on other paths and is out of scope.
- The `Dropped` track naming is a **red herring for this defect**. The reconciliation
  already scans all Video-kind tracks except `Annotations`, so it does see `Dropped` clips.
  Track restructuring is real (`['Video Track','Dropped','Audio Track','Dropped']` →
  `['Dropped']`) but it is a *consequence* of the rebuild churn, not its cause.

## Goals / Non-Goals

**Goals:**

- A `sync_container` pass over an unchanged xStudio timeline broadcasts nothing.
- A clip added once is broadcast once.
- The peer converges on the host's actual sequence contents, including a clip appended to
  the end.
- A non-converging pass is visible in the log as such, not merely as slowness.

**Non-Goals:**

- The bin-additions path in the same function. It fired once, correctly, and is untouched.
- Track-identity redesign, `Dropped`-track handling, or `traverse_and_map_preserve`'s
  `setdefault`. None is the cause here; each may deserve its own change.
- The event subscription itself — `fix-xs-playhead-attribute-subscription` owns it.
- Protocol or message-shape changes. The messages are correct; there are far too many.

## Decisions

### One authority per pass

Within a single `sync_container` execution, structural state SHALL be produced by exactly
one of the two writers, never both. Rationale: the failure is not in either mechanism but
in their interleaving, so the fix belongs at the point that sequences them rather than
inside either.

The rebuild is the stronger authority — it derives the whole timeline from xStudio, which
is the source of truth for what the user actually did — so when a pass determines a rebuild
is warranted, incremental reconciliation for that timeline SHALL be skipped for that pass.

Alternatives considered:

- *Make the rebuild preserve incrementally-inserted clips.* Rejected: it makes correctness
  depend on merging two representations of the same edit, which is what already went wrong.
- *Drop incremental reconciliation entirely and always rebuild.* Attractive for
  simplicity, but a full rebuild plus `REPLACE_TIMELINE` per edit is heavy, and the
  incremental path emits precise `INSERT_CHILD`s that peers apply cheaply. Keep both, order
  them.
- *Debounce/coalesce `sync_container`.* Treats the symptom. A slower loop is still a loop,
  and the peer still diverges.

### Diff against what was last broadcast, not against mutable local state

The incremental diff currently compares xStudio's clips against the manager's track — state
the rebuild is free to replace underneath it. It SHALL instead compare against a record of
what this peer has already broadcast for that timeline, so that re-registering a timeline
cannot make already-sent inserts look unsent.

This is what makes convergence a property of the design rather than of the two paths
happening not to overlap.

### Make a no-op pass observable

Today a converged pass and a looping pass look identical in the log until you count lines
across three minutes. A pass that makes no changes SHALL say so, once, at a bounded rate —
enough that "is it converging?" is answerable from a log tail.

### Addendum: the rebuild-need fingerprint must span all video-kind tracks, not one

Live two-peer testing (2026-08-03) of a direct-drag addition — a clip dragged straight onto
the sequence's xStudio track rather than added via the bin — showed the fixes above reduce
but do not eliminate the loop: `INSERT_CHILD`/`REPLACE_TIMELINE` counts dropped from
153/57 to 41/40 for one edit, and `manager_clips` oscillated 2⟷3 instead of settling. A
second peer receiving the resulting repeated `REPLACE_TIMELINE`s had its media bin balloon
to 64 entries.

Cause: xStudio places a directly-dragged clip on a fresh `Dropped` track rather than the
existing principal track — documented in the proposal, but the "Two prior beliefs to
discard" section below judged this a red herring **for the original defect**, which was
true. It is not a red herring for the fingerprint this design adds: that check compared
only the principal video track (`_sequence_video_track`) on both sides. Once the
incremental path (which already scans all video-kind tracks) folds the `Dropped` clip into
the manager's single track, xStudio's own principal track will never show it — the
fingerprint mismatch has no fixed point, so `poll_sequence_source_ranges` rebuilds forever.
And because `build_single_sequence_otio` parses xStudio's tracks as-is, each rebuild leaves
the *manager's* OTIO multi-track too — so a single-track read on the manager side
reproduces the identical unresolvable mismatch even once xStudio's state stops changing.

Fix: fingerprint across all non-Annotations video-kind tracks (clips and gaps, so trim/
reposition detection is preserved) on **both** sides of every comparison this design
introduces — the rebuild-need check in `poll_sequence_new_media`, the rebuild decision in
`poll_sequence_source_ranges`, and the broadcast-record seed in
`_reset_sequence_broadcast_record` (which must also see clips on the extra track, or the
incremental path treats them as unsent and duplicate-inserts them). The manager's OTIO
mirror normally has exactly one video track, so this is a no-op there in the common case;
it only matters once a rebuild has made it genuinely multi-track.

This narrows, rather than reopens, the original Non-Goal: `Dropped`-track *identity* and
`traverse_and_map_preserve` are still untouched and still out of scope. What's now in scope
is that the convergence fingerprint this change adds cannot assume the sequence collapses
to one track, because nothing in this code path collapses it.

## Risks / Trade-offs

- **Skipping incremental work on a rebuild pass could drop an edit** if a clip is added in
  the same window as a source-range change. Mitigated because the rebuild derives the
  timeline wholesale from xStudio and therefore already contains that clip; the risk is
  really that the peer receives it as `REPLACE_TIMELINE` rather than `INSERT_CHILD`, which
  is a coarser but correct message.
- **Tracking "what was broadcast" adds state that can itself go stale**, and stale state
  here means a genuinely-new clip is never sent — a silent under-broadcast, the harder
  direction to notice. It must be reset wherever the timeline is re-registered or removed,
  and the no-op logging above is what makes the failure visible if it happens.
- **The measurement baseline is not yet trustworthy.** Sequence structure in any sync-test
  recording captured while this loop was running is suspect; verification should be a live
  two-peer session plus a fresh recording, not the existing suite.
- **`sync_container` is a ~250-line function with several interacting paths.** Two attempts
  during this investigation made things worse by changing adjacent code quickly. Changes
  here should be small, and each verified against a live session before the next.
