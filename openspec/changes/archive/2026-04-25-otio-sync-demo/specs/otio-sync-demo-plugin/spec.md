## ADDED Requirements

### Requirement: OTIO Sync menu is present in the RV menu bar
The plugin SHALL register an "OTIO Sync" top-level menu in OpenRV containing at minimum an "Add Clip to Timeline..." item and a "Sync Status" item.

#### Scenario: Menu appears on startup
- **WHEN** OpenRV starts with the `openrv_sync_plugin` loaded
- **THEN** an "OTIO Sync" menu SHALL be visible in the RV menu bar

---

### Requirement: Plugin bootstraps a shared tracked timeline on startup
The plugin SHALL create a fresh `otio.schema.Timeline` containing a single `otio.schema.Track` stamped with the well-known UUID constant `SYNC_DEMO_TRACK_UUID` and register it with the `SyncManager` when the plugin initialises.

#### Scenario: Shared track is registered
- **WHEN** the plugin `__init__` completes
- **THEN** the `SyncManager._object_map` SHALL contain an entry keyed by `SYNC_DEMO_TRACK_UUID`

---

### Requirement: "Add Clip to Timeline..." opens a file dialog and inserts a clip
The plugin SHALL, when the user selects "Add Clip to Timeline...", open a file dialog via `rv.commands.openMediaFileDialog`, create an `otio.schema.Clip` from the chosen path, call `SyncManager.insert_child` on the tracked Track, and then call `rv.commands.addSource(path)` to load the media into the local RV session.

#### Scenario: User adds a clip
- **WHEN** user selects "OTIO Sync > Add Clip to Timeline..." and picks a valid media file
- **THEN** the clip SHALL appear in the local OTIO timeline AND be loaded into the local RV viewer

#### Scenario: User cancels the file dialog
- **WHEN** user selects "Add Clip to Timeline..." and cancels the dialog
- **THEN** no clip SHALL be added and no delta SHALL be broadcast

---

### Requirement: Receiver loads media when an insert_child patch arrives
The plugin SHALL, when `receive_and_apply_all` returns a count greater than zero and at least one applied patch was an `insert_child` action, call `rv.commands.addSource(path)` for each newly inserted clip so the media is visible in the receiver's RV viewer.

#### Scenario: Remote clip appears in receiver
- **WHEN** Instance B receives an `insert_child` delta from Instance A
- **THEN** the clip SHALL be inserted into Instance B's OTIO timeline AND loaded into Instance B's RV viewer
