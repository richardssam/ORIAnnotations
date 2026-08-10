## MODIFIED Requirements

### Requirement: Menu callback delegation

Menu callbacks registered on `OpenRVSyncPlugin` (`do_create_session`,
`do_join_session`, `do_leave_session`, `do_add_clip`, `do_resync`,
`do_show_session_state`) SHALL
contain only user-interaction glue — file/session dialogs, warning popups, and
`event.reject()` — and SHALL delegate all domain logic to a controller method or
to a session-lifecycle method on the plugin. Construction of OTIO objects,
time-range arithmetic, and calls into `sync_manager` mutation APIs SHALL NOT
appear in a menu callback.

Panel construction counts as user-interaction glue: `do_show_session_state` MAY
build the QML view and its Qt models, provided the session state it displays is
read through `otio_sync_core.ui_model` rather than reimplemented in `plugin.py`.

#### Scenario: Add Clip delegates clip construction

- **WHEN** the user selects "Add Clip to Timeline…" and chooses a media file
- **THEN** the menu callback SHALL pass the chosen path to a `SequenceSyncController` method
- **AND** that controller method SHALL perform the RV source add, the OTIO clip and time-range construction, and the `insert_child` call
- **AND** the resulting clip SHALL be inserted into the same media track as before this change, so peers observe an identical `insert_child` message

#### Scenario: Add Clip cancelled

- **WHEN** the user selects "Add Clip to Timeline…" and cancels the file dialog
- **THEN** the menu callback SHALL reject the event without calling into any controller

#### Scenario: Session State panel reads state through the shared model

- **WHEN** the user selects "Session State…"
- **THEN** the callback SHALL construct the panel from `otio_sync_core.ui_model` models bound to the plugin's `sync_manager`
- **AND** SHALL NOT read `SyncManager` internals directly to build its own peer or lease listing
