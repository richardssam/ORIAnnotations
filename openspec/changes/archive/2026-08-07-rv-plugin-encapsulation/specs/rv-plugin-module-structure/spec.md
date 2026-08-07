## MODIFIED Requirements

### Requirement: Module layout

The OpenRV sync plugin SHALL be organised as a flat set of Python modules within `rvplugin/ori_sync/`, with `plugin.py` as the sole entry-point declared in `PACKAGE`.

`plugin.py` SHALL contain only session lifecycle, menu construction, RV event handler registration, the poll loop, and the action dispatcher. Reusable UI helpers — including the host/session-name connection dialog — SHALL live in `utils.py`.

The module set SHALL be:

| Module | Responsibility |
|---|---|
| `plugin.py` | `MinorMode` subclass, menus, poll loop, session lifecycle, action dispatcher |
| `utils.py` | Logger, warning popups, session connection dialog, path normalisation, static helpers |
| `sequence_sync.py` | `SequenceSyncController` — timeline/sequence structure management |
| `playback_sync.py` | `PlaybackSyncController` — playback state and selection sync |
| `display_sync.py` | `DisplaySyncController` — pan, zoom, exposure, channel sync |
| `annotation_sync.py` | `AnnotationSyncController` — strokes, text, partial broadcasts |
| `color_sync.py` | `ColorSyncController` — OCIO colour space and display/view sync |

#### Scenario: Plugin loads successfully with split modules

- **WHEN** OpenRV loads the `OTIO Sync Plugin` package
- **THEN** `plugin.py` SHALL import all controller modules and `utils.py` without error
- **AND** the plugin SHALL initialise identically to the pre-split single-file version

#### Scenario: No sub-packages or __init__.py

- **WHEN** the plugin directory is examined
- **THEN** all Python modules SHALL be flat siblings in `rvplugin/ori_sync/` with no `__init__.py` or nested directories (excluding vendored `pika/`)

#### Scenario: Session dialog lives in utils

- **WHEN** a menu callback needs to prompt the user for an MQ host and session name
- **THEN** it SHALL call the shared dialog helper exported by `utils.py`
- **AND** `plugin.py` SHALL NOT define its own Qt form-building code

### Requirement: Packaging includes all modules

The `makepackage.csh` script SHALL include **every** Python module present in `rvplugin/ori_sync/` in the `.rvpkg` zip archive. The module list is hand-maintained, so adding a module to the plugin directory without adding it to `makepackage.csh` SHALL be treated as a defect: RV loads the installed package copy, and a missing module makes the plugin fail to import at load time.

#### Scenario: Built package contains all modules

- **WHEN** `makepackage.csh` is executed
- **THEN** the resulting `.rvpkg` file SHALL contain every Python module file that exists in `rvplugin/ori_sync/`
- **AND** the `PACKAGE` file SHALL NOT be modified (only `plugin.py` is listed in `modes:`)

#### Scenario: New module added to the plugin

- **WHEN** a new Python module is added to `rvplugin/ori_sync/`
- **THEN** it SHALL be added to the `zip` module list in `makepackage.csh` in the same change

## ADDED Requirements

### Requirement: Controller state is not re-exposed on the plugin

`OpenRVSyncPlugin` SHALL NOT define property forwarders, aliases, or any other
indirection that re-exposes a controller's state under a plugin attribute of the
same name. State ownership SHALL have exactly one name: the controller attribute
itself. Callers — including `plugin.py`'s own methods — SHALL reach controller
state through the owning controller.

#### Scenario: Cross-controller state access from plugin code

- **WHEN** a method on `OpenRVSyncPlugin` needs a controller-owned value such as the active media track guid
- **THEN** it SHALL read it as `self.<controller>.<attribute>` (e.g. `self.sequence._active_media_track_guid`)
- **AND** no `@property` on `OpenRVSyncPlugin` SHALL exist that forwards to a controller attribute

#### Scenario: Renaming a controller attribute

- **WHEN** a controller-owned attribute is renamed
- **THEN** the rename SHALL require edits only at the controller's definition and its direct call sites
- **AND** SHALL NOT require a matching edit in `plugin.py` unless `plugin.py` itself reads that attribute

### Requirement: Menu callback delegation

Menu callbacks registered on `OpenRVSyncPlugin` (`do_create_session`,
`do_join_session`, `do_leave_session`, `do_add_clip`, `do_show_status`) SHALL
contain only user-interaction glue — file/session dialogs, warning popups, and
`event.reject()` — and SHALL delegate all domain logic to a controller method or
to a session-lifecycle method on the plugin. Construction of OTIO objects,
time-range arithmetic, and calls into `sync_manager` mutation APIs SHALL NOT
appear in a menu callback.

#### Scenario: Add Clip delegates clip construction

- **WHEN** the user selects "Add Clip to Timeline…" and chooses a media file
- **THEN** the menu callback SHALL pass the chosen path to a `SequenceSyncController` method
- **AND** that controller method SHALL perform the RV source add, the OTIO clip and time-range construction, and the `insert_child` call
- **AND** the resulting clip SHALL be inserted into the same media track as before this change, so peers observe an identical `insert_child` message

#### Scenario: Add Clip cancelled

- **WHEN** the user selects "Add Clip to Timeline…" and cancels the file dialog
- **THEN** the menu callback SHALL reject the event without calling into any controller
