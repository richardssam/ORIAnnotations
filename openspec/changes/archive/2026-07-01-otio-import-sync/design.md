## Context

The OpenRV sync plugin (`rvplugin/ori_sync/`) was built around a flat timeline model: `defaultSequence → RVSourceGroup`, synced via fine-grained per-child patches (`INSERT_CHILD`/`MOVE_CHILD`/`REMOVE_CHILD`/`SET_PROPERTY`) with deterministic per-object guids. When a `.otio` file is imported via File → Import → OTIO, RV's `otio_reader` package instead expands the file (asynchronously, after progressive loading) into a `tracks` RVStackGroup → `Video` RVSequenceGroup whose `Video_sequence.edl` holds the cut points, plus one RVSourceGroup per clip occurrence. The current plugin scans RVSequenceGroups, lands on the empty `defaultSequence` (holding only the blank `otioFile` movieproc placeholder), finds no EDL, and registers an empty timeline — so imported cuts never reach peers.

RV ships a full OTIO reader/writer with registered hooks for annotations, CDL, retimes, and effects. xStudio already syncs OTIO by sharing the raw timeline (`to_otio_string` / `load_otio`). This design makes RV-driven OTIO sync work by leveraging RV's own reader/writer, and routes edits so that the common case (attribute changes on existing clips) stays cheap and annotation-safe while structural edits fall back to a whole-timeline push.

Constraints: must not regress native-timeline sync or the latency-sensitive live annotation delta path; identity must remain guid-keyed so annotations stay bound; behavior is coupled to the `otio_reader` RV package shipping with the target build.

## Goals / Non-Goals

**Goals:**
- OTIO-imported timelines sync to peers with correct cut structure, using RV's `otio_writer.create_timeline_from_node` (export) and `otio_reader.create_rv_node_from_otio` (apply).
- Route OTIO-origin edits by type: topology → whole-OTIO snapshot push; attribute (media swap, cut trim, CDL) → incremental `SET_PROPERTY` patch keyed by clip guid.
- Preserve guid identity across the reader/writer round-trip and across snapshot apply, so attribute patches and annotations remain resolvable.
- Leave native-timeline sync and the live annotation delta path unchanged.

**Non-Goals:**
- Tracking clip reordering within an OTIO timeline as discrete moves (it rides the snapshot push).
- Changing xStudio behavior or the color output-space rules in `color-pipeline-openrv`.
- General-purpose OTIO editing in RV; large re-edits are handled by the brute-force snapshot, not optimized.
- Conflict-free merge of concurrent topology pushes from two peers (last-writer-wins is acceptable for now).

## Decisions

### D1: Leverage RV's `otio_reader`/`otio_writer` rather than hand-rolling EDL translation
RV's `otio_writer.create_timeline_from_node(<tracks stack>)` returns a canonical `otio.schema.Timeline` with correct `source_range`s and — via registered hooks — CDL, retimes, transitions, and annotations. The reader does the inverse. Using these gives future effect fidelity "for free" and keeps RV and xStudio semantically symmetric (both share canonical OTIO).
- *Alternative considered — EDL-aware reconstruction:* read `Video_sequence.edl` (`source`/`in`/`out`) and build clips by hand. Rejected: re-implements RV's writer and forces us to re-solve every future effect (CDL, retime) ourselves. We still understand the EDL (it is how cut-trim is *detected*), but we do not use it to *serialize* structure.

### D2: Route by edit type — topology vs attribute
Snapshotting the whole timeline on every change is too heavy for the common case (a snapshot per paint stroke or per CDL nudge) and would needlessly rebuild peer node graphs. Instead:
- **Topology** (clip insert/remove, large re-edit) → whole-OTIO snapshot push.
- **Attribute on an existing clip** (media swap → `media_reference.target_url`; cut trim → `source_range`; CDL → color channel) → `SET_PROPERTY` patch keyed by clip guid.
- *Alternative — pure snapshot (mirror xStudio exactly):* simpler but loses per-clip annotation binding economy and floods the wire on color/trim nudges. Rejected for the attribute case.
- *Alternative — pure patch (extend the flat model to stacks):* keeps one protocol but requires hand-deriving structure (see D1) and bespoke effect mapping. Rejected.

