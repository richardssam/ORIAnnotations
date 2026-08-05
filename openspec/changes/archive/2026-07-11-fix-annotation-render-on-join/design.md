## Context

RV pen strokes applied via sync (live receive, join-time replay, or delta insert) never render, even though they are correctly written into the target `RVPaint` node's properties and correctly resolved to the right node. Root cause, isolated by diffing a natively-drawn session (`good.rv`) against a sync-received one (`bad.rv`) and by probing `rv.commands.metaEvaluateClosestByType` directly against a live RV process:

1. **Wrong property set.** `_pen_spec` (in `otio_sync_core/rv_annotation_codec.py`) omits `startFrame`, `duration`, and (for the non-erase case) `mode` — properties RV's own native pen tool (`annotate/annotate_mode.mu`) always writes and its render pass uses to decide whether a stroke is active on the current frame. `_text_spec`/`_box_shape_spec`/`_arrow_spec` already write these correctly; only the pen path was missed when the schema was migrated (visible from `annotate_mode.mu` using `MetaEvalInfo.frame` for `startFrame` universally). `_apply_annotation` in `annotation_sync.py` separately writes obsolete `hold`/`ghost`/`ghostBefore`/`ghostAfter` properties that RV's current native tool no longer writes for a per-stroke component at all (those names now only exist as session-level `paintEffects` defaults).

2. **Wrong frame-numbering convention.** RV keys a paint node's per-frame bucket (`<node>.frame:<N>`) and each stroke's `startFrame` by the *source's own native frame number* — for embedded-timecode media this is a large absolute number (observed: `96899`), not the small sequence-position/clip-local number (`1`) the sync code currently uses. Confirmed empirically:
   ```
   rv.commands.metaEvaluateClosestByType(1, "RVPaint")
   → [{'node': 'defaultSequence_p_sourceGroup000000', 'nodeType': 'RVPaint', 'frame': 96899}]
   ```
   `_find_paint_node_for_media` (in `annotation_sync.py`) already calls this and already gets the correct `frame` back in the result dict — it just discards everything but `['node']`. Every one of its 8 call sites then reuses its own small "storage frame" for both the RV bucket key and (once defect 1 is fixed) `startFrame`, so even a correctly-shaped stroke lands under a key RV's render pass never looks up.

Both defects are necessary and sufficient on their own to make the stroke invisible; both must be fixed together for strokes to actually render.

## Goals / Non-Goals

**Goals:**
- Pen/erase strokes applied via sync render immediately and correctly, matching a natively-drawn stroke's on-disk property shape.
- `_find_paint_node_for_media`'s callers all key RV paint-node buckets and `startFrame` consistently off the same native frame number RV itself uses.
- Bring `rv_paint_applier._PEN_PROPS` (the stroke-deletion property list) back in sync with the corrected `_pen_spec` output.
- Correct the two spec files (`rv-annotation-codec`, `openrv-sync-plugin`) that currently document the broken behavior as the requirement.

**Non-Goals:**
- No wire-protocol change. `SyncEvent` payloads (`PaintStart`/`PaintPoints`/etc.) are unaffected — this is purely how the *receiving* RV peer materializes them locally into its own paint-node graph.
- No retroactive repair of already-open RV sessions or already-saved `.rv` files that contain strokes written under the old, broken convention before this fix is deployed. A currently-running RV process with stale-keyed strokes stays stale until that content is redrawn/re-synced fresh (e.g. via a rejoin or a fresh delta), which is an accepted limitation, not something this change attempts to migrate in place.
- No change to xStudio. xStudio renders annotations via bookmarks, never RV paint-node properties, so neither defect applies there.
- No change to text/shape/arrow spec builders — they already write the correct property set and (per the call graph) already receive `frame`; only pen was affected by defect 1. Whether they're affected by defect 2 depends only on `_find_paint_node_for_media`'s fix, which is shared by all annotation kinds.

## Decisions

**`_find_paint_node_for_media` returns `(node, native_frame)` instead of just `node`.**
All 8 callers are private to `annotation_sync.py`, so changing the return shape in place (rather than adding a second accessor or an out-parameter) is the smallest, least error-prone change — one call to update per site, and the type checker/tests catch any missed unpacking. `native_frame` is `eval_infos[0]['frame']` when `metaEvaluateClosestByType` found a node; in the pre-existing fallback branch (no sequence in the graph, source-view-only context) there is no sequence-level `MetaEvalInfo` to read a native frame from, so the fallback keeps using the caller-supplied frame as before — a known, unchanged lesser-fidelity path for a context this bug report doesn't cover.

