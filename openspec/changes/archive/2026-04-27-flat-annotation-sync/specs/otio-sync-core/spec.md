## ADDED Requirements

### Requirement: OTIO Session Snapshot Persistence
The `SyncManager` SHALL ensure that annotation events (`SyncEvent`) received from clients are persistently appended to the root timeline managed by the Master, so they are included in any `state_snapshot_received` payload.

#### Scenario: Master applying annotation to timeline
- **WHEN** the Master receives an annotation event
- **THEN** it SHALL update its internal OTIO timeline to include the annotation data before any subsequent state snapshots are sent.
