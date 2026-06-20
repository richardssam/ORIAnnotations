## Why

Sequences and playlists are added and deleted mid-session, but the `TIMELINE_1.0`
message family only propagates additions and renames — there is no way to tell
peers that a timeline went away. When a user closes a playlist or sequence, every
other peer keeps the stale timeline (and its viewer container, object-map entries,
and clip-annotation timelines) forever. The only existing removal path,
`reset_timelines()`, clears *all* timelines at once and is local-only, so it cannot
express a single deletion.

## What Changes

- Add a `RemoveTimeline` protocol message (`TIMELINE_1.0` / `REMOVE_TIMELINE`)
  carrying `timeline_guid` + `sync_timestamp`, mirroring `AddTimeline` /
  `RenameTimeline`.
- Add `SyncManager.broadcast_remove_timeline(guid)` (symmetric to
  `broadcast_add_timeline`) and a `_h_remove_timeline` handler.
- The handler performs **reference-aware teardown** of a single timeline rather
  than a clear-all:
  - delete `_timelines[guid]`,
  - cascade-delete that sequence's clip-annotation timelines from
    `_clip_timelines` (and their `_timelines` entries),
  - remove **only that timeline's subtree** from the shared `_object_map`
    (not `.clear()`),
  - if `guid == active_timeline_guid`, set it to `None` — do **not** name a
    successor. The active timeline is a projection of the playback stream, so the
    next `PlaybackSettingsSet` re-asserts it.
  - return `("remove_timeline", tl)` so the host tears down its viewer container,
    symmetric to `("add_timeline", tl)`.
  - idempotent: unknown/already-removed GUID is a silent no-op.
- Host plugins detect a user closing a sequence/playlist and call
  `broadcast_remove_timeline`, following the ordering contract: **switch the
  on-screen source first, then remove**, so the removed timeline is almost never
  the active one and `active = None` only occurs when the last timeline is closed.
- Host plugins handle the inbound `remove_timeline` event by tearing down the
  corresponding viewer container.

## Capabilities

### New Capabilities
<!-- None — this extends existing message family and host behaviors. -->

### Modified Capabilities
- `otio-sync-core`: adds the `RemoveTimeline` typed message to the timeline family
  and the manager teardown contract (single-timeline removal, clip-timeline
  cascade, scoped object-map cleanup, active-timeline-on-delete behavior).
- `openrv-sync-plugin`: detects deleted sequences in the structural poll loop and
  broadcasts removal; tears down the RV viewer container on inbound removal.
- `xstudio-plugin-module-structure`: `StructureSyncController` broadcasts removal
  when a playlist/timeline is deleted and tears down the xStudio container on
  inbound removal.

## Impact

- **Protocol**: new `REMOVE_TIMELINE` event under `TIMELINE_1.0`. Additive and
  backward-compatible — peers that do not understand it ignore it (registry
  dispatch drops unknown messages).
- **Code**: `python/otio_sync_core/protocol_messages.py` (message),
  `python/otio_sync_core/manager.py` (broadcast + handler + teardown helpers),
  RV plugin structural/sequence controller, xStudio `StructureSyncController`.
- **State**: `_timelines`, `_clip_timelines`, `_object_map`,
  `active_timeline_guid` lifecycle. `StateSnapshot` needs no change — it
  serializes `_timelines`, which the teardown mutates before the next snapshot.
- **Docs**: regenerated protocol-message reference (the missing verb that started
  this change) will now list `REMOVE_TIMELINE`.
