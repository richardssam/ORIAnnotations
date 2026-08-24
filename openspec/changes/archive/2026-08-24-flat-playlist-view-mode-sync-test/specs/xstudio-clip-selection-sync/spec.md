## ADDED Requirements

### Requirement: View convergence and isolation under peer contention
The system SHALL converge on the host's view when a viewer joins or the host reasserts a view, while preserving local isolation if a viewer explicitly changes their own view.

#### Scenario: A viewer joins an established session
- **WHEN** a viewer joins an active session where the host is viewing a specific frame of an isolated clip
- **THEN** the viewer SHALL converge to show the same clip and frame

#### Scenario: A viewer makes a local selection
- **WHEN** a viewer in a synchronized session selects a different clip locally
- **THEN** the viewer's view SHALL change to the new clip
- **AND** the host's session SHALL NOT be affected

#### Scenario: Host reasserts their view
- **WHEN** a viewer has isolated a different clip, and the host subsequently scrubs or changes frames on their own clip
- **THEN** the viewer SHALL switch back to the host's clip and match the host's playhead position

### Requirement: Flat playlist clip isolation classification
The plugin SHALL accurately classify the isolation of a clip within a flat playlist as a `source` mode view, and SHALL NOT misclassify it as `sequence` mode. The fallback view state resolution logic SHALL respect the `xs_flat_playlist` timeline metadata flag to prevent misattributing flat playlist media to sequence timelines.

#### Scenario: Isolating media in a flat playlist
- **WHEN** a user isolates a clip in a playlist without a timeline (flat playlist)
- **THEN** the plugin SHALL broadcast `view_mode: "source"`
- **AND** the plugin SHALL NOT attribute the media to a synthetic or unrelated sequence timeline
