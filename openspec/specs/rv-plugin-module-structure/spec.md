# RV Plugin Module Structure Specification

## Purpose
Define the layout, modularity, and relationships between components in the OpenRV sync plugin.
## Requirements
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

### Requirement: Delegated controller pattern

Each domain controller SHALL be a plain Python class that receives a back-reference to the `OpenRVSyncPlugin` instance in its constructor. Controllers SHALL own their domain-specific state and methods.

#### Scenario: Controller instantiation

- **WHEN** `OpenRVSyncPlugin.__init__` runs
- **THEN** it SHALL instantiate `SequenceSyncController(self)`, `PlaybackSyncController(self)`, `DisplaySyncController(self)`, and `AnnotationSyncController(self)`
- **AND** store them as `self.sequence`, `self.playback`, `self.display`, and `self.annotation`

#### Scenario: Cross-controller access

- **WHEN** a controller needs to call a method on a sibling controller
- **THEN** it SHALL access it via `self.plugin.<sibling_controller>.<method>()`
- **AND** it SHALL NOT import sibling controller modules directly

### Requirement: Shared state ownership

The `_rv_updating` reentrancy guard and `sync_manager` reference SHALL remain as attributes of `OpenRVSyncPlugin`. Controllers SHALL access them via `self.plugin._rv_updating` and `self.plugin.sync_manager`.

#### Scenario: Reentrancy guard check from controller

- **WHEN** a controller method needs to check or set the reentrancy guard
- **THEN** it SHALL read or write `self.plugin._rv_updating`
- **AND** it SHALL NOT maintain a separate copy of this flag

### Requirement: Event handler delegation

RV event handlers registered in `init()` SHALL remain as methods on `OpenRVSyncPlugin`. Each handler SHALL delegate to the appropriate controller method and handle `event.reject()` locally.

#### Scenario: Play-start event delegation

- **WHEN** RV fires a `play-start` event
- **THEN** `OpenRVSyncPlugin.on_rv_play_start` SHALL call `self.playback.broadcast_playback()` and then `event.reject()`

#### Scenario: Graph-state-change event delegation

- **WHEN** RV fires a `graph-state-change` event
- **THEN** `OpenRVSyncPlugin.on_rv_graph_state_change` SHALL delegate to the appropriate controller based on event contents (annotation controller for pen/text changes, display controller for channel changes)

### Requirement: Action dispatcher

The `_handle_action` method SHALL remain in `plugin.py` and SHALL route sync actions to controller methods based on the action string.

#### Scenario: Dispatching a playback action

- **WHEN** `_handle_action` receives `action="playback_settings"`
- **THEN** it SHALL call `self.playback.apply_playback(data)`

#### Scenario: Dispatching an annotation action

- **WHEN** `_handle_action` receives `action="annotation_commands_added"`
- **THEN** it SHALL call `self.annotation.apply_annotation_render(delta_clip)` with the delta clip extracted from the data tuple

### Requirement: Import dependency DAG

Module imports SHALL form a strict directed acyclic graph: `utils` ← `{controllers}` ← `plugin`. No controller SHALL import another controller module at the top level.

#### Scenario: No circular imports

- **WHEN** any module in `rvplugin/ori_sync/` is imported
- **THEN** the import SHALL succeed without `ImportError` or `AttributeError` caused by circular references

### Requirement: Packaging includes all modules

The `makepackage.csh` script SHALL include **every** Python module present in `rvplugin/ori_sync/` in the `.rvpkg` zip archive. The module list is hand-maintained, so adding a module to the plugin directory without adding it to `makepackage.csh` SHALL be treated as a defect: RV loads the installed package copy, and a missing module makes the plugin fail to import at load time.

#### Scenario: Built package contains all modules

- **WHEN** `makepackage.csh` is executed
- **THEN** the resulting `.rvpkg` file SHALL contain every Python module file that exists in `rvplugin/ori_sync/`
- **AND** the `PACKAGE` file SHALL NOT be modified (only `plugin.py` is listed in `modes:`)

#### Scenario: New module added to the plugin

- **WHEN** a new Python module is added to `rvplugin/ori_sync/`
- **THEN** it SHALL be added to the `zip` module list in `makepackage.csh` in the same change

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

