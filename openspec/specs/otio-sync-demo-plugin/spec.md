## ADDED Requirements

### Requirement: OTIO Sync menu is present in the RV menu bar

The plugin SHALL register an "OTIO Sync" top-level menu in OpenRV. The menu contents SHALL be dynamic, reflecting connection state (see `ori-session-management` spec). The static "Add Clip to Timeline…" and "Sync Status" items SHALL remain present in both states.

#### Scenario: Menu appears on startup

- **WHEN** OpenRV starts with the `openrv_sync_plugin` loaded
- **THEN** an "OTIO Sync" menu SHALL be visible in the RV menu bar

---

### Requirement: Plugin bootstraps a shared tracked timeline on startup

The plugin SHALL create a fresh `otio.schema.Timeline` containing a single `otio.schema.Track` stamped with the well-known UUID constant `SYNC_DEMO_TRACK_UUID` and register it with the `SyncManager` **at connect time** (not at `__init__`), so that a timeline is only created when a session is actually joined.

#### Scenario: Shared track is registered at connect time

- **WHEN** `connect_to_session()` is called and the session is established
- **THEN** the `SyncManager._object_map` SHALL contain an entry keyed by `SYNC_DEMO_TRACK_UUID`

#### Scenario: No timeline created before connect

- **WHEN** the plugin `__init__` completes but `ORI_SESSION` is not set
- **THEN** `SyncManager._object_map` SHALL NOT contain `SYNC_DEMO_TRACK_UUID` yet

---

### Requirement: xStudio plugin gates auto-connect on ORI_SESSION

The xStudio sync plugin SHALL NOT connect to any session during `__init__` unless `ORI_SESSION` is set. If `ORI_SESSION` is set, it SHALL parse `[host:]session_name` and call `connect_to_session(host, name)`.

#### Scenario: No auto-connect without ORI_SESSION

- **WHEN** xStudio starts the plugin without `ORI_SESSION` set
- **THEN** the plugin SHALL remain disconnected and the `manager` attribute SHALL be `None`

#### Scenario: Auto-connect with ORI_SESSION

- **WHEN** xStudio starts the plugin with `ORI_SESSION=my-session`
- **THEN** the plugin SHALL call `connect_to_session("localhost", "my-session")` during `__init__`

---

### Requirement: xStudio plugin has Create, Join, and Leave session menu items

The xStudio sync plugin SHALL register "Create Session…", "Join Session…", and "Leave Session" menu items under a dedicated menu path. Callbacks SHALL guard against invalid state (already connected / not connected).

#### Scenario: Create and Join visible when disconnected

- **WHEN** the plugin is not connected and the user opens the session menu
- **THEN** "Create Session…" and "Join Session…" SHALL be available

#### Scenario: Leave visible when connected

- **WHEN** the plugin is connected to session `{name}`
- **THEN** "Leave Session" SHALL be available and SHALL disconnect when activated

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
