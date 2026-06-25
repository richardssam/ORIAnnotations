# Proposal: Convert Recording with Annotations

Enable the session recording-to-timeline converter to render drawing strokes and text overlays onto transparent PNG sequences and layer them as a stacked track in the output OTIO timeline.

## Problem Description

The existing `convert_recording_to_timeline.py` tool reconstructs play, pause, scrub, and selection operations on background media, but ignores drawings and text annotations. Reviewers cannot see annotations made during the session when viewing the generated OTIO timeline offline.

## Goals

- **Modular Annotation Builder**: Extract the helper functions from `testchart/generate_testchart.py` into a core utility library `python/otio_sync_core/annotation_builder.py` that programs can use to generate sync events for strokes, shapes, and text. Refactor `generate_testchart.py` to use this library, reducing code duplication.
- **Pillow-Based Annotation Renderer**: Create `sync_recorder/annotation_renderer.py` to draw a sequence of OTIO `SyncEvent` commands (strokes, text annotations, erasers) onto a transparent RGBA PIL Image.
- **Overtime Annotation Alignment**: Parse drawing events (`PaintStart`, `PaintPoint`, `PaintEnd`, `TextAnnotation`) from the JSONL recording, aligning them chronologically with timeline visual segments (even during pause/freeze-frame intervals).
- **Optimized Overlay Output**: Stack an "Annotations Overlay" track containing transparent PNG clips on top of the "Background Media" track. Optimize disk space by only rendering frame-by-frame sequences during active drawing animation, and using stretched static clips or standard `Gap` elements when drawings are unchanging or empty.

## Non-Goals

- Real-time video encoding (we output transparent PNG sequences and let the player/NLE composite them).
- Rendering 3D brush models or complex brush textures (we use Pillow's standard round and soft gaussian brushes).
