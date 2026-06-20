## 1. Modify RV text event handling

- [x] 1.1 Update `on_rv_graph_state_change` in `plugin.py` to identify `.text:` updates distinctively from `.pen:` strokes.
- [x] 1.2 Prevent `.text:` changes from entering the `_pending_stroke` queue or starting the 50ms partial broadcast timer.
- [x] 1.3 Extract the logic from `_flush_pending_stroke` into a reusable method that reconstructs the frame's annotation state and broadcasts it immediately using `self.sync_manager.broadcast_replace_annotation_commands`.
- [x] 1.4 Trigger the immediate broadcast method whenever a text property changes.

## 2. Testing and Validation

- [x] 2.1 Rebuild the plugin package (`makepackage.csh`).
- [x] 2.2 Test typing text annotations in OpenRV and verify that they sync to a peer instantly and replace correctly without creating duplicates in the timeline.
