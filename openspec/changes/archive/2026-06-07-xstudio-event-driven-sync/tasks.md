## 1. Setup & Poll Loop Refactor

- [x] 1.1 Remove the 33ms `time.sleep` and state polling methods from `_poll_loop`
- [x] 1.2 Update `_poll_loop` to block on `self._cmd_queue.get(timeout=1.0)` to process broadcast mutations safely

## 2. Playhead Sync

- [x] 2.1 Rename `_on_test_position_event` to `_on_position_event` and remove the `[TEST]` stub
- [x] 2.2 Implement the echo guard in `_on_position_event` by checking the frame against `_last_applied_frame`
- [x] 2.3 Remove `_poll_and_broadcast_frame()` and ensure `position_atom` properly enqueues `playback_settings`
- [x] 2.4 Verify scrubbing and standard playback accurately syncs to network

## 3. Selection Sync

- [x] 3.1 Identify the correct xStudio `atom` for selection (e.g. `selection_actor_atom` or `item_selection_atom`)
- [x] 3.2 Create `_on_selection_event` callback to parse the selected UUIDs
- [x] 3.3 Ensure the callback enqueues a `selection_changed` message to `_cmd_queue`
- [x] 3.4 Remove `_poll_and_broadcast_selection()`

## 4. Hierarchy & Mutation Sync

- [x] 4.1 Expand the existing `_on_timeline_item_event` (from `[2F]`) to parse the actual mutation (insert, delete, reorder) instead of just marking the timeline dirty
- [x] 4.2 Create echo guards for structural mutations to prevent remote inserts from bouncing back
- [x] 4.3 Remove `_poll_flat_playlist_reorders`, `_poll_sequence_reorders`, `_poll_sequence_new_media`, `_poll_sequence_track_deletions`, etc.

## 5. Cleanup & Testing

- [x] 5.1 Remove any unused `_poll_*` functions related to hierarchy and sequence
- [x] 5.2 Validate CPU usage when idle has dropped significantly
- [x] 5.3 Run `sync_test` suite to verify event-driven sync maintains data integrity across peers
