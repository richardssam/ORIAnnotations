## MODIFIED Requirements

### Requirement: OTIO Sync menu is present in the RV menu bar

The plugin SHALL register an "OTIO Sync" top-level menu in OpenRV. The menu contents SHALL be dynamic, reflecting connection state (see `ori-session-management` spec). The static "Add Clip to Timeline…" and "Sync Status" items SHALL remain present in both states.

#### Scenario: Menu appears on startup

- **WHEN** OpenRV starts with the `openrv_sync_plugin` loaded
- **THEN** an "OTIO Sync" menu SHALL be visible in the RV menu bar

### Requirement: Plugin bootstraps a shared tracked timeline on startup

The plugin SHALL create a fresh `otio.schema.Timeline` containing a single `otio.schema.Track` stamped with the well-known UUID constant `SYNC_DEMO_TRACK_UUID` and register it with the `SyncManager` **at connect time** (not at `__init__`), so that a timeline is only created when a session is actually joined.

#### Scenario: Shared track is registered at connect time

- **WHEN** `connect_to_session()` is called and the session is established
- **THEN** the `SyncManager._object_map` SHALL contain an entry keyed by `SYNC_DEMO_TRACK_UUID`

#### Scenario: No timeline created before connect

- **WHEN** the plugin `__init__` completes but `ORI_SESSION` is not set
- **THEN** `SyncManager._object_map` SHALL NOT contain `SYNC_DEMO_TRACK_UUID` yet

## ADDED Requirements

### Requirement: xStudio plugin gates auto-connect on ORI_SESSION

The xStudio sync plugin SHALL NOT connect to any session during `__init__` unless `ORI_SESSION` is set. If `ORI_SESSION` is set, it SHALL parse `[host:]session_name` and call `connect_to_session(host, name)`.

#### Scenario: No auto-connect without ORI_SESSION

- **WHEN** xStudio starts the plugin without `ORI_SESSION` set
- **THEN** the plugin SHALL remain disconnected and the `manager` attribute SHALL be `None`

#### Scenario: Auto-connect with ORI_SESSION

- **WHEN** xStudio starts the plugin with `ORI_SESSION=my-session`
- **THEN** the plugin SHALL call `connect_to_session("localhost", "my-session")` during `__init__`

### Requirement: xStudio plugin has Create, Join, and Leave session menu items

The xStudio sync plugin SHALL register "Create Session…", "Join Session…", and "Leave Session" menu items under a dedicated menu path. Callbacks SHALL guard against invalid state (already connected / not connected).

#### Scenario: Create and Join visible when disconnected

- **WHEN** the plugin is not connected and the user opens the session menu
- **THEN** "Create Session…" and "Join Session…" SHALL be available

#### Scenario: Leave visible when connected

- **WHEN** the plugin is connected to session `{name}`
- **THEN** "Leave Session" SHALL be available and SHALL disconnect when activated
