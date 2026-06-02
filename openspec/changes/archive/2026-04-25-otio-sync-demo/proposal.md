## Why

The `otio-sync-poc` change established the core sync protocol (UDP broadcasting, `SyncManager`, transparent `OTIOSyncProxy`). We now need a functional interactive demo that proves the concept end-to-end: a user adds media inside one OpenRV instance via a menu action and sees that clip appear live in a second OpenRV instance running on the same machine.

## What Changes

- Add an "OTIO Sync" menu to the OpenRV plugin with an "Add Clip to Timeline..." action
- Extend `SyncManager` to support `insert_child` delta actions (structural mutations, not just property changes)
- Bootstrap both RV instances with a shared well-known Track UUID so the receiver knows where to insert incoming clips
- On the receiver side, call `rv.commands.addSource()` after applying an `insert_child` patch so the clip is visible in RV's viewer
- On the sender side, also load the clip locally in RV after adding it to the OTIO timeline

## Capabilities

### New Capabilities
- `otio-sync-demo-plugin`: Interactive OpenRV plugin with OTIO Sync menu, file dialog, and live sync feedback

### Modified Capabilities
- `otio-sync-core`: Extend with `insert_child` action support in `SyncManager` and network protocol

## Impact

- `python/otio_sync_core/manager.py`: Add `insert_child` method and update `apply_patch` to handle `insert_child` action
- `rvplugin/openrv_sync_plugin/plugin.py`: Add menu definition, file dialog handler, startup bootstrap, and RV source loading on patch apply
