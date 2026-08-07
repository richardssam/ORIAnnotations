## MODIFIED Requirements

### Requirement: Dynamic OTIO Sync menu reflects connection state

The plugin SHALL rebuild the "OTIO Sync" menu via `defineModeMenu` whenever connection state changes, showing session management items appropriate to the current state. The connected/disconnected menus below apply when the sync core imported successfully; when it did not, the unavailable menu defined in "Sync core import failure is visible in the menu" applies instead.

#### Scenario: Disconnected menu

- **WHEN** the plugin is not in a session and the sync core is available
- **THEN** the OTIO Sync menu SHALL contain "Create Session…", "Join Session…", a separator, "Add Clip to Timeline…", and "Sync Status"
- **AND** "Add Clip to Timeline…" SHALL be in `DisabledMenuState`

#### Scenario: Connected menu

- **WHEN** the plugin is in a session named `{name}`
- **THEN** the OTIO Sync menu SHALL contain "Leave Session ({name})", a separator, "Add Clip to Timeline…", and "Sync Status"
- **AND** "Create Session…" and "Join Session…" SHALL NOT appear

## ADDED Requirements

### Requirement: Sync core import failure is visible in the menu

When `otio_sync_core` or `RabbitMQNetwork` cannot be imported, the plugin SHALL
surface that failure in the OTIO Sync menu rather than presenting session items
that silently do nothing. A swallowed import error — as in the vendored-`pika`
packaging incident — must not be indistinguishable from a working plugin.

The failure SHALL additionally be reported to the plugin log and to stderr,
including the underlying exception text, so the cause is recoverable from a
session log without attaching a debugger.

#### Scenario: Sync core unavailable

- **WHEN** the plugin loads and the `otio_sync_core` / `RabbitMQNetwork` import raised `ImportError`
- **THEN** the OTIO Sync menu SHALL show a single item labelled to state that sync is unavailable because the sync core failed to import
- **AND** that item SHALL be in `DisabledMenuState`
- **AND** "Create Session…", "Join Session…", and "Add Clip to Timeline…" SHALL NOT be offered

#### Scenario: Import failure is logged with its cause

- **WHEN** the sync core import fails
- **THEN** the plugin SHALL log the originating `ImportError` message
- **AND** SHALL also write it to stderr so it is visible in the RV console

#### Scenario: Sync core available

- **WHEN** the plugin loads and the sync core imports successfully
- **THEN** the OTIO Sync menu SHALL be built from the connected/disconnected states as before
- **AND** no unavailable item SHALL appear

#### Scenario: Controller modules do not abort the mode load

- **WHEN** any controller module (`sequence_sync.py`, `playback_sync.py`, `display_sync.py`, `annotation_sync.py`, `color_sync.py`) imports a name from `otio_sync_core` at module level
- **THEN** that import SHALL be wrapped so a missing sync core raises no exception out of the module
- **AND** `plugin.py` SHALL still import successfully and build the unavailable menu
- **AND** the substituted values SHALL NOT be functional stubs — any code path reaching one SHALL fail loudly rather than behave as if sync were working

### Requirement: Sync is unreachable when the core is unavailable

Tolerating a missing sync core at import time SHALL NOT make the plugin appear
functional. The import guards exist only so the failure can be *reported*; every
route into an actual session SHALL remain closed.

#### Scenario: No route into a session

- **WHEN** the sync core failed to import
- **THEN** `connect_to_session` SHALL return without creating a `SyncManager`
- **AND** the menu SHALL offer no item capable of opening a session
- **AND** no protocol message SHALL be sent or applied
