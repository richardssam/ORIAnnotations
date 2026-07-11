## Why

Both OpenRV ("Clear Frame" / "Clear All Frames on Timeline" in the Annotate mode, plus the per-source "Show Drawings" toggle) and xStudio (Ctrl+D "Delete all strokes", plus the 'V' "Toggle annotation visibility" hotkey) let a user delete or hide annotations locally, but neither action is currently tracked by the sync layer. A peer that deletes or hides annotations sees no change reflected on other peers, leaving stale strokes/text visible remotely after the local user believed they were gone.

## What Changes

- Bind RV's `clear-paint` and `clear-all-paint` internal events (currently unbound — `on_graph_state_change` explicitly ignores `.order`-only property changes) and rebuild the affected annotation clip(s)' surviving `annotation_commands`, minus the soft-deleted stroke uuids RV already reports.
- Stop discarding `data["event"]` in xStudio's `on_annotation_event`; distinguish `PaintClear` from ordinary stroke-commit events and rebuild the affected bookmark's surviving commands the same way.
- Broadcast per-clip deletions (partial or full) via the existing `broadcast_replace_annotation_commands` / `REPLACE_ANNOTATION_COMMANDS` message — no new wire message type.
- Fix the "fully empty" gap this reuse exposes: both `rv_paint_applier.apply_specs` (reconcile mode) and xStudio's `refresh_annotation_bookmark` currently infer "nothing to prune" from an empty incoming command list, so a REPLACE that empties a clip's annotations entirely is silently a no-op today. Add an explicit hard-clear branch on each receiver for a genuinely-empty payload, without changing the ambiguous-by-omission behavior existing partial-replace callers (e.g. text-only edits) rely on.
- Add a synced, session-wide `annotations_visible` flag to `display_settings` (the existing shared display-state blob), toggled by RV's per-source "Show Drawings" property and xStudio's `annotations_visible_` module attribute. RV's per-source toggle is broadcast/applied session-wide (every `RVPaint` node's `paint.show`), matching xStudio's actual global toggle rather than introducing new per-clip visibility state.
- **BREAKING**: none — this only adds broadcast/apply behavior for actions that previously had no sync effect at all.

## Capabilities

### New Capabilities
- `annotation-lifecycle-sync`: Detecting local annotation deletion (partial or full clear) and visibility toggling in both RV and xStudio, and broadcasting/applying that state across peers.

### Modified Capabilities
- `otio-annotation-sync`: `REPLACE_ANNOTATION_COMMANDS` gains defined semantics for a fully-empty command list (authoritative "this clip has no annotations left") distinct from the existing partial-replace omission semantics.
- `rv-annotation-codec`: `apply_specs` reconcile mode needs an explicit empty-payload hard-clear path (wipe the frame's `order` property) alongside its existing kind-inferred prune logic.
- `xs-event-annotation`: the plugin must discriminate `PaintClear` / `HideDrawings` / `ShowDrawings` from ordinary stroke-commit events instead of treating every `AnnotationsUI` event identically.
- `openrv-sync-plugin`: new bindings for `clear-paint`, `clear-all-paint`, and the `paint.show` property change, alongside the existing `graph-state-change` annotation handling.

## Impact

- `rvplugin/ori_sync/annotation_sync.py`, `rvplugin/ori_sync/plugin.py` (new event bindings, delete/visibility detection and broadcast)
- `xstudio_plugin/ori_sync/annotation_sync.py`, `xstudio_plugin/ori_sync/ori_sync_plugin.py` (event discrimination, delete/visibility detection and broadcast)
- `python/otio_sync_core/rv_paint_applier.py` (hard-clear branch for empty reconcile payload)
- `python/otio_sync_core/manager.py` (display_settings field addition; possible helper for building a clip's surviving command list)
- No changes to `otio_event_plugin` schema — no new `SyncEvent` type is introduced.
