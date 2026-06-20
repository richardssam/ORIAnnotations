## Why

The `xstudio_plugin` currently relies on a heavy 33ms polling loop running on a background thread to detect changes in playback, selection, sequence edits, and playlists. This "pull" model bypasses xStudio's robust, native actor-based event system (`atom` events), wasting CPU cycles and introducing race conditions (echo loops). Replacing the polling loop with event-driven callbacks will dramatically improve the plugin's performance, stability, and responsiveness.

## What Changes

- **BREAKING**: Replace the monolithic `_poll_loop` in `ori_sync_plugin.py` with specific xStudio event subscriptions.
- Map playhead updates to `position_atom` and `play_forward_atom`.
- Map selection updates to `selection_actor_atom` or `item_selection_atom`.
- Map timeline/sequence mutations (insert, remove, reorder, rename) to `change_atom` and `item_atom`.
- Maintain the asynchronous `_cmd_queue` and a lightweight background worker to safely broadcast updates to the network without blocking xStudio's event dispatch threads.
- Implement robust echo guards in the event callbacks to prevent infinite loops when a remote update triggers a local xStudio event.

## Capabilities

### New Capabilities
- `xstudio-event-sync`: Defines the event-driven architecture mapping xStudio atoms to OTIO mutations and broadcasts.

### Modified Capabilities
- None. This is an internal architectural refactor; the user-facing sync requirements remain the same.

## Impact

- `xstudio_plugin/ori_sync/ori_sync_plugin.py`: Major refactoring to remove `_poll_loop` and heavily leverage `subscribe_to_event_group` and `subscribe_to_playhead_events`.
- CPU usage of xStudio when idle will drop significantly.
- Sync latency will decrease as events trigger broadcasts immediately rather than waiting for the next 33ms tick.
