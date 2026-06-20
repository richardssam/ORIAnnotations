## Why

The xStudio sync plugin (`xstudio_plugin/ori_sync/ori_sync_plugin.py`) has grown to 5232 lines in a single `ORISyncPlugin` class, mixing session lifecycle, OTIO timeline construction, playback/selection sync, display sync, structural sync, annotation broadcast/apply, and media GUID mapping. The `__init__` alone is ~290 lines declaring ~60 instance attributes. This makes the file hard to navigate, hard to reason about across its multi-threaded execution paths, and risky to modify — a change in one concern can silently break another. We just completed the equivalent split for the OpenRV plugin (`2026-06-07-rv-plugin-splitting`), so the pattern is proven and we can mirror it.

## What Changes

- Split `ori_sync_plugin.py` into focused modules within the existing `xstudio_plugin/ori_sync/` package, using the same delegated controller pattern as the OpenRV refactor: the main `ORISyncPlugin` class instantiates domain-specific controller objects and delegates to them.
- Extract stateless helpers (logging, URI/path normalisation, session-string parsing) into `utils.py`.
- Extract OTIO timeline construction (master side) into `timeline_build.py`.
- Extract playback/playhead/position/selection sync into `playback_sync.py`.
- Extract viewport display-state sync into `display_sync.py`.
- Extract structural sync (reorders, new media, deletions, new playlists, renames, remote structural apply) into `structure_sync.py`.
- Extract annotation broadcast/apply (strokes, captions, partial/live broadcasts) into `annotation_sync.py`.
- Extract the sync-GUID ↔ xStudio-media mapping into `media_map.py` as a shared controller, instantiated first so other controllers can reference it.
- Use relative imports within the package (`from .utils import _log`, etc.) — xStudio loads this directory as a real Python package via `__init__.py`, unlike RV's mode-loader.
- Move each domain's instance state out of `ORISyncPlugin.__init__` onto its owning controller; keep only the cross-cutting, cross-thread state (SyncManager reference, command queue, suppress-windows, echo-guard frame trackers) on the plugin.
- No changes to external behaviour, protocol messages, the threading model, or the `otio_sync_core` library.

## Capabilities

### New Capabilities
- `xstudio-plugin-module-structure`: Defines the module layout, the delegated controller pattern, cross-thread shared-state ownership rules, the import dependency DAG, and the preserved threading invariant for the split xStudio plugin.

### Modified Capabilities
<!-- None. This is a pure internal refactor; no spec-level behaviour changes. Existing behavioural specs (xstudio-event-sync, otio-annotation-sync, xs-*) are unaffected. -->

## Impact

- **Code**: `xstudio_plugin/ori_sync/ori_sync_plugin.py` is restructured into the entry-point module; 7 new Python files are created alongside it in the same package directory. `__init__.py` continues to re-export `create_plugin_instance` and `ORISyncPlugin`.
- **Threading**: The invariant that only the poll thread touches the SyncManager (xStudio event handlers only enqueue onto `_cmd_queue`) must be preserved exactly; methods moved into controllers must not change which thread they execute on.
- **Tests**: `sync_test/` integration tests validate end-to-end correctness and are unaffected by internal module boundaries. Any unit test that imports plugin internals directly may need import-path updates.
- **Dependencies**: None. No new runtime or build dependencies.
- **Risk**: Low–medium. Pure internal refactor with no protocol/API/behavioural changes, but the large volume of moved state and the multi-threaded execution paths make call-site migration the main verification surface.
