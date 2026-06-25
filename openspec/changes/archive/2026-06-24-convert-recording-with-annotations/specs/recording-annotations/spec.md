## ADDED Requirements

### Requirement: Expose core annotation builder library
A python based library `otio_sync_core.annotation_builder` MUST expose standard coordinate conversion and event generation utilities. The `testchart/generate_testchart.py` tool MUST use this library to generate test alignment drawings.

#### Scenario: Generate drawing stroke events
- **WHEN** a client calls the builder library to create a diagonal stroke of 200px length with a thickness of 4px
- **THEN** the builder outputs valid `PaintStart`, `PaintPoint`, and `PaintEnd` OTIO SyncEvent structures with coordinates scaled relative to the image height

### Requirement: Render strokes and text overlays
The tool MUST render standard stroke, soft gaussian stroke, text annotations, and eraser events to transparent PNG overlays.

#### Scenario: Render eraser stroke
- **WHEN** a SyncEvent stroke with `type = "erase"` is rendered
- **THEN** the pixels along the stroke trajectory are cleared to transparent `(0, 0, 0, 0)` on the RGBA canvas

### Requirement: Layer annotations overlay track in OTIO timeline
The timeline converter MUST stack an annotations overlay track on top of the background media track.

#### Scenario: Optimize output overlays using Gaps and stretched clips
- **WHEN** a session has long periods of no drawings, followed by 3 seconds of active drawing, followed by 5 seconds of static review
- **THEN** the output timeline contains:
  1. A `Gap` for the empty duration
  2. A sequence of 1-frame Clips pointing to animated PNG frames for the 3 seconds of active drawing
  3. A single static Clip stretched for 5 seconds (with a `LinearTimeWarp(0.0)` effect) pointing to the final PNG frame
