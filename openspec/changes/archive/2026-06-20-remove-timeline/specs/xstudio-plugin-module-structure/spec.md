## ADDED Requirements

### Requirement: Structural controller propagates timeline deletion

The `StructureSyncController` SHALL broadcast timeline removal when a user deletes
a synced playlist/timeline in xStudio, and SHALL tear down the local container
when a peer's removal is received. This extends the controller's existing
ownership of structural deletions and playlist handling. Detection SHALL be
event-driven (not polled), consistent with the controller's existing
structural-event model.

#### Scenario: User deletes a synced playlist/timeline in xStudio

- **WHEN** `StructureSyncController` observes the deletion of a synced
  playlist/timeline
- **THEN** it SHALL call `broadcast_remove_timeline` with that timeline's GUID,
  after the on-screen source has moved to a surviving timeline

#### Scenario: Peer removal tears down the xStudio container

- **WHEN** the plugin receives a `remove_timeline` action from the sync manager
- **THEN** `StructureSyncController` SHALL tear down the xStudio container
  corresponding to the removed timeline, symmetric to container creation on
  `add_timeline`

#### Scenario: Removal flows through the existing dispatch tables

- **WHEN** a `remove_timeline` event is routed
- **THEN** it SHALL be handled via the existing entry-point dispatch tables
  (`_handle_manager_event`), with no new protocol message format or sequence
  beyond `REMOVE_TIMELINE` itself
