## ADDED Requirements

### Requirement: Module layout

The OpenRV sync plugin SHALL be organised as a flat set of Python modules within `rvplugin/ori_sync/`, with `plugin.py` as the sole entry-point declared in `PACKAGE`.

The module set SHALL be:

| Module | Responsibility |
|---|---|
| `plugin.py` | `MinorMode` subclass, menus, poll loop, session lifecycle, action dispatcher |
| `utils.py` | Logger, warning popups, path normalisation, static helpers |
| `sequence_sync.py` | `SequenceSyncController` — timeline/sequence structure management |
| `playback_sync.py` | `PlaybackSyncController` — playback state and selection sync |
| `display_sync.py` | `DisplaySyncController` — pan, zoom, exposure, channel sync |
| `annotation_sync.py` | `AnnotationSyncController` — strokes, text, partial broadcasts |

#### Scenario: Plugin loads successfully with split modules

- **WHEN** OpenRV loads the `OTIO Sync Plugin` package
- **THEN** `plugin.py` SHALL import all controller modules and `utils.py` without error
- **AND** the plugin SHALL initialise identically to the pre-split single-file version

#### Scenario: No sub-packages or __init__.py

- **WHEN** the plugin directory is examined
- **THEN** all Python modules SHALL be flat siblings in `rvplugin/ori_sync/` with no `__init__.py` or nested directories (excluding vendored `pika/`)

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

The `makepackage.csh` script SHALL include all 6 Python modules (`plugin.py`, `utils.py`, `sequence_sync.py`, `playback_sync.py`, `display_sync.py`, `annotation_sync.py`) in the `.rvpkg` zip archive.

#### Scenario: Built package contains all modules

- **WHEN** `makepackage.csh` is executed
- **THEN** the resulting `.rvpkg` file SHALL contain all 6 Python module files
- **AND** the `PACKAGE` file SHALL NOT be modified (only `plugin.py` is listed in `modes:`)