**Every caller uses the returned `native_frame` for the RV-local `frame:N` bucket key, `startFrame`, and any dict key that must stay in lockstep with that bucket** (`_live_stroke_node`, `_partial_pen_nodes_by_key`, `order_prop` lookups in `_cleanup_partial_debris`). These dicts exist to track "the pen node currently occupying this frame's slot for this clip" across ticks of a partial (mid-drag) gesture; if they were keyed by a different frame number than the actual RV bucket the stroke lands in, `_cleanup_partial_debris` would silently fail to find and prune superseded mid-gesture nodes, leaking overlapping stroke fragments. Keeping a single source of truth for "which frame number" (the value returned alongside the node) avoids this class of drift entirely, rather than trying to keep two independently-computed frame numbers in sync by convention.

**`_pen_spec` takes `frame` positionally, matching `_text_spec(stroke, frame)`/`_box_shape_spec(stroke, frame)`/`_arrow_spec(stroke, frame)`**, and the one dispatch call site in `sync_events_to_rv_specs` passes it uniformly for every kind rather than only for kinds that already accepted it. This removes the asymmetry that let pen drift from the other three in the first place.

**`mode` is always written for pen** (`0` normal / `1` erase), not appended conditionally. This matches what `good.rv` shows for a plain color stroke (`mode = 0` present) and removes a second place pen's shape diverged from the rest of the codec's convention (every other kind's boolean-ish flags are always-present, not conditionally omitted).

**Remove the `hold`/`ghost`/`ghostBefore`/`ghostAfter` write in `_apply_annotation` rather than also writing the new properties alongside it.** These properties do not appear anywhere on a native per-stroke pen component in the current RV build (`good.rv` has none of them on the pen item; they only exist as session-level `paintEffects` defaults). Keeping them around as dead per-stroke properties would not break rendering, but perpetuates the false signal that they're part of the live schema, which is exactly how defect 1 went unnoticed.

## Risks / Trade-offs

- **[Risk]** Changing `_find_paint_node_for_media`'s return type could silently break a call site that isn't updated (unpacking a string as a 2-tuple raises immediately, or worse, a stale call site keeps treating the tuple as a node-name string). → **Mitigation**: grep-verified exactly 8 call sites before starting; update all of them in the same change; existing `_log` lines at each site make a missed/wrong unpack visible immediately in `ori_sync_log` during manual verification.
- **[Risk]** The fallback branch of `_find_paint_node_for_media` (no sequence in graph) still uses the small caller-supplied frame, so pen strokes applied in a pure source-view context could still land on a mismatched key. → **Mitigation**: this fallback already existed pre-fix and is not exercised by the join/live-sequence-view scenario this bug report covers; out of scope here, called out explicitly as a non-goal rather than silently left ambiguous.
- **[Trade-off]** No migration path for already-broken strokes in currently-open sessions. → Accepted: the fix is forward-looking; recovering already-invisible strokes in an already-running process is a separate, much narrower concern (re-trigger a full resync/rejoin) that the user can perform manually if needed, and is not required for new annotations (the common case) to work correctly going forward.

## Migration Plan

1. Land the codec/applier/annotation_sync changes together (they are only correct as a set — the property fix alone reproduced no improvement, as verified against `bad.rv` mid-investigation).
2. Rebuild and reinstall the RV plugin package (`rvplugin/ori_sync/makepackage.csh` + `rvpkg -force -install`) so a live RV picks up the change (RV loads the installed package copy, not the repo source).
3. Re-verify against the same `good.rv`/`bad.rv` comparison workflow used during root-causing: draw a stroke on a host, let a second RV instance join, save its session, and diff the pen component's properties and frame-bucket key against a natively-drawn one — they should now match exactly (modulo `uuid`/`user`/coordinates).
4. No rollback complexity beyond a normal revert: this only changes how the receiving peer writes its own local, ephemeral (or session-file-persisted) RV paint properties; it does not change any message on the wire.

## Open Questions

None outstanding — both defects were empirically confirmed (schema diff against a native stroke; live `metaEvaluateClosestByType` probe) before this design was written.