### D3: Origin detection via the `otio` component on the RVStackGroup root
A timeline is OTIO-origin iff its root is an RVStackGroup produced by `otioFile` expansion, identified by the `otio` component RV's reader stamps on the stack. The most reliable marker is **`otio.timeline_name`**, written by `_create_timeline` whenever the timeline has a name — so it is present even when the source `.otio` carried empty metadata (the common case). `otio.timeline_metadata`/`otio.metadata` appear only when the source objects had non-empty metadata, so detection checks for *any* of these `otio.*` properties, excluding `defaultStack`. Carried forward to peers as `metadata.sync.origin`.
- *Correction from implementation (§2):* the original plan keyed on `.otio.metadata`, but the sample import (`examples/otio_sample.rv`, from an `.otio` with `"metadata": {}`) has **no** `.otio.metadata` — only `otio.timeline_name`. `_add_metadata_to_node` writes the metadata property only `if item.metadata`. Hence detection keys on the `otio` component, primarily `timeline_name`.
- *Alternatives:* sniff the `blank,otioFile=…movieproc` string (only valid pre-expansion, racy — but reused as the *expansion-pending* signal in D8) or read the session filename (absent for File→Import). Rejected as the primary marker.

### D4: Guid round-trip as the identity bridge
The plugin injects `metadata.sync.guid` on timeline/track/clip. RV's reader persists OTIO metadata onto nodes (`_add_metadata_to_node`, including clip-level via `_create_item`), and the writer restores it (`get_node_otio_metadata`). Verified at clip granularity. This lets the snapshot and patch models share one guid scheme so annotations stay bound after a snapshot apply.

### D5: Snapshot change detection via cached-OTIO diff, gated on graph events
Mirror xStudio's `do_broadcast_otio`: keep the last exported OTIO per OTIO-origin timeline; on change, re-export via `create_timeline_from_node`, compare, and push only on difference. To avoid serializing the whole timeline every 33 ms poll tick, gate the re-export on the `rv.sm_view.STACKS`/`SEQUENCES` graph-state-change events (already observed firing on expansion/edit) plus a dirty flag, rather than diffing unconditionally.

### D6: New typed whole-OTIO push message in `otio_sync_core`
Add a dedicated snapshot-push message (built/consumed through one class, per the existing single-definition rule) with wholesale-replace semantics that preserves object guids — distinct from `add_timeline` (which models a new timeline) and from per-child mutations. Receivers apply it by rebuilding nodes via `create_rv_node_from_otio` under the existing `_rv_updating` guard.

### D7: Cut-trim detection via EDL-diff watcher
Cut trim lives in `Video_sequence.edl` (`in`/`out` arrays), not on a source node. A watcher diffs these arrays against a cached baseline; an in/out delta maps (via the EDL `source` index → sequence input order → clip guid) to a `source_range` `SET_PROPERTY` patch on the matching clip. This keeps trim an attribute patch (D2) without a snapshot push.

### D8: Replace retry-on-empty with an expansion-presence wait
The current `_retry_init_timelines` blindly retries after 500 ms. Replace with a check that the `tracks` RVStackGroup (and its sequence) exists before snapshotting an OTIO-origin timeline, keying off the same graph events (D5).

## Risks / Trade-offs

- **Clip metadata attaches to a switch group for multi-rep clips** → For clips with multiple media representations RV builds an RVSwitchGroup, and `_add_metadata_to_node` stamps the item node; guid may sit at a different node level than the single-rep case. → Mitigation: resolve guid at the item/switch-group node consistently in both export and patch-matching; add a round-trip test for a multi-rep clip.
- **Coupling to the `otio_reader` RV package** → entry points are package functions, not a guaranteed-stable RV API; a build without the package breaks OTIO sync. → Mitigation: import-guard and feature-detect; fall back to treating the timeline as native (degraded, no cuts) rather than crashing.
- **Snapshot apply rebuilds peer nodes** → in-flight local annotations or view state on that peer could be disturbed; annotations must re-bind by guid. → Mitigation: apply under `_rv_updating`; re-bind annotations by clip guid after apply; suppress annotation echo during the apply window (existing `_ignore_annotations_until` pattern).
- **Whole-OTIO diff cost on hot path** → serializing a large timeline every tick is expensive. → Mitigation: D5 gating (graph-event + dirty flag), not unconditional per-tick diff.
- **Concurrent topology pushes from two peers** → last-writer-wins can drop one peer's structural edit. → Accepted for now (OTIO timelines are "largely static"); revisit if it bites.
- **Snapshot-apply echo loop** (found in §4) → applying a received snapshot mutates the peer's node graph, sets `_otio_dirty`, and the next poll would re-export and push the same change back. → Mitigation: `apply_otio_snapshot` sets the diff baseline (`_otio_last_export[guid]`) from the peer's *own* re-export of the new nodes, so the self-induced change is not seen as new. Relies on the re-export being stable across round-trip — needs live confirmation.
- **Node teardown on replace** (found in §4) → `apply_otio_snapshot` deletes the prior root via `deleteNode(old_root)` before rebuilding; this may orphan source nodes or disturb view state. → Needs live validation; may need explicit source cleanup.
- **Position-based clip GUIDs** (found in §4) → the same media appears at several cuts, so clip GUIDs derive from `path:track:index:start` rather than path alone. An edit that shifts a clip's index changes its GUID, so annotations rebind by position, not identity. → Acceptable for "largely static" OTIO timelines; revisit if clips are frequently reordered.
- **Relative media paths** (`./encoded/seq_A.mov`) in exported OTIO → peer path resolution must normalize. → Mitigation: route all paths through the existing `_media_path` normalization on both export and apply.
- **Two timelines per import** (`defaultSequence` placeholder + real `Video`) → double registration. → Mitigation: D3 filtering of `defaultSequence`/`defaultStack`.

