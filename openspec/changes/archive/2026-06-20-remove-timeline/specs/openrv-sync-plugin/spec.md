## ADDED Requirements

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
