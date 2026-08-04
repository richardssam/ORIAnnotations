# Capability: xstudio-event-sync

## Purpose
TBD

## Requirements

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

Sequence reconciliation SHALL converge. A reconciliation pass over a timeline that has not changed since the previous pass SHALL broadcast nothing, and a single user edit SHALL produce a bounded number of messages regardless of how many passes run. Broadcasting the correct message repeatedly is not conformance: the observed failure broadcast a valid `INSERT_CHILD` 153 times for three clips, which satisfied the requirement as previously written while leaving the peer wrong and both sessions unusable.

Reconciliation SHALL have a single authority per pass. Where the plugin can both reconcile incrementally (diff the host timeline and emit per-clip mutations) and rebuild wholesale (reconstruct the timeline and emit a replacement), one pass SHALL NOT do both for the same timeline — a rebuild replaces the state an incremental diff is computed against, so the two in combination cannot reach a fixed point even though each is correct alone.

The incremental diff SHALL be computed against a record of what this peer has already broadcast for that timeline, not against local structural state that other paths may replace.

A reconciliation pass that makes no changes SHALL be observable in the plugin log at a bounded rate, so that convergence can be confirmed from a log rather than inferred from the absence of complaints.

#### Scenario: Clip structural edits trigger broadcast

- **WHEN** the user modifies the timeline structure (e.g. adds a new clip or reorders clips)
- **THEN** the plugin receives a change event and queues the corresponding OTIO mutation (e.g. `insert_child`, `remove_child`) to the network

#### Scenario: An unchanged sequence is not re-broadcast

- **WHEN** a reconciliation pass runs against a sequence whose xStudio contents are unchanged since the previous pass
- **THEN** the plugin SHALL broadcast no structural message for that sequence
- **AND** repeated passes SHALL continue to broadcast nothing while it stays unchanged

#### Scenario: A clip appended to a sequence reaches the peer once

- **WHEN** the user adds a clip to the end of a sequence on the host
- **THEN** the peer SHALL show that clip in the same position
- **AND** the host SHALL broadcast a bounded number of structural messages for that edit, not one per reconciliation pass

#### Scenario: Rebuild and incremental reconciliation do not run together

- **WHEN** a pass determines that a timeline needs a wholesale rebuild
- **THEN** incremental per-clip reconciliation for that timeline SHALL be skipped for that pass
- **AND** the clips the incremental path would have inserted SHALL still reach the peer, carried by the rebuild

#### Scenario: Local structural state does not make sent edits look unsent

- **WHEN** a timeline is re-registered locally, replacing the object an earlier insert was written into
- **THEN** the plugin SHALL NOT re-broadcast the clips it has already sent for that timeline

### Requirement: Non-Blocking Event Handlers
The xStudio plugin SHALL process event callbacks efficiently without blocking the xStudio UI or internal event threads.

#### Scenario: Network broadcasts are asynchronous
- **WHEN** an event callback fires and requires a network broadcast
- **THEN** the callback pushes the mutation to an asynchronous command queue (`_cmd_queue`) where a dedicated background worker consumes it for RabbitMQ transmission
