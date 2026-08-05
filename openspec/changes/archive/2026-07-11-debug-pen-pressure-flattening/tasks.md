## 1. Codec logging

- [x] 1.1 In `python/otio_sync_core/xs_annotation_codec.py::sync_events_to_xs_strokes`, after `current_stroke["thickness"] = ...` is set and the `raw_pts` loop completes (~line 386-400), log the stroke's `uuid`, computed `thickness`, and min/max of the `size_pressure` values just derived.

## 2. Bookmark call-site logging

- [x] 2.1 In `xstudio_plugin/ori_sync/annotation_sync.py::apply_remote_annotation`, log `thickness`/`size_pressure` min-max per stroke immediately before the throttled live-partial `existing_bm.set_annotation(...)` call (~line 1080).
- [x] 2.2 In the same method, log `thickness`/`size_pressure` min-max per stroke immediately before the new-bookmark `bm.set_annotation(...)` call used when a gesture completes and no existing bookmark is cached (~line 1115).
- [x] 2.3 In `xstudio_plugin/ori_sync/annotation_sync.py::refresh_annotation_bookmark`, log `thickness`/`size_pressure` min-max per stroke immediately before its `bm.set_annotation(...)` call (~line 906).
- [x] 2.4 In `xstudio_plugin/ori_sync/annotation_sync.py::load_snapshot_annotations`, log `thickness`/`size_pressure` min-max per stroke immediately before its `bm.set_annotation(...)` call (~line 827), for completeness on the late-join path.

## 3. Verify

- [x] 3.1 Restart xStudio so it reloads the modified plugin (pure Python, loaded directly from `xstudio_plugin/ori_sync/` per `docs/xstudio_constraints.md` — no separate build/install/package step like RV's rvpkg).
- [x] 3.2 Draw a pressure-varying stroke in RV, observe it sync to xStudio, and capture `xstudio_client.log`.
- [x] 3.3 Confirm from the log whether `thickness`/`size_pressure` range is already flat at the codec boundary (task 1.1) or only flattens at one of the `bm.set_annotation` call sites (tasks 2.1-2.4) — this localizes the actual bug for a follow-up fix.

**Result**: Not flat anywhere. `xstudio_client.log` (2026-07-11 09:15 session) shows `size_pressure` min/max spreads of ~0.01-1.0 surviving intact at every logged checkpoint (`sync_events_to_xs_strokes`, `apply_remote_annotation[partial]`, `refresh_annotation_bookmark`) for all three strokes drawn. Combined with the user's visual confirmation that xStudio now renders varying width, the pressure-flattening bug appears to have been a side effect of the earlier duplicate-stroke bug (unstable per-partial UUIDs, fixed prior to this session) rather than a separate defect in the codec or bookmark call sites. No further fix needed here.
