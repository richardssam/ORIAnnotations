## 1. Spike: xStudio visibility apply path

- [x] 1.1 Confirm `self.get_plugin("AnnotationsUI")` returns a handle exposing `get_attribute`/`set_attribute` the same way the viewport object does (`vp.set_attribute("Fit (F)", ...)` in `display_sync.py`); verify `set_attribute("Visibility", bool)` actually toggles `annotations_visible_` and updates the overlay. Confirmed via source trace: `get_plugin()` returns a `PluginBase`, whose `ModuleBase.__init__` eagerly queries `attribute_uuids_atom()` and wraps every existing remote attribute by uuid into `attrs_by_name_` — the identical mechanism `vp.get_attribute(...)` already relies on. Not yet exercised against a live xStudio session.
- [x] 1.2 Not needed — 1.1 confirmed the standard mechanism applies; no fallback required.

## 2. Shared helper: resolve deleted stroke uuids to annotation clips

- [x] 2.1 Added `SyncManager.annotation_clip_guid_for_stroke_uuid(uuid)` in `manager.py`.
- [x] 2.2 Added `SyncManager.surviving_annotation_commands(annotation_clip_guid, deleted_uuids)` in `manager.py`.

## 3. RV: detect and broadcast deletion

- [x] 3.1 Bound `clear-paint` and `clear-all-paint` in `plugin.py`'s `init([...])` event list.
- [x] 3.2 Implemented `AnnotationSyncController.on_clear_paint` in `annotation_sync.py`.
- [x] 3.3 `on_clear_paint` handles both events identically — `clear-all-paint`'s `event.contents()` is the same pipe-joined uuid-only list as `clear-paint` (confirmed from `annotate_mode.mu` source; no node/frame data in the payload), so one handler covers both bindings.
- [x] 3.4 Dedup is automatic: uuids are grouped into a `dict[clip_guid, set[uuid]]` before broadcasting, so uuids from a frame and its `sourceFrame` alias that resolve to the same clip merge into one set and one broadcast; no extra code needed.

## 4. RV: apply an empty replace as a hard clear

- [x] 4.1 Added the empty-payload branch in `_apply_annotation_replace` (wipes `<node>.frame:<frame>.order` directly, bypassing `apply_specs`).
- [x] 4.2 Non-empty reconcile path (text-edit replace, stroke-type exclusion) left unchanged.

## 5. RV: detect and broadcast visibility

- [x] 5.1 Added the `.paint.show` branch in `on_graph_state_change` (immediate broadcast via `self.plugin.display._broadcast_display_state()`, mirroring the existing `channelFlood` branch); `_read_rv_display_state` now includes `annotations_visible`.
- [x] 5.2 `_apply_display_state` now applies `annotations_visible` to every `RVPaint` node's `.paint.show`; already covered by the caller's existing `_rv_updating` guard around the whole `_apply_display_state` call (same as exposure/channel).

## 6. xStudio: detect and broadcast deletion

- [x] 6.1 `on_annotation_event` now reads `data["event"]`; `PaintClear` and any unrecognised event still schedule the existing debounced scan.
- [x] 6.2 Added count-decrease detection in `broadcast_local_bookmark`, rebuilding and broadcasting the full surviving stroke/caption list via `broadcast_replace_annotation_commands`.
- [x] 6.3 No new code needed — the count-decrease branch runs inside the same `broadcast_local_bookmark` the existing `_annotation_flush_retries`/debounce machinery already wraps.

## 7. xStudio: apply an empty replace as a hard clear, and handle visibility events

- [x] 7.1 Added the empty-incoming-commands branch in `refresh_annotation_bookmark` (unconditional `bm.set_annotation(strokes=[], captions=[])`).
- [x] 7.2 Added `HideDrawings`/`ShowDrawings` branch in `on_annotation_event`, broadcasting via `self.plugin.display.poll_and_broadcast_display()` instead of scheduling a bookmark scan.
- [x] 7.3 `self._ann_ui_plugin` is now retained at connect time (`ori_sync_plugin.py`) and used by `display_sync.py`'s `_read_annotations_visible`/`apply_display_state`.

## 8. Verification

- [x] 8.1 Manual test: multiple strokes on one frame in RV, "Clear All Frames" — confirmed clearing correctly after fixing uuid stability (see design.md findings); peers matched.
- [x] 8.2 Manual test: "Clear Frame" in RV with a single annotated frame — confirmed empty on both RV and xStudio peers.
- [x] 8.3 Manual test: "Clear All Frames on Timeline" in RV with multiple annotated clips, multiple strokes per frame — confirmed all clear correctly on peers after the uuid-persistence and uuid-stability fixes; no `sourceFrame`-alias duplicate-broadcast issues observed.
- [x] 8.4 Manual test: Ctrl+D "Delete all strokes" in xStudio — confirmed frame clears on RV via the `on_core_annotation_event` 3-tuple fix + existing count-decrease detection (`strokes 4->0`, `strokes 2->0` observed).
- [x] 8.5 Manual test: toggle "Show Drawings" in RV and 'V' in xStudio — confirmed working in both directions after fixing xStudio's apply path (`action_attribute_`, not the `Visibility` attribute directly) and RV's read path (`metaEvaluateClosestByType` instead of scanning all nodes).
- [x] 8.6 Added `tests/otio_sync/test_manager_annotation_lifecycle.py` covering `annotation_clip_guid_for_stroke_uuid` and `surviving_annotation_commands` (5 tests, passing). Note: no code changes were needed in `rv_paint_applier` itself — the empty-replace fix lives in `annotation_sync._apply_annotation_replace`, which bypasses `apply_specs` entirely for the empty case rather than modifying it.
- [x] 8.7 Updated `docs/openrv_constraints.md` and `docs/xstudio_constraints.md` with the new event bindings and the empty-replace hard-clear convention.
