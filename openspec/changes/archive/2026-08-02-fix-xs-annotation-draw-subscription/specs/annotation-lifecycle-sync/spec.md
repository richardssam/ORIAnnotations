# annotation-lifecycle-sync (delta)

## MODIFIED Requirements

### Requirement: xStudio detects deletion as a stroke/caption count decrease during the existing poll scan

`broadcast_local_bookmark` SHALL detect local deletion by observing that a bookmark's current stroke or caption count is lower than the count already reflected in the OTIO timeline for that clip/frame, in addition to its existing detection of count increases. On detecting a decrease, it SHALL rebuild the complete surviving stroke/caption list (preserving existing uuids for surviving items) and broadcast it via `broadcast_replace_annotation_commands`, rather than computing a (negative, and therefore empty) delta as it does today.

#### Scenario: Stroke count decrease triggers a replace broadcast

- **WHEN** the poll scan observes a bookmark whose `pen_strokes` count is lower than the count already recorded for that clip/frame in the OTIO timeline
- **THEN** the plugin SHALL broadcast the bookmark's current (smaller) full stroke list via `broadcast_replace_annotation_commands`, reusing existing uuids for surviving strokes

#### Scenario: A local clear is detected from the PaintClear draw interaction

- **WHEN** the user presses Ctrl+D ("Delete all strokes") in xStudio
- **THEN** the plugin SHALL schedule the existing debounced flush scan from the `PaintClear` draw interaction received on AnnotationsCore's draw-events group
- **AND** the subsequent scan's count-decrease detection SHALL be responsible for producing the replace broadcast
