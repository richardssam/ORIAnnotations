## ADDED Requirements

### Requirement: Event-Driven Playhead Sync
The xStudio plugin SHALL sync the playhead state by subscribing to playhead events (e.g. `position_atom`, `play_forward_atom`) instead of relying on a polling thread.

#### Scenario: Local playback updates trigger broadcast
- **WHEN** the user scrubs or starts playback in the local xStudio viewport
- **THEN** the plugin receives a playhead event and queues a `playback_settings` message to the network

#### Scenario: Remote playback updates are guarded against echo loops
- **WHEN** a remote peer updates the local xStudio playhead frame
- **THEN** the resulting local playhead event is caught by an echo guard (e.g. checking against `_last_applied_frame`) and is NOT broadcast back to the network

### Requirement: Event-Driven Selection Sync
The xStudio plugin SHALL sync selection states by subscribing to the container's selection events (e.g. `selection_actor_atom`).

#### Scenario: Local selection updates trigger broadcast
- **WHEN** the user selects or deselects a clip in the xStudio timeline
- **THEN** the plugin receives a selection event and queues a `selection_changed` message to the network

### Requirement: Event-Driven Sequence Mutation Sync
The xStudio plugin SHALL sync timeline edits (insertions, deletions, reorders, renames) by subscribing to container events (e.g. `change_atom` and `item_atom`).

#### Scenario: Clip structural edits trigger broadcast
- **WHEN** the user modifies the timeline structure (e.g. adds a new clip or reorders clips)
- **THEN** the plugin receives a change event and queues the corresponding OTIO mutation (e.g. `insert_child`, `remove_child`) to the network

### Requirement: Non-Blocking Event Handlers
The xStudio plugin SHALL process event callbacks efficiently without blocking the xStudio UI or internal event threads.

#### Scenario: Network broadcasts are asynchronous
- **WHEN** an event callback fires and requires a network broadcast
- **THEN** the callback pushes the mutation to an asynchronous command queue (`_cmd_queue`) where a dedicated background worker consumes it for RabbitMQ transmission
