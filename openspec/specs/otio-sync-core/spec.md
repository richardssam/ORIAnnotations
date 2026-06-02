# OTIO Sync Core Specification

## Purpose
Provide a robust foundation for real-time synchronization of OpenTimelineIO data structures across multiple application instances.

## Requirements

### Requirement: Unique Object Identification
The system SHALL ensure that every OTIO object managed by the sync manager has a unique identifier (`metadata["sync"]["guid"]`) to facilitate reliable targeting of patches.

### Requirement: Command-Based Messaging (ASWF PRWG)
The system SHALL use a command/event envelope for all messages to align with the ASWF Synchronized Review Messaging standard.

### Requirement: Master Election
The core SHALL implement an "Eldest Peer" master election strategy to designate a source of truth for session state snapshots.

### Requirement: Lossless Join (Buffering)
The `SyncManager` SHALL support buffering of incoming delta events while a client is in the process of applying a full state snapshot to ensure no mutations are missed.

### Requirement: RabbitMQ Transport
The system SHALL support RabbitMQ as a transport layer for fanout-style broadcasting within a session.

### Requirement: Silent Patch Application
The system SHALL apply incoming network patches to the local OTIO graph without triggering subsequent outgoing payloads (preventing echo loops).

### Requirement: OTIO Graph Mutations
The `SyncManager` SHALL provide methods to modify the OTIO graph and automatically broadcast the corresponding `OTIO_SESSION` events.

### Requirement: OTIO Session Snapshot Persistence
The `SyncManager` SHALL ensure that annotation events (`SyncEvent`) received from clients are persistently appended to the root timeline managed by the Master, so they are included in any `state_snapshot_received` payload.

#### Scenario: Master applying annotation to timeline
- **WHEN** the Master receives an annotation event
- **THEN** it SHALL update its internal OTIO timeline to include the annotation data before any subsequent state snapshots are sent.
