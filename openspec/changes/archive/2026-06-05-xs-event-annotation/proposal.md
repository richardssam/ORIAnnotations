## Why

The xStudio plugin currently detects annotation changes via a 33 ms hot-scan poll and a 1-second fallback scan. The hot-scan burns CPU continuously during drawing, and the fallback scan means second strokes on an existing bookmark take up to 1 second to reach remote peers. AnnotationsCore's `plugin_events_` broadcast already delivers `(event_atom, annotation_data_atom, user_id, stroke_completed)` on every PaintStart, PaintPoint, and PaintEnd — the subscription infrastructure was wired in the latest commit but the blind fallback scan was never retired.

## What Changes

- Raise `ANNOTATION_SCAN_INTERVAL` from 1.0 s to 30.0 s (makes it a true last-resort safety net, not a primary detection path)
- Add `_core_events_received` counter; log "first AnnotationsCore event" so we can confirm the subscription is live in each session
- When a PaintPoint event arrives, enqueue an immediate hot-scan command via the poll-thread command queue (reduces partial-stroke broadcast latency from up to 33 ms to near-zero)
- Clean up the `show_atom` hot-scan activation path: keep it but mark it clearly as "fallback only" with a log prefix `[fallback]` to distinguish from event-driven activations
- Update class-level docstring and `ANNOTATION_SCAN_INTERVAL` comment to reflect new role

## Capabilities

### New Capabilities

- `xs-event-annotation`: Event-driven annotation detection in the xStudio plugin — AnnotationsCore `plugin_events_` events replace the 1-second blind scan as the primary stroke-completion signal; hot-scan activation is event-driven rather than `show_atom`-driven.

### Modified Capabilities

- `otio-annotation-sync`: The **latency** requirement for annotation delivery changes — second strokes on an existing bookmark must reach remote peers within the hot-scan interval (~33 ms) rather than up to 1 second.

## Impact

- `xstudio_plugin/ori_sync/ori_sync_plugin.py` — only file changed
- No protocol changes; no RabbitMQ message format changes
- No changes to `otio_sync_core`, RV plugin, or sync_viewer
