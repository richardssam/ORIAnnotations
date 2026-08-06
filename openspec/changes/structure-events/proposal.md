## Why

The current xStudio plugin implementation relies on a 1.0-second periodic poll loop to detect structural changes (creation, renames, and deletions of playlists/timelines) at the session level. This periodic polling adds unnecessary overhead and latency, and requires continuously scanning `session.playlists` to diff against cached plugin state.

By subscribing to the Session actor's event group, we can make top-level playlist synchronization fully event-driven, reducing CPU overhead and simplifying the plugin's structure synchronization logic.

## What Changes

- **Session Event Group Subscription**: Subscribe to the Session actor's event group at connection time.
- **Event-Driven Handlers**: Add Python handlers for the following CAF events broadcast by the Session actor:
  - `add_playlist_atom`: Detect when a new playlist/timeline is created.
  - `playlist::rename_container_atom`: Detect when a playlist/timeline is renamed.
  - `playlist::remove_container_atom`: Detect when a playlist/timeline is deleted.
- **Remove Periodic Polling**: Remove the 1.0-second periodic loops and variables associated with:
  - `poll_new_playlists`
  - `poll_playlist_renames`
  - `poll_deleted_playlists`
- **Command Queue Integration**: Route the received CAF events from the main xStudio thread safely to the poll thread via the command queue `_cmd_queue`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `xstudio-plugin-module-structure`: Update the requirements to replace the structural playlist polling loops (`poll_new_playlists`, `poll_playlist_renames`, `poll_deleted_playlists`) with event-driven Session actor group subscriptions.

## Impact

- `xstudio_plugin/ori_sync/ori_sync_plugin.py`: Update connection lifecycle to subscribe/unsubscribe to the Session event group and route session events to the command queue.
- `xstudio_plugin/ori_sync/structure_sync.py`: Refactor or remove the 1.0-second periodic `poll_new_playlists`, `poll_playlist_renames`, and `poll_deleted_playlists` methods, replacing them with event-driven commands executed on the poll thread.
