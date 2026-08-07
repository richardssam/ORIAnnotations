## Why

A sixth of `rvplugin/ori_sync/plugin.py` (lines 93–204) is a property-forwarding shim left over from the controller extraction: ~25 `@property`/setter pairs re-exposing controller internals (`self._pending_stroke` → `self.annotation._pending_stroke`, …) on the plugin. A repo-wide reference scan shows the shim is **dead code**: its only consumer anywhere is a single `self._active_media_track_guid` read inside `do_add_clip` — every other property has zero callers in the plugin, the controllers, and the test suite. It survives only as noise that hides true state ownership and taxes every controller-attribute rename with three edit sites. Beyond the shim, `plugin.py` still carries non-dispatcher logic (OTIO clip construction in `do_add_clip`, a 40-line Qt dialog builder), and a failed `otio_sync_core` import degrades silently to a log line — the same failure mode that let the rvpkg pika-vendoring bug ship a plugin with RabbitMQ support silently disabled.

## What Changes

- The property-forwarding shim (plugin.py lines 93–204) is deleted. The one live consumer, `do_add_clip`'s `_active_media_track_guid` read, migrates to `self.sequence._active_media_track_guid` (and moves with the clip-construction extraction below).
- `do_add_clip`'s business logic — OTIO clip/time-range construction and `insert_child` — moves into `SequenceSyncController` (e.g. `add_clip_from_path(path)`); the menu callback keeps only the file dialog and delegation, matching the thin-handler pattern the spec already requires of RV event handlers.
- The `_session_dialog` Qt form builder moves from `plugin.py` to `utils.py`, leaving `plugin.py` as session lifecycle + dispatch only.
- When `otio_sync_core`/`RabbitMQNetwork` fail to import, the OTIO Sync menu SHALL show a visible disabled state (e.g. "Sync Unavailable (import failed)") instead of silently offering Create/Join items that no-op. Motivated by the pika-vendoring incident where a swallowed import error silently killed sync.
- **No sync behavior change**: protocol messages, echo handling, and session flow are untouched. Explicitly out of scope: the `_rv_updating` context-manager conversion (owned by `session-roles`) and network threading (the planned `rv-network-thread-safety` change).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `rv-plugin-module-structure`: the module-layout responsibility table adds the session dialog to `utils.py`; the delegation requirement extends to menu callbacks — menu handlers SHALL delegate domain logic to controllers and keep only dialog/UI glue and `event.reject()`; a new scenario states that controller state SHALL NOT be re-exposed as plugin attributes (retiring the shim pattern).
- `openrv-sync-plugin`: new requirement — when the sync core fails to import, the plugin SHALL surface the failure in the OTIO Sync menu as a disabled/labelled state rather than presenting functional-looking session items.

## Impact

- **Code**: `rvplugin/ori_sync/plugin.py` (shrinks by ~150 lines: shim, dialog, clip construction), `rvplugin/ori_sync/sequence_sync.py` (gains `add_clip_from_path`), `rvplugin/ori_sync/utils.py` (gains the session dialog). **Revised during implementation**: `sequence_sync.py` and `annotation_sync.py` also needed their three unguarded module-level `otio_sync_core` imports wrapped. Without that the disabled-menu requirement below is unreachable — a missing sync core aborted the mode load before `_build_menu` ran, so RV showed *no* OTIO Sync menu at all rather than the "Sync Unavailable" item. Every other `otio_sync_core` import in those files was already guarded; these three were the outliers.
- **Protocol**: none.
- **Ordering**: independent of `xstudio-controller-encapsulation` (different codebase; can proceed in parallel). Should land before `session-roles` implementation touches the RV plugin, purely to avoid merge friction.
- **Testing**: two-client `sync_test/` suite unchanged; manual check of Add Clip and the import-failure menu state. RV loads the *installed* rvpkg copy, so `rvplugin/<pkg>/reinstall.csh` must be run before any in-RV verification, and the rvpkg `folders:`/`makepackage.csh` packaging must be confirmed to still include all modules (the packaging requirement already in `rv-plugin-module-structure`).
