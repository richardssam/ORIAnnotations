# xStudio Plugin Module Structure Specification

## Purpose
Define the layout, modularity, threading invariant, and relationships between components in the xStudio sync plugin.

## Requirements

### Requirement: Module layout

The xStudio sync plugin SHALL be organised as a set of Python modules within the `xstudio_plugin/ori_sync/` package, with `ori_sync_plugin.py` as the entry-point module and `__init__.py` re-exporting `create_plugin_instance` and `ORISyncPlugin`.

The module set SHALL be:

| Module | Responsibility |
|---|---|
| `ori_sync_plugin.py` | `ORISyncPlugin(PluginBase)`, menus, session lifecycle, poll loop, command-queue drain, both dispatch tables, thin event handlers, `create_plugin_instance` |
| `utils.py` | Logger, URI/path normalisation, session-string parsing, module constants |
| `timeline_build.py` | `TimelineBuildController` — OTIO timeline construction (master side) |
| `playback_sync.py` | `PlaybackSyncController` — playback, playhead, position, and selection sync |
| `display_sync.py` | `DisplaySyncController` — viewport display-state sync |
| `structure_sync.py` | `StructureSyncController` — structural sync (reorders, new media, deletions, playlists, renames, remote structural apply) |
| `annotation_sync.py` | `AnnotationSyncController` — annotation broadcast and apply |
| `media_map.py` | `MediaMapController` — sync-GUID ↔ xStudio-media mapping |

#### Scenario: Plugin loads successfully with split modules

- **WHEN** xStudio loads the `ORI Sync Review` plugin package
- **THEN** `ori_sync_plugin.py` SHALL import all controller modules and `utils.py` without error
- **AND** the plugin SHALL initialise identically to the pre-split single-file version

#### Scenario: Entry-point exports preserved

- **WHEN** the `ori_sync` package is imported
- **THEN** `create_plugin_instance` and `ORISyncPlugin` SHALL be importable from the package as before
- **AND** `create_plugin_instance(connection)` SHALL return an `ORISyncPlugin` instance

### Requirement: Delegated controller pattern

Each domain controller SHALL be a plain Python class that receives a back-reference to the `ORISyncPlugin` instance in its constructor and stores it as `self.plugin`. Controllers SHALL own their domain-specific state and methods.

#### Scenario: Controller instantiation

- **WHEN** `ORISyncPlugin.__init__` runs
- **THEN** it SHALL instantiate `MediaMapController`, `TimelineBuildController`, `PlaybackSyncController`, `DisplaySyncController`, `StructureSyncController`, and `AnnotationSyncController`
- **AND** store them as `self.media`, `self.builder`, `self.playback`, `self.display`, `self.structure`, and `self.annotation`

#### Scenario: media_map instantiated first

- **WHEN** `ORISyncPlugin.__init__` instantiates the controllers
- **THEN** `MediaMapController` SHALL be instantiated before the controllers that depend on it (playback, structure, annotation)

#### Scenario: Cross-controller access

- **WHEN** a controller needs to call a method on a sibling controller
- **THEN** it SHALL access it via `self.plugin.<sibling_controller>.<method>()`
- **AND** it SHALL NOT import sibling controller modules at module top level

### Requirement: Shared cross-thread state ownership

Cross-cutting state that is read or written across thread boundaries or across domains SHALL remain as attributes of `ORISyncPlugin`. This includes the `SyncManager` reference (`manager`), the command queue (`_cmd_queue`), the suppression guards (`_reload_suppress_until`, `_selection_broadcast_suppress_until`, `_structural_mutation_suppress_until`, `_applying_pinned_mode`), the frame/play echo-guard fields (`_last_polled_frame`, `_last_applied_frame`, `_last_polled_playing`), and the canonical timeline registry (`_sync_playlists`). Controllers SHALL access these via `self.plugin.<attr>`.

#### Scenario: Suppression guard access from controller

- **WHEN** a controller method needs to read or set a suppression guard
- **THEN** it SHALL read or write `self.plugin.<guard>`
- **AND** it SHALL NOT maintain a separate copy of that guard

#### Scenario: Manager access from controller

- **WHEN** a controller method needs the SyncManager
- **THEN** it SHALL access `self.plugin.manager`
- **AND** it SHALL only do so on the poll thread, consistent with the threading invariant

#### Scenario: Domain state owned by controllers

- **WHEN** state belongs to a single domain (e.g. annotation hot-scan caches, structural playlist maps, media mappings, display viewport state)
- **THEN** that state SHALL live on the owning controller, not on `ORISyncPlugin`

### Requirement: Threading invariant preserved

The split SHALL preserve the existing threading model: only the poll thread (`_poll_loop`) touches the `SyncManager` after startup, and xStudio event handlers SHALL only mutate cheap local state or enqueue onto `_cmd_queue`. Moving a method into a controller SHALL NOT change which thread it executes on.

The discovery-timeout path SHALL obey the same invariant. The timeout task runs
on its own short-lived thread, and that thread SHALL NOT read or mutate the
`SyncManager` beyond the single status check that decides whether the timeout
still applies — it SHALL NOT register timelines, elect this peer as master, or
broadcast. Self-election on discovery timeout SHALL instead be enqueued onto
`_cmd_queue` and executed on the poll thread, so the manager keeps a single
writer.

#### Scenario: xStudio event handler delegation

- **WHEN** xStudio fires an event on its message-dispatch thread (playhead, selection, position, annotation, timeline-item)
- **THEN** the `_on_*` handler on `ORISyncPlugin` SHALL remain a thin shim that enqueues onto `_cmd_queue` or delegates to a controller method
- **AND** it SHALL NOT call any method that touches the `SyncManager` directly on the xStudio thread

