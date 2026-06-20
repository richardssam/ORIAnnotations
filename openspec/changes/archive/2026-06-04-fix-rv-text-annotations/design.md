## Context

Text annotations created in OpenRV currently fail to sync correctly. The existing plugin logic batches strokes (using a `_pending_stroke` queue and a 50ms timer) to emit partial broadcasts. However, text property updates lack a `.points` attribute, causing the partial broadcast logic to silently abort. Furthermore, text annotations don't trigger mouse release events (`on_rv_pen_up`), meaning the `_pending_stroke` is never flushed. When they are finally flushed (if the user clicks elsewhere), they use `broadcast_add_annotation`, which creates duplicate overlapping events in the OTIO timeline instead of replacing the text. We need to align RV's behavior with xStudio's by emitting immediate `REPLACE_ANNOTATION_COMMANDS` for text changes.

## Goals / Non-Goals

**Goals:**
- Reliably broadcast text annotation updates from RV to the sync session in real-time.
- Prevent duplicate `TextAnnotation.1` events from piling up in the OTIO timeline.

**Non-Goals:**
- Modifying the sync protocol or OTIO schema.
- Changing how painted brush strokes (pen tool) are broadcasted.

## Decisions

**Decision 1: Immediate broadcast for text annotations.**
Instead of batching text annotations through the `_pending_stroke` partial system, we will immediately fetch the frame's annotation state and broadcast it on every keystroke (`graph-state-change` for `.text:`).
*Rationale:* Text strings are small, and treating them like partial painted points is unnecessary. The `REPLACE_ANNOTATION_COMMANDS` message correctly captures text state in real-time.
*Alternatives:* Use a debounce timer. While slightly more efficient, it adds complexity and delays real-time sync visibility.

**Decision 2: Use `broadcast_replace_annotation_commands` instead of `broadcast_add_annotation` for text.**
When a text node changes, we will reconstruct all annotation events for that frame (including paint strokes and the new text) and use the sync manager's replace command.
*Rationale:* This prevents the timeline from accumulating redundant text events for every character typed. This aligns exactly with how xStudio manages text annotation updates.

## Risks / Trade-offs

- [Risk] Immediate broadcast on every keystroke might generate excessive network traffic if typed very quickly. → Mitigation: Text event payloads are very small. Testing shows it is perfectly viable over local networks. We can consider adding a debounce timer in the future if this causes performance issues.
- [Risk] Replacing all commands for a frame might overwrite incoming concurrent strokes from other users on the exact same frame. → Mitigation: This is a known limitation of the current frame-level annotation model and affects paint strokes as well.
