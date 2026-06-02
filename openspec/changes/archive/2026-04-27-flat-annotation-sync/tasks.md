## 1. Core OTIO Sync Updates (`otio-sync-core`)

- [x] 1.1 Update `SyncManager` to accept `ANNOTATION` commands and translate them into OTIO `SyncEvent` schema objects (e.g. `PaintStart`, `PaintPoints`).
- [x] 1.2 Implement logic in `SyncManager` to persistently append these `SyncEvent` objects to the Master's root timeline.
- [x] 1.3 Ensure that state snapshots sent to late-joining clients serialize the root timeline, including all accumulated annotation `SyncEvent` items.

## 2. RV Sync Plugin Broadcasting (`openrv-sync-plugin`)

- [x] 2.1 Refactor `_broadcast_annotation` in `plugin.py` to construct flat view payloads compatible with the OTIO `SyncEvent` schema rather than raw RV property updates.
- [x] 2.2 Ensure the broadcast payload securely includes the necessary mapping data (clip UUID, frame number, etc.) for correct placement on the receiving end.

## 3. RV Sync Plugin Receiving (`openrv-sync-plugin`)

- [x] 3.1 Update `_apply_annotation` to parse the flat view `SyncEvent` payload and accurately translate it back into OpenRV's `RVPaint` node property graph.
- [x] 3.2 Update `_rebuild_rv_session` (which handles late joiner state snapshots) to iterate over the timeline's `SyncEvent` items and apply all historical annotations to the rebuilt RV session.

## 4. Verification & Testing

- [x] 4.1 Verify that an annotation drawn by the Master is correctly broadcast, received, and displayed by a connected Client.
- [x] 4.2 Verify that a new Client joining an existing active session successfully requests the state snapshot and reconstructs all past annotations.
