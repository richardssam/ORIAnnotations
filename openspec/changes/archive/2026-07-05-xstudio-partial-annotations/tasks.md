# Tasks

## 1. xStudio C++ — broadcast live-stroke geometry (amend `pr/annotation-stroke-events`)

- [x] 1.1 In `AnnotationsCore::broadcast_live_stroke` (`src/plugin/viewport_overlay/annotations/src/annotations_core_plugin.cpp`), wrap `anno` in a single `AnnotationBasePtr` first, then serialize via `Annotation::serialise(plugin_uuid)` before/without moving ownership (avoid a second `new`, use-after-move, or leak).
- [x] 1.2 Replace the geometry-less 4-tuple send to `plugin_events_group()` with the 5-tuple `(event_atom_v, annotation_data_atom_v, anno_json, user_id, stroke_completed)`. Keep the existing `live_edit_event_group_` `AnnotationBasePtr` send unchanged.
- [x] 1.3 Gate shapes to pen-up only: when `user_edit_data->item_type` is a shape (`Square`, `Circle`, `Arrow`, `Line`, `Ellipse`), send the `plugin_events_` broadcast **only** when `stroke_completed == true`; suppress mid-drag partials. Pen/Brush/Draw broadcast partials as before.
- [x] 1.4 Amend commit `7d679cc8` on the `pr/annotation-stroke-events` branch (mirrored on `xstudio_sync_fixes`) so the PR lands as the 5-tuple; rebuild the annotations plugin.

## 2. ORI sync plugin (Python) — make JSON path primary, remove hot-scan

- [x] 2.1 In `annotation_sync.py::on_core_annotation_event`, keep the `has_json` branch (`("live_stroke", raw_json)`) as the sole mid-stroke path. Remove the legacy `else` branch that arms hot-scan (`_hot_scan_active`, `_hot_scan_frame`, `("hot_scan", None)`); the geometry-less 4-tuple retains only the `stroke_completed=True` pen-up flush.
- [x] 2.2 Delete `hot_scan_active_annotation` and its poll-loop invocation; remove `_hot_scan_active`, `_hot_scan_frame`, `_hot_scan_last_change`, `_hot_scan_stroke_counts`, `_hot_scan_point_counts`, `HOT_SCAN_TIMEOUT` and related state.
- [x] 2.3 Remove hot-scan trigger/references in `playback_sync.py` and `ori_sync_plugin.py` (poll wiring, `_annotation_pending_time` comments referencing hot-scan). Leave the 30-second fallback safety-net scan intact.
- [x] 2.4 Verify `broadcast_live_stroke_from_json` still resolves the same stable UUID slot after hot-scan removal: `_stroke_uuid_cache`, `_live_stroke_current_key`, and the `clear_live_stroke` command must be untouched and the pen-up flush must reuse the gesture UUID.

## 3. Throttling — coalesce partial broadcasts (~sub-second, not per point)

- [x] 3.1 In `broadcast_live_stroke_from_json`, throttle outbound partial broadcasts to peers using the existing `_last_partial_render_time` scaffolding: broadcast at most once per `PARTIAL_BROADCAST_INTERVAL` (new named constant, default ~0.5 s), keyed by `(clip_guid, local_frame)`.
- [x] 3.2 Always send the final PaintEnd/committed stroke regardless of throttle, so the last state is never dropped by coalescing.

## 4. Verification

- [x] 4.1 xStudio → RV: draw a multi-point pen stroke; confirm the partial appears on the RV peer mid-stroke and updates in place (no duplicate strokes), then matches on pen-up.
- [x] 4.2 xStudio → xStudio: same, between two xStudio peers.
- [ ] 4.3 Shapes: draw a Square/Circle/Arrow/Line; confirm **no** mid-drag partial is broadcast and the shape appears only on pen-up.
- [x] 4.4 No polling: confirm the poll loop performs no per-tick bookmark enumeration during an active gesture (log/inspection); idle vs drawing poll cost is unchanged by partial detection. Verified by code inspection — `hot_scan_active_annotation` and all poll-loop wiring were deleted (task 2.2), not just gated, so no code path exists that could enumerate bookmarks per-tick.
- [x] 4.5 Legacy build: against an xStudio that still sends the 4-tuple, confirm partials are absent but the committed stroke still syncs on pen-up (no error, no hot-scan). Verified via a standalone script feeding a synthetic 4-tuple into `on_core_annotation_event`: mid-stroke queues nothing, pen-up still schedules the flush and queues `clear_live_stroke`, no exception.
- [x] 4.6 Reinstall the rvpkg before RV-side testing (`rvplugin/<pkg>/reinstall.csh`) so RV loads the updated source, not the installed copy.
