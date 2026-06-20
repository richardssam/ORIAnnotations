## 1. Raise fallback scan interval

- [x] 1.1 Change `ANNOTATION_SCAN_INTERVAL` from `1.0` to `30.0` in `ori_sync_plugin.py`
- [x] 1.2 Update the class-level docstring comment for `ANNOTATION_SCAN_INTERVAL` to say "safety-net fallback" rather than primary detection path

## 2. Add AnnotationsCore event observability

- [x] 2.1 Add `self._core_events_received: int = 0` to `__init__` (near the other annotation state fields)
- [x] 2.2 In `_on_core_annotation_event`, increment `_core_events_received` and log `"[2C] First AnnotationsCore event received"` on the first increment only

## 3. Immediate hot-scan on PaintPoint

- [x] 3.1 Add `"hot_scan"` as a recognised command in `_drain_cmd_queue` — when drained, call `_hot_scan_active_annotation()` once
- [x] 3.2 In `_on_core_annotation_event`, when `stroke_completed=False` and the hot-scan is already active (or just activated), put `("hot_scan", None)` onto `_cmd_queue` to trigger an immediate scan on the next drain

## 4. Clean up show_atom fallback path

- [x] 4.1 In `_on_global_playhead_event`'s `show_atom` block, change the log message from `"Hot scan activated at frame {frame} (show_atom fallback)"` to `"[fallback] Hot scan activated at frame {frame} via show_atom"` to distinguish it in logs from the event-driven path

## 5. Verify and test

- [ ] 5.1 Run the xStudio plugin with a live session; draw a stroke on a new frame → confirm log shows `"[2C] First AnnotationsCore event received"` and `"[2C] AnnotationsCore: pen-up"` shortly after pen-up
- [ ] 5.2 Draw a second stroke on the same frame → confirm peer receives it within ~250 ms (not after 30 s fallback)
- [ ] 5.3 Draw a stroke while watching the log → confirm `"[2C] AnnotationsCore: mid-stroke"` fires during drawing and `hot_scan` commands appear in the drain log
- [ ] 5.4 Confirm the fallback scan log line does NOT appear during normal drawing sessions (only after 30 s of idle)
