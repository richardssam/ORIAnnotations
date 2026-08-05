## Why

A late-joining RV client (and, more generally, any RV peer receiving a pen/erase stroke over sync — live or replayed from a join snapshot) never displays the stroke, even though the underlying annotation data is transmitted and written into RV's paint-node property graph correctly. Root-caused via a byte-for-byte diff of a natively-drawn RV session (`good.rv`) against a sync-received one (`bad.rv`): the sync codec writes pen strokes with the wrong property set (missing `startFrame`/`duration`/`softDeleted`, and always-conditionally-omitted `mode`; carrying obsolete `hold`/`ghost`/`ghostBefore`/`ghostAfter` fields RV's current native pen tool no longer writes at all) and, separately, keys the stroke's `frame:N` bucket and its `startFrame` value using the wrong frame-numbering convention (small sequence/clip-local frame instead of the large absolute per-source frame `rv.commands.metaEvaluateClosestByType` already returns and RV's renderer actually looks up against). Both defects independently prevent the stroke from ever being picked up by RV's render pass.

## What Changes

- `otio_sync_core.rv_annotation_codec._pen_spec` gains a `frame` parameter (mirroring `_text_spec`/`_box_shape_spec`/`_arrow_spec`) and now writes `startFrame`, `duration`, and `mode` (unconditionally, not just for erase) and `softDeleted`, matching the property set RV's native pen tool writes.
- `rvplugin/ori_sync/annotation_sync.py::_find_paint_node_for_media` returns the native/absolute frame number `metaEvaluateClosestByType` reports for the resolved paint node (alongside the node name), instead of discarding it.
- All 8 call sites in `annotation_sync.py` that resolve a paint node for a pen/text/shape write (`_apply_annotation`, `_apply_text_annotation`, `_apply_shape_annotation`, `_apply_partial_annotation`, `_finalize_pen_stroke_events`, `_apply_annotation_render`, `_apply_annotation_replace`, and partial-stroke cleanup tracking) use this native frame — not the small clip-local "storage frame" — for the RV `frame:N` bucket key, the `startFrame` property, and the live-stroke/cleanup dictionary keys that must stay consistent with it.
- `_apply_annotation`'s obsolete manual write of `hold`/`ghost`/`ghostBefore`/`ghostAfter` is removed.
- `otio_sync_core.rv_paint_applier._PEN_PROPS` (used by `_delete_item_properties` when pruning/reconciling a removed stroke) is updated to the new property set so stroke deletion doesn't leave orphaned `startFrame`/`duration`/`softDeleted` properties behind.
- `openspec/specs/rv-annotation-codec/spec.md`'s "Property superset per kind" scenario, which currently documents the old (broken) pen property set as the requirement, is corrected.
- `openspec/specs/openrv-sync-plugin/spec.md`'s "Applying flat view stroke" scenario is strengthened to require that the translated stroke actually renders (uses RV's own native paint-node addressing), not just that properties are written.

## Capabilities

### New Capabilities

(none — this is a correctness fix to existing capabilities)

### Modified Capabilities

- `rv-annotation-codec`: pen/erase `PaintNodeSpec` property superset changes to include `startFrame`, `duration`, `mode` (always), `softDeleted`, dropping the implied `hold`/`ghost`/`ghostBefore`/`ghostAfter` fields the old requirement text never actually listed but the implementation wrote.
- `openrv-sync-plugin`: "Applying flat view stroke" scenario under Synchronized Annotations now requires the applied stroke to use RV's native per-source frame addressing so it is actually rendered, not merely written to the property graph.

## Impact

- `python/otio_sync_core/rv_annotation_codec.py` (`_pen_spec`, `sync_events_to_rv_specs` call site, docstring)
- `python/otio_sync_core/rv_paint_applier.py` (`_PEN_PROPS`)
- `rvplugin/ori_sync/annotation_sync.py` (`_find_paint_node_for_media` and its 8 call sites)
- `openspec/specs/rv-annotation-codec/spec.md`, `openspec/specs/openrv-sync-plugin/spec.md`
- No wire-protocol change: `SyncEvent` payloads are unaffected: this is purely how the receiving RV peer applies them locally.
- No xStudio-side change: xStudio renders annotations via bookmarks, not RV paint-node properties, so it is unaffected by either defect.
