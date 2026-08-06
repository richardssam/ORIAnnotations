# pen-pressure-diagnostics

## Purpose

Temporary diagnostic logging that surfaces the computed stroke thickness and per-point pressure range at each stage of the RV→xStudio annotation sync pipeline, so a flattened-width bug can be localized to a specific stage.

## Requirements

### Requirement: Codec logs computed stroke thickness and pressure range
When `xs_annotation_codec.py::sync_events_to_xs_strokes` finishes building a stroke's `points` array from an incoming `PaintPoint` event, it SHALL log the stroke's computed `thickness` and the min/max of the per-point `size_pressure` values it derived, so a caller can confirm whether pressure variation survived the wire-payload-to-stroke-dict conversion.

#### Scenario: Stroke built from a pressure-varying PaintPoint event
- **WHEN** `sync_events_to_xs_strokes` processes a `PaintPoint` event whose `size` array contains varying values
- **THEN** a log line is emitted containing the stroke's computed `thickness` and the min and max `size_pressure` across that stroke's points

### Requirement: Bookmark application logs stroke thickness at each call site
Each call site in `xstudio_plugin/ori_sync/annotation_sync.py` that invokes `bm.set_annotation(strokes=..., ...)` (the throttled live-partial update, the merge-by-uuid update, and `refresh_annotation_bookmark`) SHALL log the `thickness` (and per-point `size_pressure` min/max) of each stroke being passed, so a caller can confirm what data actually reaches the bookmark versus what the codec computed.

#### Scenario: Live-partial update applies a stroke to the bookmark
- **WHEN** a throttled live-partial update calls `bm.set_annotation` with one or more strokes
- **THEN** a log line is emitted per stroke showing its `thickness` and `size_pressure` min/max as passed to that call

#### Scenario: Final merge-by-uuid update applies a stroke to the bookmark
- **WHEN** the merge-by-uuid path (`apply_remote_annotation`) calls `bm.set_annotation` after a gesture completes
- **THEN** a log line is emitted per stroke showing its `thickness` and `size_pressure` min/max as passed to that call

#### Scenario: Bookmark refresh from merged annotation_commands
- **WHEN** `refresh_annotation_bookmark` calls `bm.set_annotation` after re-deriving strokes from a clip's full `annotation_commands`
- **THEN** a log line is emitted per stroke showing its `thickness` and `size_pressure` min/max as passed to that call
