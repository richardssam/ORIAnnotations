## Why

The xStudio plugin's encapsulation is nominal: domain state (suppression guards, frame/play echo-guard fields, show-atom tracking) is parked on `ORISyncPlugin` per the current module-structure spec, controllers reach back through `self.plugin.<attr>` for it (~200 occurrences in `playback_sync.py` alone), and `disconnect()` hand-clears ~20 private fields across five controllers. The original rationale — "cross-thread state lives on the plugin" — conflates thread safety with attribute placement: safety comes from the poll-thread invariant and the GIL, not from which object holds the field. The cost is real now: `__init__` is littered with orphaned comments describing attributes that no longer live there, and the `session-roles` change names this cleanup as its Phase 0 prerequisite — its Phase 1c guard deletion needs the echo guards to live in controller-local scope so removal is a small, safe diff.

## What Changes

- Domain state moves from `ORISyncPlugin` to its owning controller: playback/selection echo guards and show-atom tracking to `PlaybackSyncController`, the annotation flush trigger and reload suppression to `AnnotationSyncController`, structural-mutation suppression to `StructureSyncController`. Only genuinely cross-cutting infrastructure remains on the plugin: `manager`, `_cmd_queue`, the poll-thread lifecycle (`_poll_stop`, `_poll_thread`), and the canonical timeline registry (`_sync_playlists`).
- Every controller gains a `reset()` method that returns its state to post-construction defaults; `disconnect()` collapses to one `reset()` call per controller (the pattern `MediaMapController.reset()` and `ColorSyncController.reset()` already establish).
- Orphaned `__init__` comment blocks — those describing state that previously moved to controllers, plus those relocated by this change — move with their attributes or are deleted.
- The `xstudio-plugin-module-structure` spec's "Shared cross-thread state ownership" requirement is rewritten: state ownership follows domain, and cross-thread safety is carried by the existing "Threading invariant preserved" requirement, not by attribute placement.
- **No behavior change**: echo-suppression semantics, protocol messages, and timing are identical. This change relocates the guards; `session-roles` Phase 1c later deletes them.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `xstudio-plugin-module-structure`: the "Shared cross-thread state ownership" requirement is replaced — domain state (including suppression guards and echo-guard fields) SHALL live on its owning controller; only the manager reference, command queue, poll-thread lifecycle, and canonical timeline registry remain plugin attributes; each controller SHALL expose `reset()` and `disconnect()` SHALL delegate teardown to it. The "Behaviour unchanged" requirement gains a scenario covering this refactor.

## Impact

- **Code**: all modules in `xstudio_plugin/ori_sync/` — `ori_sync_plugin.py` (`__init__`, `disconnect()`) shrinks substantially; `playback_sync.py`, `annotation_sync.py`, `structure_sync.py`, `display_sync.py` take ownership of their state and gain `reset()`; `media_map.py` and `color_sync.py` are the reference pattern and need at most renaming alignment.
- **Protocol**: none.
- **Downstream**: unblocks `session-roles` Phase 0 (its design.md names this cleanup as a prerequisite for the Phase 1c guard-deletion commit). Should land before `session-roles` implementation begins.
- **Testing**: the two-client `sync_test/` integration suite must pass unchanged — same bar as the original module split ("Behaviour unchanged" requirement). No RV-side impact.
