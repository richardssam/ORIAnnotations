## 1. Network Transport
- [x] 1.1 Create `otio_sync_core.network` module for simple UDP broadcast and reception
- [x] 1.2 Write a test script to ensure JSON payloads can be sent and received locally

## 2. OTIO SyncManager Core
- [x] 2.1 Create `otio_sync_core.manager.SyncManager` class
- [x] 2.2 Implement GUID auto-generation for all ingested OTIO objects
- [x] 2.3 Implement `set_property` wrapper that mutates the local object and emits an `otio-delta` payload via the network module
- [x] 2.4 Implement `apply_patch` to ingest incoming `otio-delta` payloads and update local objects silently

## 3. OpenRV Integration
- [x] 3.1 Scaffold basic OpenRV plugin package `openrv_sync_plugin`
- [x] 3.2 Initialize `SyncManager` when the OpenRV plugin loads
- [x] 3.3 Set up OpenRV `addTimer` to poll the network module for incoming payloads
- [x] 3.4 Wire incoming `apply_patch` updates to execute `rv.commands` to visually refresh the OpenRV timeline
- [x] 3.5 Intercept a basic OpenRV event (e.g. clip name change) and route it through `SyncManager.set_property`
