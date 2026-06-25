# Design: Convert Recording with Annotations

## Architecture Overview

```text
 JSONL Recording
      │
      ▼
  [Parser] ─────────────► [Visual Segment Builder]
      │                               │
      │ (drawing events)              │ (media cuts)
      ▼                               ▼
 [Renderer] ──────────────────► [Timeline Assembler]
      │                               │
      ▼                               ▼
 PNG Overlays                  OTIO Timeline
(output_dir/frame_*.png)       (Background Track + Overlay Track)
```

## Proposed Components

### 1. Annotation Builder Library (`python/otio_sync_core/annotation_builder.py`)

Extract standard vector generators from `testchart/generate_testchart.py`:
- `px_to_norm` / `norm_to_px`: Normalized/height coordinate converters.
- `line_pts`: Evenly-spaced points along a line.
- `pressure_sizes`: Symmetrical swell/taper width scaling.
- `make_stroke`: Build `PaintStart.1`, `PaintPoint.1`, `PaintEnd.1` events.
- `make_text`: Build `TextAnnotation.1` events.

Refactor `generate_testchart.py` to import and use this builder library.

### 2. Pillow-Based Renderer (`sync_recorder/annotation_renderer.py`)

A standalone module that takes a target resolution and a list of `SyncEvent` command dictionaries/objects, and returns a transparent RGBA PIL Image.

- **Solid Brush Strokes**: Rendered as a sequence of connected lines with rounded joint/cap ellipses based on point coordinates and thicknesses.
- **Soft/Gaussian Brush Strokes**: Rendered by creating an L-mode mask, drawing lines, applying `ImageFilter.GaussianBlur`, and compositing with the color.
- **Erasers**: Represented by drawing transparent `(0, 0, 0, 0)` colors directly onto the RGBA canvas.
- **Text Annotations**: Drawn using `ImageDraw.text` with loaded standard fonts (e.g. Helvetica) scaled to match the OTIO `font_size` (normalized by height).

### 3. Chronological Mapping & Assembler (`sync_recorder/convert_recording_to_timeline.py`)

Extend the parser to support drawing events:
- Maintain a list of committed stroke/text events on the active timeline/clips.
- Map the wall-clock time `t` of drawing events to segment-relative frames.
- For each frame of the output timeline, compile the state of drawings (including any partial in-progress strokes) and render it to a transparent PNG.
- Save PNG frames into an output directory (e.g., `[output_path_basename]_annotations/`).
- Create an `Annotations Overlay` track stacked on top of the `Background Media` track in the output OTIO timeline:
  - Empty periods -> `Gap`
  - Static drawings -> 1 stretched `Clip` pointing to a single PNG with a `LinearTimeWarp(0.0)` effect.
  - Active drawing animations -> Sequence of 1-frame `Clips` pointing to the individual PNG frames.
