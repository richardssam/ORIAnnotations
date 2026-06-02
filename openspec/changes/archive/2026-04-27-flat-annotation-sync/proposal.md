## Why

Annotations are currently not kept as part of the overall session state. As a result, participants who join a live review session late are unable to see the existing annotations. This change ensures that the overall state of the display (with the exception of `playbackSettings`) is stored reliably within the OpenTimelineIO (OTIO) model, providing consistency for all participants regardless of when they join.

## What Changes

- Syncing annotations across the network via the OTIO sync protocol.
- Using a flat view storage model for annotations, taking advantage of the approach prototyped in `rvplugin/ori_annotations_plugin.py`.
- Ensuring that all overall state (except `playbackSettings`) is accurately reflected and stored in OTIO.

## Capabilities

### New Capabilities
- `otio-annotation-sync`: Extracting, managing, and synchronizing annotations within the OTIO sync protocol using a flat view model.

### Modified Capabilities
- `otio-sync-core`: Update the core OTIO sync state to incorporate flat annotations as part of the persistent session state tree.
- `openrv-sync-plugin`: Update the OpenRV sync plugin to listen to annotation events and push/pull them via the OTIO state.

## Impact

- `rvplugin/openrv_sync_plugin/plugin.py`: Will need updates to intercept annotation events and apply incoming annotation sync events.
- `rvplugin/ori_annotations_plugin.py`: Will be leveraged for its flat view formatting logic.
- `tests/otio_sync/*`: Tests will need to be added or updated to cover annotation syncing.
