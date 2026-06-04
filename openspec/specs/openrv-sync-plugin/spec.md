# OpenRV Sync Plugin Specification

## Purpose
Enable real-time, bi-directional synchronization between OpenRV instances using OpenTimelineIO and RabbitMQ.

## Requirements

### Requirement: Network Transport (RabbitMQ)
The plugin SHALL use RabbitMQ fanout exchanges for session-based broadcasting of sync events.

### Requirement: Session State Management

The plugin SHALL support runtime-configurable session identity. The session name and RabbitMQ host SHALL be determined at connect time from either the `ORI_SESSION` environment variable or interactive user input, replacing the previously hardcoded `SYNC_SESSION_ID` constant.

#### Scenario: Late joiner synchronization

- **WHEN** a new instance joins an active session
- **THEN** it SHALL request a full state snapshot from the Master peer
- **AND** it SHALL rebuild its local RV session (media sources, timeline) based on the received snapshot.

#### Scenario: Session name from ORI_SESSION

- **WHEN** `ORI_SESSION` is set at launch
- **THEN** the plugin SHALL parse `[host:]session_name` and call `connect_to_session(host, name)` automatically, with no hardcoded fallback name

#### Scenario: Session name from interactive menu

- **WHEN** `ORI_SESSION` is not set and the user selects Create or Join Session from the OTIO Sync menu
- **THEN** the plugin SHALL present a two-field dialog and call `connect_to_session(host, name)` on confirm

### Requirement: Synchronized Playback
The plugin SHALL synchronize the playhead (frame) and playback state (play/stop) between all instances.

### Requirement: Synchronized Selection
The plugin SHALL synchronize the active node/clip selection.

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

#### Scenario: disconnect_from_session tears down cleanly

- **WHEN** `disconnect_from_session()` is called
- **THEN** all background threads SHALL stop, the `SyncManager` SHALL be set to `None`, and the menu SHALL rebuild to the disconnected state

### Requirement: Asynchronous Polling

The plugin SHALL use a background consumer thread to receive messages without blocking the RV UI.
