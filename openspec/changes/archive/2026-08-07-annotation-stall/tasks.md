## 1. Shared deletion helper

- [x] 1.1 In `python/otio_sync_core/rv_paint_applier.py`, add per-kind property-name lists (`pen`/`erase` sharing one list, `text`, `ellipse`/`rect` sharing the box-shape list, `arrow`), sourced from `rv_annotation_codec.py`'s `_pen_spec`, `_text_spec`, `_box_shape_spec`, and `_arrow_spec` property tuples, plus the pen-only extras set outside the codec in `annotation_sync._apply_annotation` (`uuid`, `hold`, `ghost`, `ghostBefore`, `ghostAfter`). Add a comment pointing back at those codec functions so the lists don't drift silently.
- [x] 1.2 Add `_delete_item_properties(commands, rv_node: str, item: str) -> None` in the same file: derive the item's kind-prefix (`item.split(":", 1)[0]`), look up its property-name list, and call `commands.deleteProperty(f"{rv_node}.{item}.{prop}")` for each, wrapped in try/except to absorb already-deleted properties (mirroring OpenRV's own `annotate_mode.mu::deleteStroke` pattern).

## 2. Wire into reconcile-mode pruning

- [x] 2.1 In `_apply_reconcile`'s prune step (~line 205-217), call `_delete_item_properties(commands, rv_node, item)` for each item being pruned, before it's dropped from the rebuilt `order` list.

## 3. Wire into partial-tick cleanup

- [x] 3.1 In `rvplugin/ori_sync/annotation_sync.py::_cleanup_partial_debris` (~line 339), import `_delete_item_properties` from `otio_sync_core.rv_paint_applier` and call it for each stale item being swept out of `order`, before the pruned `order` list is written back.

## 4. Verify

- [x] 4.1 Reinstall the rvpkg (`rvplugin/<pkg>/reinstall.csh`) so RV loads the updated source, not the installed copy.
- [x] 4.2 In a single long-running RV process, draw two annotations on the same clip+frame from xStudio (same scenario that originally measured a 34ms first-stroke apply vs. 13.51s second-stroke apply) and capture `rv_client.log`.
- [x] 4.3 Confirm from the log timestamps that the second annotation's `apply_patch` now runs promptly after the raw MQ message is logged (no multi-second gap), closing the stall.
- [x] 4.4 Spot-check that deleted properties are actually gone (not just unreferenced) — e.g. via `rv.commands.propertyExists()` on a swept item's `.width`/`.points` paths in RV's Python console after a partial tick is superseded — to confirm the fix closes the leak, not just coincidentally speeds up this one test.
