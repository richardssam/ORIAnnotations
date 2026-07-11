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

#### Scenario: A failed annotation replay does not block the rest of the join

- **WHEN** the joining instance replays the snapshot's annotation clips and one clip's event raises an exception (e.g. an unexpected event shape, or a lookup that fails for that specific clip)
- **THEN** that one event's replay SHALL be skipped and logged
- **AND** every other event, clip, and kind SHALL still be replayed
- **AND** the join SHALL still apply playback state, display state, and color sync, none of which SHALL be silently skipped as a side effect of the annotation-replay failure

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

#### Scenario: disconnect_from_session tears down cleanly

- **WHEN** `disconnect_from_session()` is called
- **THEN** all background threads SHALL stop, the `SyncManager` SHALL be set to `None`, and the menu SHALL rebuild to the disconnected state

### Requirement: Asynchronous Polling

The plugin SHALL use a background consumer thread to receive messages without blocking the RV UI. The poll loop (`poll_network`) SHALL reside in `plugin.py` and SHALL delegate action handling to domain-specific controller methods via `_handle_action`. Structural polling (sequence reorders, new sequences, renames) and display state polling SHALL be delegated to the `SequenceSyncController` and `DisplaySyncController` respectively.

#### Scenario: Poll loop delegates structural checks

- **WHEN** the poll timer fires and `sync_manager.status` is `STATE_SYNCED`
- **THEN** `poll_network` SHALL call `self.sequence.check_sequence_reorders()`, `self.sequence.poll_new_sequences()`, `self.sequence.poll_sequence_renames()`, and `self.display.broadcast_display_state()`

### Requirement: Synchronized timeline deletion

The RV plugin SHALL detect when a user deletes a synced sequence/playlist and
propagate the deletion to peers, and SHALL tear down the local viewer container
when a peer's deletion is received.

Detection SHALL occur in the structural poll loop, as a counterpart to
`poll_new_sequences`. When a previously-synced sequence is no longer present in
the RV node graph, the plugin SHALL call `broadcast_remove_timeline` with that
timeline's GUID. Following the ordering contract, the plugin SHALL ensure the
on-screen source has moved to a surviving sequence before broadcasting the
removal, so the removed timeline is not the active one except when it is the last
remaining timeline.

#### Scenario: User deletes a synced sequence in RV

- **WHEN** the structural poll detects that a previously-synced sequence is no
  longer present in the RV node graph
- **THEN** the plugin SHALL call `broadcast_remove_timeline` with that timeline's
  GUID after switching the on-screen source to a surviving sequence

#### Scenario: Peer removal tears down the RV container

- **WHEN** the plugin receives a `remove_timeline` action from the sync manager
- **THEN** it SHALL tear down the RV viewer container corresponding to the removed
  timeline, symmetric to the container creation performed on `add_timeline`

#### Scenario: Removal of an unknown timeline is ignored

- **WHEN** a `remove_timeline` action references a timeline the plugin has no
  container for
- **THEN** the plugin SHALL take no action and SHALL NOT raise
