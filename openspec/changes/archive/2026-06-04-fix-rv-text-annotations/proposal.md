## Why

Text annotations created natively in OpenRV are not correctly broadcasted to the sync session. This happens because the current plugin logic incorrectly funnels text updates through the same queue used for brush strokes (the `_pending_stroke` queue). Text property updates lack a `.points` property, which causes the partial broadcast logic to silently abort. Furthermore, because text entry uses the keyboard rather than the mouse, it never triggers the mouse-release event (`on_rv_pen_up`) that the queue relies on to "flush" the data to the network. Consequently, unless the user subsequently clicks the mouse in the viewer, text annotations typed in RV simply vanish from the perspective of other synced peers. We need to fix this so text annotations bypass the brush stroke queue and sync immediately, aligning with how xStudio handles text annotations.

## What Changes

- Modify `plugin.py` to bypass the `_pending_stroke` queue and its `_send_partial_stroke` logic for `.text` property updates.
- Reconstruct the frame's annotation state immediately on text property change.
- Emit a `REPLACE_ANNOTATION_COMMANDS` message to correctly handle text updates without creating duplicate overlapping `TextAnnotation.1` events in the OTIO timeline.

## Capabilities

### New Capabilities

### Modified Capabilities
- `openrv-sync-plugin`: Update the OpenRV plugin specification to properly describe how native text annotations are captured, reconstructed, and broadcasted using `REPLACE_ANNOTATION_COMMANDS`.

## Impact

- `rvplugin/openrv_sync_plugin/plugin.py`: The `on_rv_graph_state_change` and `_broadcast_annotation` logic will be modified to support immediate broadcast of text annotations.
