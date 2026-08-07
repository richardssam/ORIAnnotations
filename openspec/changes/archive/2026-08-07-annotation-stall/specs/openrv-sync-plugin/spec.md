## MODIFIED Requirements

### Requirement: Synchronized Annotations

The plugin SHALL synchronize paint strokes between instances by intercepting RV drawing events, translating them into the flat view `SyncEvent` format, and broadcasting them. Upon receiving flat view annotations, the plugin SHALL apply them back to the RV property graph. Text annotations SHALL be broadcast immediately upon change using the `REPLACE_ANNOTATION_COMMANDS` message to prevent duplicate text objects in the timeline.

The plugin SHALL additionally bind RV's internal `clear-paint` and `clear-all-paint` events (in addition to the existing `graph-state-change` binding) so that local annotation deletion is detected and broadcast, and SHALL bind changes to `<node>.paint.show` so that toggling annotation visibility is detected and broadcast.

Because RV cannot mutate a dynamically-created pen node's properties from outside the call that created it, applying a mid-gesture partial update creates a fresh pen node per tick and supersedes the previous tick's node. When superseding a partial-tick pen node, the plugin SHALL delete that node's own RV properties (not merely remove it from the frame `order`), so mid-gesture ticks do not accumulate as orphaned property components on the node for the lifetime of the RV process.

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

#### Scenario: Clear Frame is detected and broadcast
- **WHEN** the user chooses "Clear Frame" in RV's Annotate mode, firing the `clear-paint` internal event
- **THEN** the plugin SHALL identify the affected annotation clip and broadcast its surviving (possibly empty) commands via `REPLACE_ANNOTATION_COMMANDS`

#### Scenario: Clear All Frames on Timeline is detected and broadcast
- **WHEN** the user chooses "Clear All Frames on Timeline" in RV's Annotate mode, firing the `clear-all-paint` internal event
- **THEN** the plugin SHALL identify every affected annotation clip and broadcast each one's surviving (possibly empty) commands via `REPLACE_ANNOTATION_COMMANDS`

#### Scenario: Show Drawings toggle is detected and broadcast
- **WHEN** the user toggles "Show Drawings" for an RV source, changing `<node>.paint.show`
- **THEN** the plugin SHALL broadcast the new value as `annotations_visible` via `display_settings`

#### Scenario: Superseded partial-tick pen node is deleted, not just dereferenced
- **WHEN** a later mid-gesture partial update supersedes an earlier tick's pen node for the same live gesture
- **THEN** the plugin SHALL delete the superseded pen node's own RV properties in addition to removing it from the frame `order`
