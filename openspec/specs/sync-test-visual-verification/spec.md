# Sync Test Visual Verification

## Purpose
Ground-truth-driven visual verification of a live app instance's rendered annotation output, layered on top of the `ui-sync-testing` framework's numeric state assertions. Where the numeric `annotation_geometry` check confirms a codec's forward/reverse conversions are internally self-consistent, this capability confirms the *rendered pixels* actually match the geometry the test itself specified — catching codec bugs (like a self-consistent-but-wrong border-width scale) that a numeric-only check cannot.

## Requirements

### Requirement: Live Frame Capture To Image

The system SHALL be able to render a live app instance's current frame (video plus any applied annotations) to an image file on disk, for both OpenRV and xStudio, via a `capture_frame` script-driven command.

For xStudio, capture SHALL resolve the bookmark at the target media/frame and call `OffscreenViewport.render_bookmark_with_transparency` with `include_image=True, include_drawings=True`. For OpenRV, capture SHALL use an in-process grab of the live viewport widget (not an external `rvio` subprocess and not a save/reload round-trip), since the target instance is already running.

#### Scenario: Capturing an xStudio frame
- **WHEN** the runner sends `{"action": "capture_frame", ...}` to an xStudio instance with an annotation present at the current frame
- **THEN** xStudio SHALL render a single composited (video + annotation) image to the requested output path via `render_bookmark_with_transparency`

#### Scenario: Capturing an OpenRV frame
- **WHEN** the runner sends `{"action": "capture_frame", ...}` to an OpenRV instance
- **THEN** OpenRV SHALL grab its live viewport widget in-process and save it to the requested output path, without spawning an external render process

### Requirement: Ground-Truth-Driven Visual Geometry Comparison

The system SHALL be able to verify a captured frame's annotation rendering against the geometry the test itself specified (the `draw_annotation` payload), by projecting that OTIO-normalized geometry into the captured image's own actual pixel resolution (via `coords.otio_to_px`) and sampling a perpendicular cross-section to locate the annotation-colored centroid — the same technique `testchart/compare_testchart.py` uses for its reference-chart geometry, applied to test-owned geometry instead of hardcoded chart positions.

This comparison is additive to, not a replacement for, the existing numeric round-trip check (`annotation_geometry`): the numeric check verifies the codec math is self-consistent; this verifies the *rendered* output actually matches, which the numeric check alone cannot (a codec bug that is self-consistent but wrong, like the rect border-width bug, passes the numeric check and fails this one).

#### Scenario: Rendered border thickness matches expected geometry
- **WHEN** a captured frame contains a shape annotation whose `min`/`max` and expected line-width are known from the test's `draw_annotation` payload
- **THEN** the comparison SHALL project that geometry into the captured image's actual pixel resolution and measure the annotation-colored cross-section thickness at the boundary
- **AND** report the measured thickness and its offset from the expected value

#### Scenario: Comparison does not assume a fixed capture resolution
- **WHEN** two captured images (e.g. from OpenRV and xStudio) have different actual pixel dimensions
- **THEN** the comparison SHALL project expected geometry independently against each image's own actual resolution (read from the image file), not a single assumed resolution shared across captures
