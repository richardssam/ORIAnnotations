## MODIFIED Requirements

### Requirement: Dynamic OTIO Sync menu reflects connection state

The plugin SHALL rebuild the "OTIO Sync" menu via `defineModeMenu` whenever connection state changes, showing session management items appropriate to the current state. The connected/disconnected menus below apply when the sync core imported successfully; when it did not, the unavailable menu defined in "Sync core import failure is visible in the menu" applies instead.

The "Sync Status" item is replaced by "Session State…", which opens the shared Session State panel (see the `session-state-ui` spec) instead of printing a one-line summary to the console. The connected menu additionally offers "Force Resync", which re-requests the full session state from the master.

#### Scenario: Disconnected menu

- **WHEN** the plugin is not in a session and the sync core is available
- **THEN** the OTIO Sync menu SHALL contain "Create Session…", "Join Session…", a separator, "Add Clip to Timeline…", and "Session State…"
- **AND** "Add Clip to Timeline…" SHALL be in `DisabledMenuState`

#### Scenario: Connected menu

- **WHEN** the plugin is in a session named `{name}`
- **THEN** the OTIO Sync menu SHALL contain "Leave Session ({name})", "Force Resync", a separator, "Add Clip to Timeline…", and "Session State…"
- **AND** "Create Session…" and "Join Session…" SHALL NOT appear

#### Scenario: Force Resync is unavailable to the master

- **WHEN** the plugin is in a session and this peer is the master
- **THEN** "Force Resync" SHALL be in `DisabledMenuState`
- **AND** selecting it on a non-master peer SHALL call `request_state()` on the sync manager
