## 1. Confirm the shim is unreferenced

- [x] 1.1 Re-run the reference scan for all 16 forwarded names across the repo (`_rv_node_to_timeline_guid`, `_sequence_input_order`, `_sg_to_path_cache`, `_sequence_settle_until`, `_active_media_track_guid`, `_track`, `_last_broadcast_frame`, `_last_selection`, `_last_display_state`, `_pending_stroke`, `_next_stroke_uuid`, `_stroke_timer`, `_last_partial_point_count`, `_partial_pen_nodes`, `_last_sent_replace_sig`, `_ignore_annotations_until`) and confirm the only non-controller-local hits are the already-direct `self.plugin.<controller>.<attr>` reads plus `do_add_clip`'s `self._active_media_track_guid`
- [x] 1.2 Sweep for dynamic access that a name-based grep would miss: `getattr(`/`setattr(` applied to a plugin or mode object anywhere in `rvplugin/`, `python/otio_sync_core/`, and `sync_test/`; record the result so a later `AttributeError` isn't re-investigated from scratch

## 2. Move clip construction into SequenceSyncController

- [x] 2.1 Add `SequenceSyncController.add_clip_from_path(path)` to `sequence_sync.py`: `rv.commands.addSource(path)`, the fps/in-point/out-point time-range derivation with its existing `except` fallback, `otio.schema.Clip` construction, and `self.plugin.sync_manager.insert_child(self._active_media_track_guid, clip)`; return the inserted clip, or `None` when there is no sync manager or no active media track
- [x] 2.2 Reduce `plugin.py`'s `do_add_clip` to the `not self.sync_manager` early-out, `openFileDialog`, the cancel path, `self.sequence.add_clip_from_path(path)`, and `event.reject()` — no OTIO imports, no time arithmetic
- [x] 2.3 Confirm `import opentimelineio.opentime as otio_time` is no longer needed inside `do_add_clip` and that `sequence_sync.py` already has the imports the moved code requires

## 3. Delete the property-forwarding shim

- [x] 3.1 Delete the ~25 `@property`/setter pairs at `plugin.py` lines 93–204, keeping the `_in_session` property (it derives from plugin-owned `sync_manager` and is not part of the shim)
- [x] 3.2 Grep `plugin.py` for any remaining bare `self._<shimmed name>` read or write and repoint it at the owning controller
- [x] 3.3 Byte-compile every module in `rvplugin/ori_sync/` and confirm no `NameError`/`AttributeError` surfaces from the deletion

## 4. Move the session dialog to utils.py

- [x] 4.1 Add `session_dialog(title)` to `utils.py` as a verbatim move of `_session_dialog`, keeping the function-local `PySide2`→`PySide6` import chain and the `ORI_RMQ_HOST` default
- [x] 4.2 Delete `_session_dialog` from `plugin.py`; call `session_dialog(...)` directly from `do_create_session` and `do_join_session` — do not add a forwarding wrapper
- [x] 4.3 Add `session_dialog` to `plugin.py`'s `from utils import ...` line

## 5. Surface sync-core import failure in the menu

- [x] 5.1 In `plugin.py`'s `except ImportError` block, record the exception text in a module-level `_SYNC_IMPORT_ERROR` and write it to **both** `_log` and `sys.stderr` (today's `_log`-only path is silent unless `ORI_SYNC_LOG_FILE`/`DEBUG_OTIO_SYNC` is set)
- [x] 5.2 Make `_build_menu` return the unavailable menu as its first branch when `_SYNC_IMPORT_ERROR` is set: a single `DisabledMenuState` item labelled `Sync Unavailable (otio_sync_core import failed)`, with no Create/Join/Add Clip items
- [x] 5.4 Guard the three unguarded module-level `otio_sync_core` imports (`sequence_sync.py`'s `font_size_to_rv`; `annotation_sync.py`'s `STATE_SYNCED` and `rv_annotation_codec`/`rv_paint_applier`) so a missing core cannot abort the mode load before the unavailable menu is built — discovered while implementing 5.2; every other `otio_sync_core` import in those files was already guarded
- [x] 5.5 Add `tests/otio_sync/test_sync_unavailable_menu.py` covering the sentinel, the single disabled item, the absence of Create/Join/Add Clip, and the unchanged available menu
- [x] 5.3 Confirm the existing `elif not SyncManager or not RabbitMQNetwork` branch in `__init__` and the guard at the top of `connect_to_session` still behave correctly alongside the new sentinel

## 6. Verify in RV

- [x] 6.1 Run `rvplugin/ori_sync/reinstall.csh` — RV loads the installed rvpkg copy, not the repo source, so nothing below tests this change until this is done
- [x] 6.2 Confirm `makepackage.csh`'s hand-maintained zip list still matches the module set (no module was added or removed by this change; this is a regression check, not an edit)
- [x] 6.3 Launch RV and confirm the OTIO Sync menu shows the normal disconnected items and the plugin's startup banner reports the expected module copy
- [x] 6.4 Exercise Create Session and Join Session end-to-end to confirm the relocated dialog still builds, focus-chains, and returns `(host, name)` / `(None, None)` on cancel
- [x] 6.5 Exercise "Add Clip to Timeline…" against a connected peer and confirm the peer receives an `insert_child` with the same clip name, target track, and time range as before the change; also confirm the file-dialog cancel path calls no controller
- [x] 6.6 Re-run `reinstall.csh` (sources changed after the last install), then force the import-failure path once (e.g. temporarily rename `otio_sync_core` inside the *installed* package), confirm the disabled "Sync Unavailable" menu and the stderr line, then restore and re-run `reinstall.csh`

## 7. Regression and close-out

- [x] 7.1 Run the two-client `sync_test/` suite and confirm no change versus the pre-change baseline; sample free memory/swap alongside the run so a swap-induced slowdown isn't misread as a regression from this change
- [x] 7.2 Run `openspec validate rv-plugin-encapsulation --strict` and confirm the delta specs parse
- [x] 7.3 Confirm `plugin.py` has shrunk to session lifecycle, menus, event-handler registration, `poll_network`, and `_handle_action`, with no OTIO construction or Qt widget assembly remaining
