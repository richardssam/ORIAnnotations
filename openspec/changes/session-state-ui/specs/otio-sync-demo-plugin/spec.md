## MODIFIED Requirements

### Requirement: OTIO Sync menu is present in the RV menu bar

The plugin SHALL register an "OTIO Sync" top-level menu in OpenRV. The menu contents SHALL be dynamic, reflecting connection state (see `ori-session-management` spec). The static "Add Clip to Timeline…" and "Session State…" items SHALL remain present in both states.

#### Scenario: Menu appears on startup

- **WHEN** OpenRV starts with the `ori_sync` plugin loaded
- **THEN** an "OTIO Sync" menu SHALL be visible in the RV menu bar
