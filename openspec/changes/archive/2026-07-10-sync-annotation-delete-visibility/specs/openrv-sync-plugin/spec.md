## MODIFIED Requirements

### Requirement: Synchronized Annotations

The plugin SHALL synchronize paint strokes between instances by intercepting RV drawing events, translating them into the flat view `SyncEvent` format, and broadcasting them. Upon receiving flat view annotations, the plugin SHALL apply them back to the RV property graph. Text annotations SHALL be broadcast immediately upon change using the `REPLACE_ANNOTATION_COMMANDS` message to prevent duplicate text objects in the timeline.

The plugin SHALL additionally bind RV's internal `clear-paint` and `clear-all-paint` events (in addition to the existing `graph-state-change` binding) so that local annotation deletion is detected and broadcast, and SHALL bind changes to `<node>.paint.show` so that toggling annotation visibility is detected and broadcast.

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

### Requirement: Dynamic OTIO Sync menu reflects connection state

The plugin SHALL rebuild the "OTIO Sync" menu via `defineModeMenu` whenever connection state changes, showing session management items appropriate to the current state.

#### Scenario: Disconnected menu

- **WHEN** the plugin is not in a session
- **THEN** the OTIO Sync menu SHALL contain "Create Session…", "Join Session…", a separator, "Add Clip to Timeline…", and "Sync Status"
- **AND** "Add Clip to Timeline…" SHALL be in `DisabledMenuState`

#### Scenario: Connected menu

- **WHEN** the plugin is in a session named `{name}`
- **THEN** the OTIO Sync menu SHALL contain "Leave Session ({name})", a separator, "Add Clip to Timeline…", and "Sync Status"
- **AND** "Create Session…" and "Join Session…" SHALL NOT appear

### Requirement: connect_to_session and disconnect_from_session methods

The plugin SHALL expose `connect_to_session(host, session_name)` and `disconnect_from_session()` as first-class methods callable from menu callbacks and startup code.

#### Scenario: connect_to_session initialises SyncManager

- **WHEN** `connect_to_session(host, name)` is called
- **THEN** the plugin SHALL create a `SyncManager` with `session_id=name`, create a `RabbitMQNetwork` with `host=host`, call `start_session()`, and update menu state