#### Scenario: Poll-thread-only manager access

- **WHEN** a controller method touches `self.plugin.manager`
- **THEN** that method SHALL only be invoked from the poll thread (via `_drain_cmd_queue`/`_execute_command` or `_handle_manager_event`)

#### Scenario: Discovery timeout defers election to the poll thread

- **WHEN** the discovery timeout expires with the session still discovering
- **THEN** the timeout thread SHALL enqueue a self-election command onto
  `_cmd_queue` and perform no further manager access
- **AND** timeline registration, election, and the `I_AM_MASTER` broadcast SHALL
  all run on the poll thread when that command is drained

#### Scenario: Election is skipped if a master appeared meanwhile

- **WHEN** the queued self-election command is drained and the session is no
  longer discovering — because a peer's `I_AM_MASTER` was processed in the
  interval — or the session has been disconnected
- **THEN** the command SHALL be a no-op
- **AND** no `I_AM_MASTER` SHALL be broadcast, so a late timeout cannot create a
  second master

#### Scenario: Solo start still elects and registers its timelines

- **WHEN** an xStudio peer starts a session with no other peer present
- **THEN** after the discovery timeout it SHALL be master, `SYNCED`, and SHALL
  have registered its current session's timelines with the manager, exactly as
  before this change

### Requirement: Dispatch tables in entry-point

Both routing tables SHALL remain in `ori_sync_plugin.py`: `_handle_manager_event` for remote sync events and `_execute_command` (with `_execute_sync_container`) for drained command-queue items. Each SHALL route to the appropriate controller method.

#### Scenario: Dispatching a remote display action

- **WHEN** `_handle_manager_event` receives `action="display_settings"`
- **THEN** it SHALL call `self.display.apply_display_state(data)` (the relocated `_apply_display_state`)

#### Scenario: Dispatching a remote annotation insert

- **WHEN** `_handle_manager_event` receives `action="insert_child"` carrying annotation commands
- **THEN** it SHALL call the annotation controller's apply method (the relocated `_apply_remote_annotation`)

#### Scenario: Dispatching a queued command

- **WHEN** `_execute_command` drains a `live_stroke` command
- **THEN** it SHALL call the annotation controller's live-stroke broadcast method (the relocated `_broadcast_live_stroke_from_json`)

### Requirement: Import dependency DAG

Module imports SHALL form a strict directed acyclic graph: `utils ← {media_map, timeline_build, playback_sync, display_sync, structure_sync, annotation_sync} ← ori_sync_plugin`. No controller SHALL import another controller module at top level. Imports within the package SHALL be relative (e.g. `from .utils import _log`).

#### Scenario: No circular imports

- **WHEN** any module in `xstudio_plugin/ori_sync/` is imported
- **THEN** the import SHALL succeed without `ImportError` or `AttributeError` caused by circular references

#### Scenario: Relative imports used within the package

- **WHEN** a module references a sibling module in the package
- **THEN** it SHALL use a relative import (`from .<module> import ...`)

### Requirement: Behaviour unchanged

The refactor SHALL NOT change any externally observable behaviour: protocol messages, sync semantics, menu items, attribute/preference names, or QML integration SHALL be identical to the pre-split version.

#### Scenario: Two-client sync regression passes

- **WHEN** the `sync_test/` two-client integration suite is run against the split plugin
- **THEN** all scenarios that passed before the split SHALL pass after it
- **AND** no protocol message format or sequence SHALL differ

### Requirement: Structural controller propagates timeline deletion

The `StructureSyncController` SHALL broadcast timeline removal when a user deletes
a synced playlist/timeline in xStudio, and SHALL tear down the local container
when a peer's removal is received. This extends the controller's existing
ownership of structural deletions and playlist handling.

Detection SHALL occur in the structural poll loop as `poll_deleted_playlists`, a
counterpart to `poll_new_playlists`: it enumerates the live `session.playlists`
and broadcasts `broadcast_remove_timeline` for any tracked timeline whose native
identity is no longer present. Liveness SHALL be judged from the live enumeration
rather than by reading the stored (possibly-dead) actor, so a deleted playlist's
actor read cannot freeze the poll thread.

Local container teardown SHALL remove the container by its **container uuid**
(`create_playlist`'s first return value, resolved from `session.playlist_tree`),
not the `Playlist` actor's uuid — `session.remove_container` keys on the former,
and using the latter silently removes nothing and lets the poll re-detect and
resurrect the timeline. The teardown SHALL set the structural-mutation suppression
guard so the removal's own xStudio events do not echo back as a re-broadcast.

#### Scenario: User deletes a synced playlist/timeline in xStudio

- **WHEN** `poll_deleted_playlists` finds a tracked timeline whose container is no
  longer present in the live `session.playlists`
- **THEN** it SHALL call `broadcast_remove_timeline` with that timeline's GUID

#### Scenario: Peer removal tears down the xStudio container

- **WHEN** the plugin receives a `remove_timeline` action from the sync manager
- **THEN** `StructureSyncController` SHALL remove the xStudio container by its
  resolved container uuid, symmetric to container creation on `add_timeline`
- **AND** the removed timeline SHALL NOT be re-broadcast by a subsequent poll

#### Scenario: Removal flows through the existing dispatch tables

- **WHEN** a `remove_timeline` event is routed
- **THEN** it SHALL be handled via the existing entry-point dispatch tables
  (`_handle_manager_event`), with no new protocol message format or sequence
  beyond `REMOVE_TIMELINE` itself
