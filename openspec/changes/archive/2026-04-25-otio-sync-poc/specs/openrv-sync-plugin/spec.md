## ADDED Requirements

### Requirement: Network Transport
The plugin SHALL provide a lightweight network mechanism (e.g., UDP broadcast or simple socket) to transmit and receive `otio-delta` JSON payloads across instances.

#### Scenario: Broadcasting a payload
- **WHEN** the `SyncManager` emits an `otio-delta` payload
- **THEN** the plugin transmits it over the configured network socket.

### Requirement: Asynchronous Polling
The plugin SHALL monitor the network socket for incoming payloads without blocking the main OpenRV event loop.

#### Scenario: RV Event Loop integration
- **WHEN** the OpenRV timer ticks
- **THEN** the plugin reads from the socket queue
- **AND THEN** applies any pending patches via `SyncManager.apply_patch`.

### Requirement: RV UI Synchronization
The plugin SHALL update the OpenRV interface and timeline session when an external patch modifies the underlying OTIO graph.

#### Scenario: Remote clip name changed
- **WHEN** an incoming patch changes an OTIO clip's name
- **THEN** the plugin executes the necessary `rv.commands` to visually reflect the new name in the RV session.
