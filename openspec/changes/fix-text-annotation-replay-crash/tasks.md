## 1. Implement the missing duplicate-guard method

- [x] 1.1 Add `_text_uuid_exists_in_rv(self, node, frame, uuid_val)` to `AnnotationSyncController` in `rvplugin/ori_sync/annotation_sync.py`: read `<node>.frame:<frame>.order`, return `True` if any `text:`-prefixed item's `.uuid` property equals `uuid_val`, else `False`. Treat a missing `order` property as "not found" (no exception).

## 2. Fix the call site

- [x] 2.1 In `sequence_sync.py`'s annotation-replay pass, unpack `_find_paint_node_for_media(media_path, frame)`'s `(node, native_frame)` return correctly (currently assigns the tuple to `paint_node` directly).
- [x] 2.2 Use the unpacked `native_frame` (not the loop's `frame`) wherever this specific lookup's result is used for RV-side frame addressing, consistent with `fix-annotation-render-on-join`'s convention.

## 3. Isolate per-event replay failures

- [x] 3.1 Wrap each event's handling inside the `for event in child.metadata["annotation_commands"]:` loop (all branches: text, ellipse, rect, arrow, pen/erase grouping) in its own `try/except Exception`, logging the kind, clip, and error, then continuing to the next event rather than propagating.
- [x] 3.2 Confirm the loop's existing outer structure (per-clip, per-timeline iteration) is unaffected — only individual event handling gains isolation, not the surrounding loops.

## 4. Verify

- [x] 4.1 Reproduce the original crash: via the `sync_test` harness, draw a text annotation on a host, join a second RV, confirm the joiner's plugin log no longer shows `on_synced callback error: ... _text_uuid_exists_in_rv`.
- [x] 4.2 Confirm the joiner's `/state` reflects the text annotation (present in `annotations`), and that playback/display state also applied (not silently skipped).
- [x] 4.3 Confirm a pen-only join (no text annotations) still works unchanged — this bug's line was never reached in that case, so it should be a pure no-op for pen-only sessions.
- [x] 4.4 Rebuild/reinstall the RV plugin package and re-verify against a live join, not just the harness's property-level checks.