## Migration Plan

- **Additive and backward-compatible.** `metadata.sync.origin` defaults to native when absent (spec: otio-sync-core), so older peers and native timelines are unaffected.
- **Rollout:** ships behind origin detection; native-timeline code paths are untouched, so the change is inert until an OTIO file is imported.
- **Rollback:** disabling OTIO-origin routing reverts to current behavior — native sync keeps working; OTIO timelines simply lose cut structure again (the pre-change state). No data migration, no schema break.

## Open Questions

- Should snapshot detection be fully event-driven off `graph-state-change` (`STACKS`/`SEQUENCES`) instead of poll-gated, to eliminate the dirty-flag bookkeeping? (Perf vs. simplicity.)
- Where exactly does the snapshot-push message sit relative to `add_timeline`/`remove_timeline` in dispatch — is "replace if exists else create" the right apply semantics?

### Resolved by source-read spike (§1; pending live RV confirmation)

- **`create_rv_node_from_otio` setup (1.2):** No manual hook/context init required. `set_context` is an internal recursion contextmanager. Apply path = `commands.addSourceBegin()`; `create_rv_node_from_otio(timeline, {"otio_file": <path-or-None>})`; `commands.addSourceEnd()` — mirroring `read_otio_file`. Hooks are globally registered by the `otio_reader` package. Pass `otio_file` where available so relative-path hooks resolve media. **D6 confirmed.**
- **Guid round-trip incl. multi-rep (1.3):** Timeline metadata round-trips via `<stack>.otio.timeline_metadata` (`_create_timeline` → writer `get_node_otio_metadata(root, "timeline_metadata")`). Clip metadata round-trips via `_add_metadata_to_node(it, src_or_switch_group)` in reader `_create_item`, read back by writer `_create_item` for both RVSourceGroup and RVSwitchGroup. **D4 confirmed at timeline + clip granularity.** Live test still warranted for the multi-rep active-rep edge.
- **source_range ↔ EDL fidelity (1.1):** `_set_sequence_edl` writes `edl.source = range(len(frame))`, `in/out` terminated by `0`; the cut uses the OTIO `trimmed_range`. Matches `examples/otio_sample.rv`. **D1/D7 confirmed.**

## Live-debugging findings (RV↔RV and RV↔xStudio)

Hard-won discoveries from running the real apps with the `otio_import_rv_to_rv` /
`otio_import_rv_to_xstudio` tests on `examples/otio_sample.rv` (the imported
`otio_test_quicktime.otio`: 20 one-frame cuts, media `seq_{A,B,C,D}.mov` each
spanning embedded-timecode frames 100–119, so each file appears at **5**
positions). These are the cross-cutting facts any continuation thread needs.

### The node graph an OTIO import produces
- Import (File→Import→OTIO, or `examples/otio_sample.rv`) expands to:
  `tracks : RVStackGroup` → `Video : RVSequenceGroup` (+ `Video_sequence : RVSequence` holding the EDL) → 20 `sourceGroupNNNNNN : RVSourceGroup`, each wrapped in a `sourceGroupNNNNNN_switchGroup : RVSwitchGroup`, plus per-slot paint nodes `Video_p_sourceGroupNNNNNN_switchGroup : RVPaint`.
- RV's default `defaultSequence`/`defaultStack` and a blank `blank,otioFile=…movieproc` placeholder source also exist — all filtered out (D3, §2.5).

### Frame base is `frameStart()`, never a hardcoded 1 — the big one
- The wire protocol's `current_time.value` is a **0-based offset into the current view**. Both plugins already agree (xStudio sends `playhead.position`; RV historically sent `frame - 1`). There is **no ±1 protocol bug** — do not "fix" one.
- The `- 1` was only ever correct because the *native* test media is `_notc` (no timecode) so its views start at frame 1. The correct, universal base is **`rv.commands.frameStart()`** of the current view:
  - native no-timecode source/sequence → `frameStart()==1` (unchanged behavior),
  - OTIO-imported sequence (EDL `frame` starts at 0) → `frameStart()==0`,
  - timecode source view (`seq_D.mov`, frames 100–119) → `frameStart()==100`.
