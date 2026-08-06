## Context

Currently, `StructureSyncController` polls xStudio's session playlists every 1.0 second in the background thread (`_poll_loop`) to identify newly created playlists (`poll_new_playlists`), renamed playlists (`poll_playlist_renames`), and deleted playlists (`poll_deleted_playlists`). This periodic polling involves calling C++ actor methods synchronously, exposing the poll loop to potential freezes if any of the actors are busy or dead, and introducing latency to structural sync.

We will replace these polling methods with event-driven notifications by subscribing to the Session actor's event group.

## Goals / Non-Goals

**Goals:**
- Eliminate the 1.0-second periodic checks for playlist creation, deletions, and renames.
- Transition `StructureSyncController`'s session-level sync logic to be entirely event-driven.
- Keep the threading invariant: all mutations/manager operations must run on the background poll thread, not the xStudio event-dispatch thread.

**Non-Goals:**
- Do not refactor other periodic poll components (display state, color sync) that are not session-level playlist structures.

## Decisions

### Decision 1: Subscribe to Session event group at startup
- **Approach**: During `connect_to_session`, we call `subscribe_to_event_group` on `self.connection.api.session`. We will save the returned callback subscription ID in `self._session_sub_id` and unsubscribe during `disconnect()`.
- **Alternatives Considered**: 
  - *Subscribe per playlist*: Subscribing to individual playlists causes SIGSEGV crashes on tear-down, which is why it was avoided. Subscribing to the top-level `Session` is safe because the Session object has a lifecycle tied to the plugin itself and is never dynamically deleted during a connection.

### Decision 2: Process Session events on the Poll Thread
- **Approach**: The callback for the Session event subscription runs on xStudio's event-dispatch thread. We must not mutate the sync manager or do heavy C++ operations directly inside this callback. Instead, the callback will package the event and push it to `_cmd_queue`. The poll thread will dequeue the event, determine the command (e.g., `add_playlist`, `remove_playlist`, `rename_playlist`), and delegate it to the controller.
- **Rationale**: Preserves the threading invariant where only the poll thread interacts with `SyncManager` and does xStudio session modifications.

## Risks / Trade-offs

- **[Risk] Echo Loops on Remote Modifications**
  - *Description*: When a remote client adds/deletes/renames a playlist, our local client applies that change to xStudio, which in turn causes the local Session event group to fire an `add_playlist` or similar event. If not guarded, we might re-broadcast this event back to the network.
  - *Mitigation*: We will use the existing `self._structural_mutation_suppress_until` and `_sync_playlists` registry checks to ignore events resulting from our own remote-apply actions.
