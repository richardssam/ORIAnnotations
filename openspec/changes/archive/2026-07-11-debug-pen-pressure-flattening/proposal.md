## Why

Pen-pressure width sync from RV to xStudio was investigated across two sessions (2026-07-10/11). The RV→xStudio duplicate-stroke bug (unstable per-partial UUIDs) is now fixed and confirmed via logs. Despite that fix, xStudio still renders synced strokes at a visually constant width, even though the wire payload's `PaintVertices.size` array demonstrably varies (~78x range across a stroke). Locally-drawn xStudio strokes render pressure correctly, which rules out the shared renderer/shader/`Stroke::from_json` path and narrows the fault to the RV-message → `pen_strokes` dict conversion (`xs_annotation_codec.py::sync_events_to_xs_strokes`) and/or the `bm.set_annotation()` call sites in `annotation_sync.py`. Static review of that code did not surface an obvious bug, so runtime visibility is needed to pinpoint where the pressure variation is lost.

## What Changes

- Add temporary diagnostic logging to `xs_annotation_codec.py::sync_events_to_xs_strokes`: log the computed `thickness` and the min/max of the per-point `size_pressure` values for each stroke it produces.
- Add temporary diagnostic logging to each `bm.set_annotation(strokes=..., ...)` call site in `xstudio_plugin/ori_sync/annotation_sync.py` (the throttled live-partial update, the merge-by-uuid update, and `refresh_annotation_bookmark`): log `thickness` (and size_pressure min/max) for each stroke actually being passed to the bookmark.
- No behavior change — this is observability-only instrumentation to localize an existing bug (codec output vs. merge/cache logic vs. bookmark call) before a real fix is scoped.

## Capabilities

### New Capabilities
- `pen-pressure-diagnostics`: temporary diagnostic logging that surfaces the computed stroke thickness and per-point pressure range at each stage of the RV→xStudio annotation sync pipeline, so a flattened-width bug can be localized to a specific stage.

### Modified Capabilities
(none — no changes to existing sync/protocol behavior; this only adds log statements)

## Impact

- `python/otio_sync_core/xs_annotation_codec.py` (shared codec, used by xStudio plugin)
- `xstudio_plugin/ori_sync/annotation_sync.py` (bookmark application call sites)
- No protocol, schema, or behavioral changes. Logging can be removed or left behind `_log()` (already gated by the plugin's existing debug logger) once the root cause is found.
