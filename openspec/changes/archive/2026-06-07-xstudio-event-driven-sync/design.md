## Context

The xStudio integration plugin (`ori_sync_plugin.py`) currently relies on a heavy `_poll_loop` that runs every 33ms to query the entire state of the host application (playhead, selection, timelines, playlists, and edits). While this "pull" architecture guarantees the plugin eventually sees all changes, it is computationally expensive and sidesteps xStudio's native, modern actor-based `atom` event bus.

## Goals / Non-Goals

**Goals:**
- Completely remove the 33ms `_poll_loop` from `ori_sync_plugin.py`.
- Map xStudio's granular `atom` events to `OTIO` mutations.
- Ensure that the xStudio internal event dispatch threads are never blocked by RabbitMQ or SyncManager processing.
- Prevent "echo loops" where a remote timeline sync applied locally triggers a local event that gets broadcast back to the network.

**Non-Goals:**
- This design does not change how OpenRV or the Sync Viewer operate.
- This design does not change the core `SyncManager` or `RabbitMQNetwork` logic.
- We will not overhaul the xStudio UI components.

## Decisions

### 1. Replacing Polling with Atom Subscriptions
**Rationale:** Rather than iterating over all items, we will leverage xStudio's event hooks.
- **Playhead**: Subscribe to `viewport_playhead_atom` updates using `subscribe_to_playhead_events(ph_remote)`. This yields `position_atom` events which map to `_poll_and_broadcast_frame`.
- **Selection**: Subscribe to `selection_actor_atom` or similar on the viewed container.
- **Hierarchy/Media**: Subscribe to `change_atom` and `item_atom` via `subscribe_to_event_group(xs_tl, cb)` to detect structural changes (inserts, deletes, renames).

### 2. Retaining the Async Command Queue
**Rationale:** xStudio callbacks execute on the application's internal CAF/actor threads. Calling `SyncManager` mutations or RabbitMQ network functions directly from these callbacks could stall the host application.
We will maintain the `_cmd_queue` and the `_poll_stop` background thread. However, instead of the thread waking up every 33ms to query the application, it will simply block on the queue (`self._cmd_queue.get()`) waiting for payloads pushed by the event callbacks.

### 3. Echo Guards
**Rationale:** When a remote peer moves the playhead, the `SyncManager` calls into the xStudio plugin to update the local playhead. This local update will cause xStudio to fire a `position_atom` event. If our event callback naively broadcasts this, it creates an infinite feedback loop.
We will implement "echo guards." For the playhead, we will compare the event's frame to `_last_applied_frame` (as the polling loop currently does). For structural mutations, we may need a temporal debounce (e.g., ignoring structural events for a specific UUID for 0.5s after a remote apply) or a more sophisticated UUID matching check.

## Risks / Trade-offs

- **Risk: Missed State Transitions.** Polling guarantees eventual consistency. If an event is dropped or poorly mapped, the sync session may drift.
  - *Mitigation:* Extensive testing using `sync_test` suite. We will keep a "dirty" fallback or manual "resync" button if necessary during development, but the goal is 100% event reliability.
- **Risk: xStudio API Instability.** Undocumented or experimental atoms (like `change_atom` and `item_atom`) may change signature or behavior across xStudio versions.
  - *Mitigation:* Encapsulate the event payload parsing in distinct helper methods (e.g., `_parse_item_atom`) so they are easy to update if the xStudio API shifts.
