## 1. Connection and Session Subscription

- [ ] 1.1 Subscribe to Session event group during `connect_to_session` in `ori_sync_plugin.py`
- [ ] 1.2 Store session subscription ID as `self._session_sub_id`
- [ ] 1.3 Unsubscribe from Session event group during `disconnect` in `ori_sync_plugin.py`

## 2. Event Dispatching and Command Queue

- [ ] 2.1 Implement `_on_session_event` handler in `ori_sync_plugin.py` to intercept CAF events
- [ ] 2.2 Enqueue session events (`add_playlist_atom`, `playlist::remove_container_atom`, `playlist::rename_container_atom`) onto `_cmd_queue`
- [ ] 2.3 Add dispatch mapping in `_execute_command` for the session event commands

## 3. Event-Driven Structure Sync Implementation

- [ ] 3.1 Implement event-driven container addition in `StructureSyncController` (triggered by `add_playlist_atom`)
- [ ] 3.2 Implement event-driven container rename in `StructureSyncController` (triggered by `playlist::rename_container_atom`)
- [ ] 3.3 Implement event-driven container deletion in `StructureSyncController` (triggered by `playlist::remove_container_atom`)

## 4. Cleanup and Removal of Poll Loops

- [ ] 4.1 Remove periodic calls to `poll_new_playlists`, `poll_playlist_renames`, and `poll_deleted_playlists` in the 1-second poll thread block in `ori_sync_plugin.py`
- [ ] 4.2 Delete/clean up the deprecated polling methods in `structure_sync.py`
- [ ] 4.3 Clean up no longer needed timestamp attributes (e.g. `structure._last_structure_scan` if unused elsewhere)

## 5. Verification

- [ ] 5.1 Run the existing sync integration tests under `sync_test/` to verify structural synchronization behaves identically
