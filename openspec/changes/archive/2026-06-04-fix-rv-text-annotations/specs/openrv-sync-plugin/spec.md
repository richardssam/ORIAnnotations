## MODIFIED Requirements

### Requirement: Synchronized Annotations
The plugin SHALL synchronize paint strokes between instances by intercepting RV drawing events, translating them into the flat view `SyncEvent` format, and broadcasting them. Upon receiving flat view annotations, the plugin SHALL apply them back to the RV property graph. Text annotations SHALL be broadcast immediately upon change using the `REPLACE_ANNOTATION_COMMANDS` message to prevent duplicate text objects in the timeline.

#### Scenario: Translating stroke to flat view
- **WHEN** a user completes a paint stroke in RV
- **THEN** the plugin SHALL extract the stroke properties and broadcast them as a flat view annotation payload.

#### Scenario: Applying flat view stroke
- **WHEN** the plugin receives a flat view annotation payload or snapshot
- **THEN** it SHALL translate the flat data back into OpenRV's node-based property graph and display the stroke.

#### Scenario: Immediate text annotation broadcast
- **WHEN** a user types or modifies a text annotation in OpenRV
- **THEN** the plugin SHALL immediately reconstruct the frame's annotation state and broadcast it using `REPLACE_ANNOTATION_COMMANDS`
- **AND** the plugin SHALL NOT buffer text annotations in the pending stroke queue.
