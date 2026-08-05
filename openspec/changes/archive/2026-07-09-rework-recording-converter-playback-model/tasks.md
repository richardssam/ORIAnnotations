## 1. View→media resolution helper (otio_sync_core)

- [x] 1.1 Add a view→media resolution helper to `otio_sync_core` that, given an OTIO timeline/track, a view frame, and a view mode, returns `(clip, media_frame)` using OTIO's `child_at_time` / `transformed_time`, with a `_clip_effective_range`-style fallback (`source_range` else `media_reference.available_range`) for clips OTIO cannot transform.
- [x] 1.2 Handle `source` view mode as a direct offset into a single selected clip's effective range (no `child_at_time`).
- [x] 1.3 Raise a clear, identifying error when neither `source_range` nor `available_range` is known for the target clip.
- [x] 1.4 Unit-test the helper: `source_range=None` + embedded-timecode `available_range`, explicit trimmed `source_range`, `frameStart`≠0 base, and the missing-range error.

## 2. Playback projection model in the converter

- [x] 2.1 Introduce a projection-model object holding `active_timeline_guid`, `active_view_mode`, `playing`, last-seen `playback_mode`, and current view time; populate it from `STATE_SNAPSHOT` / `ADD_TIMELINE` / `REPLACE_TIMELINE` / `SELECTION` / `PLAYBACK_SETTINGS` events.
- [x] 2.2 Replace the scattered `current_segment_*` globals and `get_clip_sequence_start_time` math with segment emission driven by model transitions (play↔pause, timeline/selection change, boundary crossing, loop wrap).
- [x] 2.3 On flush, resolve the segment's view frame to a media frame via the Section 1 helper and write background-clip `source_range` in media frame space.

## 3. Sequence traversal

- [x] 3.1 While advancing a playing segment, detect clip-boundary crossings in the active track and split into one output clip per underlying media clip, each addressed in its own media frame space.
- [x] 3.2 Preserve segment compaction (one output clip per continuous media run) across cuts.

## 4. Loop-mode wrap

- [x] 4.1 When a playing playhead reaches the active sequence end and last-seen `playback_mode == "loop"`, wrap the view time to sequence start (frame 0) and continue emitting.
- [x] 4.2 When not looping, hold the final frame (freeze) until the next event.

## 5. Freeze frames and annotation overlays

- [x] 5.1 Re-anchor pause/scrub freeze clips (`LinearTimeWarp(0.0)`) to the resolved media frame instead of the raw view frame.
- [x] 5.2 Anchor annotation-overlay clips to the same resolved media frame so overlays sit on the drawn picture; keep the existing signature-grouping/gap-stretch overlay construction.

## 6. Fixtures and tests

- [x] 6.1 Update `tests/otio_sync/test_convert_recording.py` fixtures/assertions from view-frame source ranges to media-frame source ranges.
- [x] 6.2 Add a test asserting the sample recording (`demo-otioconvert-1.jsonl`) emits background source ranges inside the media's `available_range` (e.g. frame 31 → 98530).
- [x] 6.3 Add/synthesize a recording fixture that switches clips and runs off the sequence end while looping; assert traversal (Section 3) and loop wrap (Section 4).
- [x] 6.4 Add a test asserting the missing-range case fails with a non-zero exit and an identifying error.

## 8. Robustness fixes (found converting demo-otioconvert-2)

- [x] 8.1 Skip flush intervals whose active view is not yet resolvable (playback event names a timeline/clip before its snapshot or `ADD_TIMELINE` arrives) instead of failing; keep fail-loud for a loaded clip with no range.
- [x] 8.2 Read the `source`-mode selected clip from the `clip_guid` on the playback event, not only from `SelectionSet`.
- [x] 8.3 Regression tests: pre-snapshot playback event is skipped; source-mode `clip_guid` from playback resolves; multi-clip demo-2 converts with every clip in its own media range.

## 7. Migration and verification

- [x] 7.1 Regenerate any checked-in sample `.otio` outputs affected by the coordinate-space change.
- [x] 7.2 Run the converter on `demo-otioconvert-1.jsonl` and confirm in a player that media renders and annotations sit on the correct frame.
- [x] 7.3 Note the future task (out of scope here): honor an explicit in/out playback range on loop wrap instead of restarting at frame 0.
