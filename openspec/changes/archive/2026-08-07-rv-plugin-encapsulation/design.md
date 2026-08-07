## Context

See `proposal.md` — Why. Three facts from the code shape the approach:

1. **The shim really is dead.** A repo-wide scan of all 16 forwarded names shows every cross-controller read already uses the owning controller directly (`self.plugin.sequence._rv_node_to_timeline_guid` in `playback_sync.py`, `self.plugin.annotation._ignore_annotations_until` in `sequence_sync.py`). `plugin.py` itself bypasses its own shim everywhere — `self.sequence._sg_to_path_cache`, `self.display._last_display_state`, `self.annotation._ignore_annotations_until` — with exactly one exception: `do_add_clip`'s `self._active_media_track_guid` (`plugin.py:727`). Deleting the shim is therefore a one-call-site migration, not a refactor.

2. **`plugin.py` is imported by RV as a top-level module, not a package.** Imports are bare (`from utils import _log`), there is no `__init__.py`, and `PACKAGE` lists only `plugin.py` in `modes:`. Anything moved out of `plugin.py` has to land in an already-packaged sibling module — hence `utils.py` for the dialog and `sequence_sync.py` for the clip construction, rather than a new module that would need `PACKAGE`/`makepackage.csh` changes.

3. **The import guard is a bare `except ImportError` that assigns `None` sentinels** (`plugin.py:16-24`) and then only logs. `_build_menu` never consults those sentinels, so a plugin with a broken `otio_sync_core` presents a fully populated, fully inert menu. `connect_to_session` does guard (`plugin.py:247`), so clicking Create/Join is a silent no-op — the exact failure signature of the vendored-`pika` incident.

Constraint carried through the whole change: RV loads the **installed** rvpkg copy, so nothing is verifiable in RV until `reinstall.csh` has run.

## Goals / Non-Goals

**Goals:**

- One name per piece of state — controller attributes only, no plugin-level aliases.
- `plugin.py` reads as lifecycle + dispatch: no OTIO construction, no Qt widget assembly.
- A broken sync core is visible at the menu, not just in a log file nobody opens.
- Zero wire-format change: peers see the same messages before and after.

**Non-Goals:**

- Renaming the `_`-prefixed controller attributes themselves. They stay private-by-convention; this change removes the *duplicate* names, not the underscore convention.
- Making `plugin.py` shorter for its own sake — `poll_network` and `_handle_action` are dispatch and stay put, per the existing `openrv-sync-plugin` Asynchronous Polling requirement.
- The `_rv_updating` context-manager conversion (owned by `session-roles`) and network threading (`rv-network-thread-safety`), per the proposal's out-of-scope list.

## Decisions

### Delete the shim outright rather than deprecate it

**Chosen:** remove all ~25 `@property`/setter pairs in one edit; migrate the single live consumer.

**Alternative — keep the shim but mark it deprecated:** rejected. There are no external importers of `plugin.py` (RV instantiates it via `createMode()`), so there is no compatibility surface to deprecate against. A deprecation window would preserve exactly the ambiguity the change exists to remove.

**Alternative — invert it, moving state onto the plugin and forwarding from controllers:** rejected. It contradicts the existing "Delegated controller pattern" and "Shared state ownership" requirements, which deliberately keep only `_rv_updating` and `sync_manager` on the plugin.

`_in_session` is a `@property` too (`plugin.py:206-208`) but is **not** part of the shim — it derives from `sync_manager`, which the plugin owns. It stays.

### `add_clip_from_path` owns the RV source add as well as the OTIO construction

**Chosen:** `SequenceSyncController.add_clip_from_path(path)` performs `rv.commands.addSource`, the fps/in-point/out-point time-range derivation, the `otio.schema.Clip` construction, and `sync_manager.insert_child(self._active_media_track_guid, clip)`. The menu callback keeps `openFileDialog`, the `not self.sync_manager` early-out, and `event.reject()`.

**Rationale:** the `addSource` call and the time-range derivation are coupled — the in/out points read at `plugin.py:718-719` are the ones RV sets *as a result of* the preceding `addSource`. Splitting them across the callback boundary would leave a subtle ordering dependency in two files. `sequence_sync.py` already owns every other path from media to track (`_make_clip`, `_apply_insert_child`, `_path_to_source_group_map`).

