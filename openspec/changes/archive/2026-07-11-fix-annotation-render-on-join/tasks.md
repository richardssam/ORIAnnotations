## 1. Codec: pen spec property set

- [x] 1.1 `_pen_spec` in `otio_sync_core/rv_annotation_codec.py` takes a `frame` parameter and always writes `mode` (0/1), plus `startFrame`, `duration`, `softDeleted`, matching `_text_spec`/`_box_shape_spec`/`_arrow_spec`.
- [x] 1.2 `sync_events_to_rv_specs`'s pen/erase dispatch branch passes `frame` into `_pen_spec` like the other three kinds.
- [x] 1.3 Update `sync_events_to_rv_specs`'s docstring (`ctx["frame"]` note) to say pen/text/shape, not just text/shape.
- [x] 1.4 Remove the obsolete `hold`/`ghost`/`ghostBefore`/`ghostAfter` manual property write in `annotation_sync.py::_apply_annotation`, and its now-stale docstring paragraph.

## 2. Applier: stroke-deletion property list

- [x] 2.1 Update `_PEN_PROPS` in `otio_sync_core/rv_paint_applier.py` to the corrected set (`brush, color, debug, join, cap, splat, mode, width, points, uuid, startFrame, duration, softDeleted`), dropping `hold`/`ghost`/`ghostBefore`/`ghostAfter`.
- [x] 2.2 Update the comment above `_PEN_PROPS` that lists where each property comes from, so it no longer references the removed `hold`/`ghost`/`ghostBefore`/`ghostAfter` write site.

## 3. Native frame plumbing

- [x] 3.1 Change `_find_paint_node_for_media` in `annotation_sync.py` to return `(node, native_frame)`, where `native_frame` is `eval_infos[0]['frame']` when `metaEvaluateClosestByType` resolves a node, else the caller-supplied `frame` (unchanged fallback behavior for the no-sequence-in-graph case).
- [x] 3.2 Update `_apply_annotation` to unpack the tuple and use `native_frame` for the `apply_specs(..., frame=...)` call and the `order_prop`/pen-lookup that follows it.
- [x] 3.3 Update `_apply_text_annotation` the same way.
- [x] 3.4 Update `_apply_shape_annotation` the same way.
- [x] 3.5 Update `_apply_partial_annotation` (`node = self._find_paint_node_for_media(...)` call) the same way, and thread `native_frame` (not the locally-computed `rv_frame`) into the `key = (clip_guid, rv_frame)` used for `_live_stroke_node`/`_partial_pen_nodes_by_key`, and into the nested `_apply_annotation` payload's `"frame"` field.
- [x] 3.6 Update `_finalize_pen_stroke_events` (`node = paint_node_cache or self._find_paint_node_for_media(...)`) the same way, threading `native_frame` into its own `_apply_annotation` payload and into `_cleanup_partial_debris`'s `rv_frame` argument.
- [x] 3.7 Update `_apply_annotation_render` and `_apply_annotation_replace` the same way.
- [x] 3.8 Audit every other read of the resolved frame within these methods (e.g. `_cleanup_partial_debris`'s `order_prop = f"{node}.frame:{rv_frame}.order"`) to confirm it's now fed the same `native_frame` used to write the property, not the original clip-local value. (`_import_existing_rv_annotations` at annotation_sync.py:~204 has an unrelated pre-existing frame-range assumption on the SEND/local-import side — out of scope for this change, not touched.)

## 4. Verify

- [x] 4.1 Run `tests/otio_sync/test_rv_annotation_codec.py` and `tests/otio_sync/test_rv_paint_applier.py` (python3.10 or 3.11 — 3.14 lacks pytest; note two pre-existing unrelated failures, text font-size and ellipse border-width, are not introduced by this change). Result: 26 passed, 2 pre-existing failures (unchanged), 0 new failures.
- [x] 4.2 Rebuild and reinstall the RV plugin package (`rvplugin/ori_sync/makepackage.csh`, then `rvpkg -force -remove/-add/-install/-optin`) against the `openrv_annotations` custom build so a live RV picks up the change.
- [x] 4.3 Repeat the `good.rv`/`bad.rv` comparison: draw a stroke on a host, let a second RV instance join, save its session, and diff the pen component's properties and `frame:<N>` key against a natively-drawn stroke in the same session — they should match (modulo `uuid`/`user`/coordinates), and the earlier `startFrame = 1` vs `startFrame = 96899` mismatch should be gone. Result: exact match, `startFrame = 96899` / `frame:96899` bucket, in both a single-clip and the original 8-clip reproduction.
- [x] 4.4 Visually confirm via a fresh RV process loading the saved joiner session (headless frame grab or interactive) that the stroke actually renders, not just that its properties are correct on disk. Result: user confirmed fixed via live manual join test.
- [x] 4.5 Spot-check a text and a shape annotation through the same join scenario to confirm the native-frame plumbing fix (which they share via `_find_paint_node_for_media`) didn't regress kinds that were already rendering correctly. Result: surfaced a separate, pre-existing crash (`_text_uuid_exists_in_rv` missing method) unrelated to this change's fix — tracked and scoped in `fix-text-annotation-replay-crash`, not fixed here.
