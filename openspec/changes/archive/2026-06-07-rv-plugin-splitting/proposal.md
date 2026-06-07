## Why

The OpenRV sync plugin (`rvplugin/ori_sync/plugin.py`) has grown to 3063 lines in a single file, mixing sequence management, playback sync, display state sync, annotation rendering, and UI/menu code in one monolithic `OpenRVSyncPlugin` class. This makes the file difficult to navigate, hard to test in isolation, and risky to modify — a change to annotation rendering can accidentally break sequence detection, and vice versa.

## What Changes

- Split `plugin.py` into 6 focused modules within the same `rvplugin/ori_sync/` directory, using a delegated controller pattern where the main `OpenRVSyncPlugin` class instantiates domain-specific controller objects.
- Extract shared utilities (logging, path normalisation, UI dialogs) into a standalone `utils.py` module.
- Extract sequence/timeline structural sync into `sequence_sync.py`.
- Extract playback state and selection sync into `playback_sync.py`.
- Extract display state sync (pan, zoom, exposure, channel) into `display_sync.py`.
- Extract annotation sync (strokes, text, partial broadcasts) into `annotation_sync.py`.
- Update `makepackage.csh` to include the new files in the `.rvpkg` zip.
- Fix the existing `test_openrv_annotations.py` test suite which is currently broken due to a stale `_setup_sync` mock reference.
- No changes to external behaviour, protocol messages, or the `otio_sync_core` library.

## Capabilities

### New Capabilities

- `rv-plugin-module-structure`: Defines the module layout, controller delegation pattern, and cross-controller communication rules for the split OpenRV plugin.

### Modified Capabilities

- `openrv-sync-plugin`: The implementation structure changes (single file → multi-module), but all existing behavioural requirements remain unchanged. A delta spec will document the new module organisation constraint.

## Impact

- **Code**: `rvplugin/ori_sync/plugin.py` is restructured; 5 new Python files are created alongside it. No other source files in the repository change.
- **Packaging**: `makepackage.csh` zip command must include the new modules. `PACKAGE` file does not change (only the entry-point `plugin.py` is listed in `modes:`).
- **Tests**: `tests/otio_sync/test_openrv_annotations.py` import paths and mock targets must be updated to reflect the new module locations.
- **Dependencies**: None. No new runtime or build dependencies are introduced.
- **Risk**: Low. This is a pure internal refactoring with no protocol, API, or behavioural changes. All existing integration tests (`sync_test/`) validate end-to-end correctness and are unaffected by internal module boundaries.