- Implemented as `PlaybackSyncController._frame_base()`; used symmetrically on send (`value = frame - base`) and receive (`frame = value + base`). On receive, base is read **after** the view switch so `frameStart()` reflects the target view.

### `global_start_time` must be stripped on export
- RV's `otio_writer` sets `timeline.global_start_time = RationalTime(first_clip_in_point, fps)` (e.g. 100). xStudio honors it and labels its playhead from 100, producing a ~100-frame offset. `_stamp_sync_identity` sets `tl.global_start_time = None`; the RV reader treats `None` as `RationalTime(0)` (otio_reader.py:224). `otio_compare` ignores it (compares cut structure only).

### Sequence-mode selection must NOT seek by clip guid
- A single media file appears at many cut positions, so a clip guid can't identify a unique frame. In sequence mode `_apply_selection` only does `setViewNode` (for annotation context); the **playback message owns the frame**. Seeking by guid-derived position jumps to the wrong occurrence.

### Echo / duplicate-node hazards (all fixed, keep in mind)
- `check_otio_snapshots` is **master-only** (`is_master` gate). A peer that re-broadcasts received OTIO causes the master to rebuild, accumulating `Video`, `Video000002`, … and doubling source groups.
- `apply_otio_snapshot` deletes the prior root **and its child RVSequenceGroups** before rebuilding (orphan cleanup), and sets `_otio_last_export` from its own re-export to avoid echoing its own graph mutation.
- Fixture app needs a longer spawner startup delay (5 s) so it wins the 2 s master-discovery race; otherwise the empty peer becomes master.

### OPEN PROBLEM — annotation binding for OTIO cut-sequences (unsolved)
This is the main remaining work; "no Annotations track" was fixed (a logical
`Annotations` track is now stamped onto the OTIO timeline and stripped before
`create_rv_node_from_otio`), but annotations still do not land correctly. Two
root causes, both stemming from repeated-media + timecode:

1. **Wrong occurrence.** `playback._clip_guid_for_media_path(media_path)` returns the *first* clip with that media. A stroke on `Video_p_sourceGroup000011_switchGroup` (a specific occurrence) binds to the first `seq_C` clip, not the painted one.
2. **Wrong clip-local frame.** `_broadcast_annotation` does `otio_frame = frame - 1`, assuming source media starts at frame 1. The paint component frame is the **media frame** (e.g. 110), and the clip's `source_range.start` is also 110, so clip-local should be `110 - source_range.start = 0`, not `109`.

**Proposed fix (not yet implemented):** resolve the clip by **(media_path AND media_frame)** — the clip whose `source_range` covers the painted media frame uniquely identifies both the occurrence (issue 1) and the correct `source_range.start` to compute clip-local frame `= media_frame - source_range.start` (issue 2). Add e.g. `_clip_guid_for_media_and_frame(media_path, media_frame)` and use it in the annotation send path; keep the existing `_clip_guid_for_media_path` for native (single-occurrence) callers, or make the new resolver fall back to it. Mirror on the RV receive/render side (`_find_paint_node_for_media` already locates by sequence frame, so verify its `seq_frame` math once the send side is correct). Must not regress native annotations (notc media, single occurrence, `source_range.start==0`).

### Where things live (orientation for a fresh thread)
- OTIO snapshot export/apply, identity stamping, annotation-track stamping: `rvplugin/ori_sync/sequence_sync.py` (`_stamp_sync_identity`, `_export_otio_stack`, `check_otio_snapshots`, `apply_otio_snapshot`, `_replay_otio_annotations`, plus the `_is_otio_*`/`_otio_*` helpers).
- Frame base + selection/seek: `rvplugin/ori_sync/playback_sync.py` (`_frame_base`, `_apply_playback`, `_apply_selection`).
- Annotation send/receive + paint-node resolution: `rvplugin/ori_sync/annotation_sync.py` (`_resolve_media_path_for_paint_node`, `_broadcast_annotation`, `_find_paint_node_for_media`).
- Protocol message + origin marker: `python/otio_sync_core/protocol_messages.py`; dispatch/handlers: `python/otio_sync_core/manager.py`.
- Test harness: `sync_test/python/sync_test/{runner,spawner,openrv_hook,xstudio_hook,otio_compare}.py`; tests `otio_import_rv_to_rv` / `otio_import_rv_to_xstudio` in `sync_test/sync_tests.yaml`; comparison unit tests `tests/otio_sync/test_otio_compare.py`.
