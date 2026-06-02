## MODIFIED Requirements

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

## ADDED Requirements

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
