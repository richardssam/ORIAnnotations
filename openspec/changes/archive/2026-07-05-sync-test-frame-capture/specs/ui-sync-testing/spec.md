## ADDED Requirements

### Requirement: Script-Driven Frame Capture
The system SHALL support a `capture_frame` script-driven command, available for both OpenRV and xStudio driver/peer apps, that renders the target app's current live frame (video plus applied annotations) to an image file at a caller-specified output path.

#### Scenario: Capturing a peer's rendered frame after a draw_annotation converges
- **WHEN** the runner sends `{"action": "capture_frame", "output_path": ..., ...}` to an app after a prior `draw_annotation` has converged
- **THEN** the app SHALL render its current frame, including the annotation, to the requested output path
