## Context

The xStudio plugin (`ori_sync_plugin.py`) uses two overlapping mechanisms to detect locally-drawn annotations:

1. **Hot-scan** (`_hot_scan_active_annotation`): runs every 33 ms when `_hot_scan_active=True`. Reads `session.bookmarks.bookmarks`, finds the local bookmark on the active frame, diffs stroke counts against the last broadcast, and calls `manager.broadcast_partial_annotation`. Activated by `show_atom` or by `_on_core_annotation_event` (PaintStart/PaintPoint).

2. **Fallback scan** (`_flush_pending_annotations`): runs either after a debounce following an event (`_annotation_pending_time`), or every 1 second regardless of events. Reads all bookmarks, diffs against OTIO timeline, broadcasts completed strokes. The 1-second unconditional path was the primary detection mechanism for second strokes on existing bookmarks before AnnotationsCore events were wired.

In commit `59cf7fa`, `subscribe_to_plugin_events(AnnotationsCore, _on_core_annotation_event)` was added. AnnotationsCore's C++ `broadcast_live_stroke` sends `(event_atom, annotation_data_atom, user_id, stroke_completed)` to `plugin_events_group()` on every PaintStart, PaintPoint, and PaintEnd. The handler already:
- Sets `_annotation_pending_time` on `stroke_completed=True` (PaintEnd) → triggers flush after debounce
- Sets `_hot_scan_active=True` on `stroke_completed=False` (PaintStart/PaintPoint) → triggers hot-scan

The 1-second fallback scan was never updated to reflect that PaintEnd events now fire reliably.

## Goals / Non-Goals

**Goals:**
- Retire the 1-second blind scan as a primary detection path; raise `ANNOTATION_SCAN_INTERVAL` to 30 s
- Add observability: log the first AnnotationsCore event received per session
- Reduce partial-stroke broadcast latency by triggering an immediate hot-scan on PaintPoint events, not waiting for the next 33 ms tick
- Maintain the hot-scan for partial stroke streaming (no alternative — bookmark data must be read from xStudio API)

**Non-Goals:**
- Changing the hot-scan mechanism itself (still needed to stream partial strokes)
- Modifying the RabbitMQ protocol or message formats
- Changing the 33 ms poll interval (still needed for frame sync and other poll paths)
- Removing the `show_atom` fallback (keep as safety net for builds without the AnnotationsCore broadcast)

## Decisions

### Keep hot-scan, make it event-paced

**Decision**: Retain `_hot_scan_active_annotation()` called from the 33 ms poll loop. When a PaintPoint event arrives in `_on_core_annotation_event`, additionally enqueue an immediate `"hot_scan"` command to the poll-thread command queue so the next drain runs the scan without waiting up to 33 ms.

**Alternatives considered**:
- Call `_hot_scan_active_annotation()` directly from the event callback — rejected because the callback fires on xStudio's event thread, and the bookmark API calls inside the hot-scan are not thread-safe relative to the poll thread.
- Remove hot-scan entirely and only flush on PaintEnd — rejected because this breaks partial-stroke streaming to peers (they wouldn't see strokes in progress).

### Raise ANNOTATION_SCAN_INTERVAL to 30 s

**Decision**: Change the constant from `1.0` to `30.0`. The 30 s scan is a safety net for hypothetical missed events (e.g., a build that doesn't fire AnnotationsCore events, or a race where PaintEnd fires before annotation_data is committed). It is no longer a primary detection path.

**Alternatives considered**:
- Remove the fallback entirely — rejected; the 30 s cost is negligible and provides a recovery path.
- Make it conditional on `_core_events_received > 0` — too complex for marginal benefit.

### Observability via event counter

**Decision**: Add `_core_events_received: int = 0` to `__init__`. Increment in `_on_core_annotation_event`. Log `"[2C] First AnnotationsCore event received"` on the first event. This gives immediate confirmation in the log that the subscription is live.

## Risks / Trade-offs

- **AnnotationsCore events don't fire in some xStudio build** → Mitigation: `show_atom` fallback still activates hot-scan; 30 s fallback scan still catches missed strokes. Worst case latency is 30 s (was 1 s), but this scenario is unlikely given the C++ broadcast is unconditional.
- **PaintPoint enqueue rate** → Each PaintPoint fires one "hot_scan" command into the queue. At typical stroke speeds (tens of points/s) the queue stays small. The poll thread drains the whole queue each tick, so commands don't pile up.
- **annotation_data race on PaintEnd** → xStudio may not commit annotation_data before PaintEnd fires. The existing retry logic (up to 5 retries, re-set `_annotation_pending_time`) handles this. No change needed.

## Migration Plan

Single-file change to `ori_sync_plugin.py`. No data migration. No protocol changes. The RV plugin and sync_viewer are unaffected. Rollback: revert the constant and remove the counter.

## Open Questions

- Does the current xStudio build (commit being tested) have the `broadcast_live_stroke → plugin_events_group()` path? The log will now show `[2C] First AnnotationsCore event received` if yes. If the log never shows this, investigate whether `get_plugin("AnnotationsCore")` returns the right actor.
