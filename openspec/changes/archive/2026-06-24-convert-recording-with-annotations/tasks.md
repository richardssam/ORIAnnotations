# Tasks: Convert Recording with Annotations

## 1. Core Annotation Builder Library

- [x] 1.1 Create `python/otio_sync_core/annotation_builder.py` and populate it with coordinate conversion, line, pressure, bezier, and event creation helpers.
- [x] 1.2 Refactor `testchart/generate_testchart.py` to import and use the new `annotation_builder` module.
- [x] 1.3 Verify that `generate_testchart.py` generates identical outputs to the baseline test charts.

## 2. Pillow-Based Annotation Renderer

- [x] 2.1 Create `sync_recorder/annotation_renderer.py` with drawing algorithms for solid/soft strokes, erasers, and text annotations.
- [x] 2.2 Add unit tests in `tests/otio_sync/test_annotation_renderer.py` validating rendering accuracy (positions, widths, colors, erasers).

## 3. Timeline Annotation Integration

- [x] 3.1 Update the JSONL parser loop in `convert_recording_to_timeline.py` to collect and group drawing events chronologically.
- [x] 3.2 Implement overlay track assembly mapping drawing states into standard Gaps, 1-frame animated clips, and stretched static clips.
- [x] 3.3 Add PNG rendering execution, writing frames to a subfolder based on the output `.otio` file name.
- [x] 3.4 Stack the "Annotations Overlay" track on top of the "Background Media" track in the final assembled timeline.

## 4. Verification & Testing

- [x] 4.1 Create integration tests verifying overlay track construction, segment cuts, and file paths.
- [x] 4.2 Verify the tool with real session recordings containing drawing strokes and text overlays (e.g. `xstudio_selects.jsonl`).
