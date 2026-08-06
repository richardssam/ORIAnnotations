## ADDED Requirements

### Requirement: Session event-group subscription
The plugin SHALL subscribe to xStudio's Session actor event group at connection time to receive session-level notifications (such as container creation, renames, and moves). This subscription SHALL be maintained throughout the session lifecycle and unsubscribed upon disconnection.

#### Scenario: Successful subscription at connect
- **WHEN** `connect_to_session` is called
- **THEN** the plugin SHALL join the session's event group by calling `subscribe_to_event_group` with `self.connection.api.session`
- **AND** store the subscription ID as `self._session_sub_id`

#### Scenario: Unsubscribe on disconnect
- **WHEN** `disconnect` is called
- **THEN** the plugin SHALL unsubscribe from the session's event group using `unsubscribe_from_event_group(self._session_sub_id)`

### Requirement: Event-driven playlist creation and rename sync
The plugin SHALL process `add_playlist_atom` and `playlist::rename_container_atom` events from the Session event group to reactively sync playlist creation and renaming. The periodic polling methods `poll_new_playlists` and `poll_playlist_renames` SHALL be removed.

#### Scenario: Session broadcasts add_playlist_atom
- **WHEN** an `add_playlist_atom` event is received from the Session event group
- **THEN** the plugin SHALL queue a `load_timelines` command via the command queue
- **AND** compile the playlist configuration reactively without periodic polling

#### Scenario: Session broadcasts rename_container_atom
- **WHEN** a `playlist::rename_container_atom` event is received from the Session event group with the container's Uuid and new name
- **THEN** the plugin SHALL rename the local matching container to the new name on the poll thread

## MODIFIED Requirements

### Requirement: Structural controller propagates timeline deletion
The `StructureSyncController` SHALL reactively process timeline deletion when a user deletes a synced playlist/timeline in xStudio, and SHALL tear down the local container when a peer's removal is received. This extends the controller's existing ownership of structural deletions and playlist handling.

Detection SHALL occur by handling `playlist::remove_container_atom` events received from the Session event group. Upon receiving the deletion event of a tracked container, the master client SHALL call `broadcast_remove_timeline` with that timeline's GUID to notify peers.

Local container teardown SHALL remove the container by its **container uuid** (`create_playlist`'s first return value, resolved from `session.playlist_tree`), not the `Playlist` actor's uuid — `session.remove_container` keys on the former, and using the latter silently removes nothing and lets the event handler re-detect and resurrect the timeline. The teardown SHALL set the structural-mutation suppression guard so the removal's own xStudio events do not echo back as a re-broadcast. The periodic `poll_deleted_playlists` method SHALL be removed.

#### Scenario: User deletes a synced playlist/timeline in xStudio
- **WHEN** a `playlist::remove_container_atom` event is received for a tracked container
- **THEN** the master client SHALL call `broadcast_remove_timeline` with that timeline's GUID

#### Scenario: Peer removal tears down the xStudio container
- **WHEN** the plugin receives a `remove_timeline` action from the sync manager
- **THEN** `StructureSyncController` SHALL remove the xStudio container by its resolved container uuid, symmetric to container creation on `add_timeline`
- **AND** the removed timeline SHALL NOT be re-broadcast by a subsequent event

#### Scenario: Removal flows through the existing dispatch tables
- **WHEN** a `remove_timeline` event is routed
- **THEN** it SHALL be handled via the existing entry-point dispatch tables (`_handle_manager_event`), with no new protocol message format or sequence beyond `REMOVE_TIMELINE` itself
