## MODIFIED Requirements

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

Payload location SHALL NOT be a responsibility of any of these modules. A single shared
resolver, living outside both plugin packages at the top level of the search folder, SHALL
own it. `utils.py` SHALL invoke that resolver rather than performing its own `sys.path`
and `OTIO_PLUGIN_MANIFEST_PATH` manipulation, and SHALL no longer assume the plugin sits
at a fixed depth below a repository checkout.

The resolver SHALL be a file, not a directory, so that xStudio's plugin scan does not
attempt to load it as a plugin.

#### Scenario: Plugin loads successfully with split modules

- **WHEN** xStudio loads the `ORI Sync Review` plugin package
- **THEN** `ori_sync_plugin.py` SHALL import all controller modules and `utils.py` without error
- **AND** the plugin SHALL initialise identically to the pre-split single-file version

#### Scenario: Entry-point exports preserved

- **WHEN** the `ori_sync` package is imported
- **THEN** `create_plugin_instance` and `ORISyncPlugin` SHALL be importable from the package as before
- **AND** `create_plugin_instance(connection)` SHALL return an `ORISyncPlugin` instance

#### Scenario: Payload location is not derived from the module's own depth

- **WHEN** the plugin package is placed at a location that is not two levels below a
  repository checkout
- **THEN** the plugin SHALL still resolve `otio_sync_core`, `ORIAnnotations`, and the
  `SyncEvent` schemadef
