## 1. Subscription rewiring

- [x] 1.1 Replace the two `subscribe_to_plugin_events` calls in `ori_sync_plugin.py` with a single `subscribe_to_annotation_draw_events(self._on_annotation_draw_event)`, keeping the `"Subscribed to AnnotationsCore plugin events [2C]"` log line the spec requires
- [x] 1.2 Keep the `get_plugin("AnnotationsUI")` lookup that populates `_ann_ui_plugin` for `display_sync`, without subscribing to it
- [x] 1.3 Replace the `_on_annotation_event` / `_on_core_annotation_event` wrappers with `_on_annotation_draw_event(event_data, user_id, stroke_completed)`

## 2. Handler contract

- [x] 2.1 Add `AnnotationSyncController.on_draw_event` dispatching on whether `stroke_completed` is present
- [x] 2.2 Emit `"[2C] First AnnotationsCore event received"` on the first event of a session (previously specified but never reached)
- [x] 2.3 Change `on_annotation_event` to take the decoded interaction payload; drop the tuple guards and the `[TEST annotation_atom]` probe log
- [x] 2.4 Change `on_core_annotation_event` to take `(anno_json, stroke_completed)`; drop the tuple guards, the legacy 4-tuple branch, and the 3-tuple clear branch
- [x] 2.5 Drop the now-unused `event_atom`, `annotation_atom`, `annotation_data_atom` imports
- [x] 2.6 Reword the interaction log lines that claimed the event came from AnnotationsUI

## 3. Live verification

- [x] 3.1 Drive a synthetic PaintStart/PaintPoint/PaintEnd gesture into AnnotationsCore against a running xStudio with the plugin loaded (`xstudio/scratch/annotation_event_route_probe.py`)
- [x] 3.2 Confirm the log shows `"[2C] First AnnotationsCore event received"` — the check archived change `2026-06-05-xs-event-annotation` left unticked
- [x] 3.3 Confirm mid-stroke partials broadcast and grow (`points=4` → `points=12`)
- [x] 3.4 Confirm pen-up schedules the flush
- [x] 3.5 Confirm `HideDrawings`/`ShowDrawings` reach `on_annotation_event` and broadcast visibility rather than scheduling a scan — visibility confirmed syncing by manual test
- [x] 3.6 Confirm the clear gesture produces a `PaintClear` interaction and that the count-decrease scan then broadcasts the replace — confirmed by manual test via both the clear hotkey (Cmd+D on macOS) and the toolbox Clear button; both call `AnnotationsUI::clear_annotation()` → `send_event("PaintClear", …)` (annotations_ui_plugin.cpp:311, :485, :639). Note the hotkey is guarded by `current_tool() != None`
- [x] 3.7 Confirm from a log capture which path carried 3.5 and 3.6 — confirmed: `Draw interaction: HideDrawings — broadcasting visibility` with no scan scheduled after it, and `Draw interaction (event='PaintClear') — scheduling broadcast scan` firing immediately on the gesture. Partials grow 4 → 128 → 240 points; every pen-up schedules the flush

## 4. Documentation

- [x] 4.1 Correct `docs/xstudio_constraints.md` — rewritten around the draw-events group, with the "nothing is ever broadcast on a `plugin_events_` group" rule stated up front, the unreachability of `live_edit_event_group_` recorded, and the count-decrease section extended with bookmark disappearance and its two traps
- [x] 4.2 Update the `[2C]` entry in `TODO.md` — marked done, and corrected: it specified `live_edit_event_group_` via `join_broadcast_atom() + annotation_atom()`, which Python cannot reach
- [x] 4.3 Refresh the stale `_on_annotation_event` inspection recipe in `xstudio_plugin/ori_sync/README.md` — replaced with the two payload shapes and how to raise the raw-event log cap

## 5. Now that the event path is confirmed live

- [x] 5.1 Restore `ANNOTATION_SCAN_INTERVAL` to 30 s. Safe now that both the draw events and the disappearance diff (`xs-detect-deleted-bookmarks`) are live, so the scan is genuinely a safety net again
- [x] 5.2 Stop logging every draw interaction — `PaintPoint` added to `_HIGH_RATE_EVENTS` and skipped. Confirmed live: 0 `PaintPoint` log lines across five strokes, against 130 for three strokes before
- [x] 5.3 Confirm the fallback scan does not trigger between strokes — confirmed: scans now only follow events (9 across a five-stroke session, none during a 5 s idle gap), where before it was one per second continuously
- [ ] 5.4 Consider not scheduling a scan on `PaintPoint` at all. Points still refresh the debounce timestamp, so a stroke whose points arrive slower than the 250 ms debounce triggers a scan per point — visible with the synthetic probe (points 0.4 s apart → a scan every ~0.4 s). Harmless (nothing is committed mid-stroke to find) and invisible at real pointer rates, but it is wasted work, and the `[2C]` live-stroke path already covers mid-stroke

## 6. Follow-up found during verification

- [x] 6.1 Investigate why `PaintClear` produced no `REPLACE_ANNOTATION_COMMANDS` — **root cause found**. A clear on a drawing with no note text deletes the whole bookmark: `AnnotationsCore::clear_annotation` computes `bookmark_is_empty = !(detail.note_ && !detail.note_->empty())` and `ClearAnnotation::redo` calls `plugin_->remove_bookmark(bm_id_)` (annotations_core_plugin.cpp:1459-1460, :1514). ORIAnnotations detects a clear as a stroke-count *decrease* while iterating `session.bookmarks.bookmarks`, and `flush_pending_annotations` returns early at `if not scan_uuids: return` when the list is empty — so a deleted bookmark is invisible and nothing is ever broadcast. Confirmed live with `xstudio/scratch/annotation_clear_probe.py`: bookmark list goes `[] → [25902086…] → []` across draw/clear, and the plugin log shows `Draw interaction (event='PaintClear') — scheduling broadcast scan` followed by no scan output at all. Peers keep the annotation indefinitely
- [ ] 6.2 Establish whether the absence of a committed `Annotation.1` broadcast after pen-up is by design — the clear-probe session sent none, but an `OTIO_SESSION_1.0` update followed the pen-up scan, so the committed state may already propagate that way
- [x] 6.3 Fix the send side — split out as the `xs-detect-deleted-bookmarks` change and landed in `ef43d8e`. Implemented as: track the (clip, frame) keys last broadcast, diff against the surviving bookmark set on each scan, and emit an empty `REPLACE_ANNOTATION_COMMANDS` for keys that have vanished. The receive side already specifies this — `annotation-lifecycle-sync` requires an empty replace to be applied as an authoritative hard clear. Note this also covers deleting a note from the notes panel, and belongs in `annotation-lifecycle-sync` scope — likely its own change rather than this one

## 7. Regression check

- [ ] 7.1 Run the annotation tests in `sync_test/sync_tests.yaml` against the develop xStudio build and record which failures pre-date this change
