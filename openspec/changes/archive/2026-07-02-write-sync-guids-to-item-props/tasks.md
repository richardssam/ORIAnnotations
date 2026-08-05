## 1. Verify item_prop round-trips to exported OTIO

- [x] 1.1 On a live xStudio sequence, set a test `item_prop` (e.g. `{"sync": {"guid": "test"}}`) on a clip item and confirm `xs_tl.to_otio_string()` emits it as that clip's `metadata["sync"]["guid"]`
- [x] 1.2 Confirm the `item_prop` setter merges vs. replaces the item's prop dict; decide read-merge-write is required (per design)

## 2. Write sync GUIDs into xStudio item props

- [x] 2.1 Add a helper in `timeline_build.py` (e.g. `_write_sync_item_props(xs_tl, tl)`) that walks `xs_tl.tracks` in lockstep with `tl.tracks`, and each track's `.children` Clips in lockstep with the OTIO Clip children
- [x] 2.2 For each track and clip, read-merge-write `item_prop`: get current props, `setdefault("sync", {})["guid"] = <guid>`, write back
- [x] 2.3 Wrap every `item_prop` get/set with `utils.bounded(...)`; on timeout/error log via `_log_exc` and skip that item (best-effort, non-fatal)
- [x] 2.4 Call the helper immediately after GUID assignment in `build_otio_timelines` (after the track/clip loop around line 291)
- [x] 2.5 Call the helper after GUID assignment in `build_single_sequence_otio` (after the track/clip loop around line 513)
- [x] 2.6 Ensure the walk skips non-Clip children (Gaps) consistently with the OTIO Clip enumeration so indices stay aligned

## 3. Read sync GUID in polling functions

- [x] 3.1 In `poll_sequence_reorders`, replace the URL/stem matching loop (lines ~423-458) with a direct read of `clip.metadata.get("sync", {}).get("guid")` from each exported clip
- [x] 3.2 Keep a narrow URL/stem fallback only for clips whose exported metadata has no sync guid; do not treat the sequence as empty in that case
- [x] 3.3 Applied guid-first + fallback to the URL-matching loop in `poll_sequence_new_media` (track-dragging path, ~lines 1087-1107) — the actual broken code at those lines; `poll_sequence_track_deletions` uses name matching and needed no change
- [x] 3.4 `poll_sequence_source_ranges` uses a source-range fingerprint, not clip identity matching — it benefits from item_prop GUIDs being present in the new OTIO built by `build_single_sequence_otio` with no direct code changes needed
- [x] 3.5 URL-matching code retained as a guarded fallback for newly-added clips; it is not dead code

## 4. Verify

- [x] 4.1 Run a two-client sync (xStudio master), reorder clips, and confirm `current_order` is populated and `MOVE_CHILD` mutations broadcast (previously empty)
- [x] 4.2 Confirm source-range edits on the xStudio client are detected and broadcast
- [x] 4.3 Confirm a track deletion on the xStudio client is detected and broadcast
- [x] 4.4 Confirm a freshly-added clip (no item_prop yet) still resolves via the fallback for one cycle, then via its guid after the next build
- [x] 4.5 Run the relevant `sync_test` scenarios to confirm no regressions in reorder/source-range suites
