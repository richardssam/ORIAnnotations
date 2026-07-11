## Why

A late-joining RV client whose session snapshot includes a text annotation crashes its own join-time rebuild: `sequence_sync.py`'s annotation-replay pass (`_rebuild_rv_session`, called from the `on_synced` callback) calls `self.plugin.annotation._text_uuid_exists_in_rv(...)`, a method that does not exist anywhere in `annotation_sync.py` — it is a dangling reference, never implemented. The resulting `AttributeError` is caught only by `SyncManager._set_status`'s broad `try/except` around the entire `on_synced` callback, several layers above where it's thrown, so it silently aborts *everything else* that callback does: any remaining annotation-clip replay after the failing one, and the playback-state/display-state/color-sync application that runs after `rebuild_rv_session()` returns in `plugin.py::_on_synced`. Discovered while spot-checking text/shape annotations during verification of the separate `fix-annotation-render-on-join` change — pen-only sessions never hit this line, which is presumably why it has gone unnoticed.

Separately, the same line's `_find_paint_node_for_media(media_path, frame)` call now returns a `(node, native_frame)` tuple (per `fix-annotation-render-on-join`), so `paint_node` here needs unpacking regardless of the missing-method fix, or it will pass a tuple where a node-name string is expected.

## What Changes

- Implement `AnnotationSyncController._text_uuid_exists_in_rv(node, frame, uuid_val)` in `rvplugin/ori_sync/annotation_sync.py`: scan the paint node's `frame:<frame>.order` list for a `text:` item whose `.uuid` property matches, so the pre-existing duplicate-guard comment at the call site (`sequence_sync.py`) is finally backed by working code.
- Fix the call site in `sequence_sync.py`'s annotation-replay pass to unpack `_find_paint_node_for_media`'s `(node, native_frame)` return correctly.
- Isolate each annotation event's replay in that pass with its own `try/except` (matching the pattern already used elsewhere in this file for per-node graph operations), so one bad or unexpected event can no longer silently abort structure rebuild, playback sync, display sync, or color sync for the rest of a joining peer's session.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `openrv-sync-plugin`: "Late joiner synchronization" gains a resilience requirement — a failure replaying one annotation clip must not prevent the rest of the join (structure, playback, display, color) from completing.

## Impact

- `rvplugin/ori_sync/annotation_sync.py` (new method)
- `rvplugin/ori_sync/sequence_sync.py` (call-site fix + per-event error isolation in the annotation-replay pass)
- `openspec/specs/openrv-sync-plugin/spec.md`
- Depends on `fix-annotation-render-on-join` having landed first (it changed `_find_paint_node_for_media`'s return shape, which this call site must match).
