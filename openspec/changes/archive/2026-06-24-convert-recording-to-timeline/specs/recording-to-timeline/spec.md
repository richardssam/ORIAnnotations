## ADDED Requirements

### Requirement: Convert JSONL session recording to OTIO timeline structure
The tool MUST parse a JSONL session recording containing `STATE_SNAPSHOT`, `SelectionSet`, and `PlaybackSettingsSet` events and output a valid `.otio` timeline.

#### Scenario: Parse snapshot and write background track
- **WHEN** the CLI tool is executed with a valid session recording containing a `STATE_SNAPSHOT`
- **THEN** the output OTIO timeline contains a "Background Media" track with clips populated from the snapshot's timelines

#### Scenario: Map playback changes to OTIO timeline cuts
- **WHEN** the user plays or pauses during the recorded session
- **THEN** the output timeline splits the background media clip at the wall-clock boundaries, mapping normal playback segments to moving source ranges, and pause/scrub segments to freeze-frame clips

### Requirement: Freeze-frame representation
The tool MUST represent pause and scrub segments using 1-frame source clips stretched to the segment's wall-clock duration with a `LinearTimeWarp(time_scalar=0.0)` effect.

#### Scenario: Reconstruct pause segment
- **WHEN** a pause segment of 5 seconds at frame 100 is processed at target 24fps
- **THEN** the output track contains a clip of duration 120 frames pointing to the media, with a source range of start=100 and duration=1 frame, and a `LinearTimeWarp` effect with `time_scalar = 0.0`