**Return value:** the method returns the inserted clip (or `None` when no sync manager / no active track). The callback ignores it; a return value keeps the method testable without RV.

### The unavailable menu is built from the same `_build_menu`, gated first

**Chosen:** add a module-level `_SYNC_IMPORT_ERROR` string set in the existing `except ImportError` block, and make `_build_menu` return the unavailable menu as its first branch when it is set.

**Alternative — skip `init()`'s menu argument entirely when the import failed:** rejected. RV would then show no OTIO Sync menu at all, which is indistinguishable from "the package didn't load", the second-worst diagnostic after the current silent one.

**Alternative — raise from the import block:** rejected. RV would log a traceback and drop the mode, losing the ability to tell the user anything in-app.

The item is a single `DisabledMenuState` entry. Label: `Sync Unavailable (otio_sync_core import failed)`. Menu labels cannot carry the exception text usefully, so the exception goes to `_log` **and** stderr — the existing block only calls `_log`, which is a no-op unless `ORI_SYNC_LOG_FILE`/`DEBUG_OTIO_SYNC` is set, meaning today's failure can be completely invisible. That stderr line is the difference between a five-minute and a five-hour diagnosis.

### The dialog helper keeps its lazy PySide import

`_session_dialog` moves to `utils.py` as `session_dialog(title)` (public name — it is now a cross-module call). Its function-body `PySide2`/`PySide6` import stays function-local rather than joining `utils.py`'s module-level `QtCore` try/except: `QtWidgets` is only needed when a dialog is actually shown, and keeping it lazy preserves importability of `utils.py` in the headless contexts that already import it for `_media_path` and `_clip_effective_range`.

`plugin.py` keeps a thin `_session_dialog` wrapper? **No** — the two callers (`do_create_session`, `do_join_session`) call `session_dialog(...)` directly. A wrapper would be a new forwarder, which is precisely the pattern this change deletes.

### Spec corrections folded in

Two pre-existing inaccuracies in `rv-plugin-module-structure` are fixed while the requirements are being rewritten: the module table omitted `color_sync.py` (present since the colour-pipeline work and already in `makepackage.csh`), and the packaging requirement hard-coded "all 6 Python modules" when there are 7. The packaging requirement is restated enumeration-free so it stops drifting. This is spec-accuracy work, not scope creep — but it does mean this change's diff touches a requirement the proposal did not name.

## Risks / Trade-offs

- **A shim consumer exists that the scan missed** (dynamic `getattr`, string-built attribute name, or an RV-side caller reaching into the mode object) → the scan covered all 16 names across the repo with the receiver-agnostic pattern `\.<name>\b`; the only hits outside the owning controllers are the four already-direct cross-controller reads. Residual risk is `getattr(plugin, name)` with a computed name, for which a `grep -rn "getattr(.*plugin" ` sweep is a task step. Failure mode is a loud `AttributeError`, not silent drift.

- **`add_clip_from_path` changes the clip's inserted contents** → the method is a literal move of `plugin.py:713-727`; the `_active_media_track_guid` read becomes `self._active_media_track_guid` inside the controller (same object, one fewer hop). The spec scenario pins "peers observe an identical `insert_child` message" so a behavioural diff is a spec failure, not a judgement call.

- **The moved dialog silently stops working under PySide6** → the move is verbatim including the `PySide2`→`PySide6` fallback chain; the manual Create/Join check in the task list exercises it.

- **`makepackage.csh` isn't touched, so the change ships stale** → no module is added or removed by this change, so the existing list stays correct. The risk is the inverse — assuming that means no reinstall is needed. `reinstall.csh` is a mandatory task step before any in-RV verification, per `feedback_rvpkg_reinstall_before_test`.

- **The unavailable menu can't be reached in normal testing** → forcing it means breaking the import deliberately (e.g. renaming the vendored `otio_sync_core` in the *installed* package, then restoring). This is a manual step and it is worth doing once: the whole point of the requirement is a path that only executes when something is already wrong.
