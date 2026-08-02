## 1. Establish the frame convention

- [x] 1.1 Confirm on both senders whether a source-view `current_time.value` is clip-local or sequence-relative (design.md's open question). Everything else depends on this; write the receiver's translation only once it is answered

  **Answer: clip-local on both senders, and no receiver translation is needed.**
  - xStudio sends `ph.position` where `ph` is the *isolated clip's* playhead (source view switches the on-screen source to the single clip), so the value is 0-based within the clip.
  - RV sends `current_frame - self._frame_base()`, and `_frame_base()` is `rv.commands.frameStart()` of the **current view** — in source view that is the source's own first frame (89899 for timecode media, 1 otherwise). So RV's value is also 0-based within the clip.
  - RV's receive path already re-reads `frameStart()` *after* the view switch ([playback_sync.py:175-180](rvplugin/ori_sync/playback_sync.py#L175-L180)), so the same base conversion runs in reverse. The translation is already correct **provided the view switch lands first** — which is the actual dependency, not the frame convention.
  - The harness normalises RV to the same space: `frame() - frameStart() + 1` ([openrv_hook.py:40](sync_test/python/sync_test/openrv_hook.py#L40)), matching `validate_checkpoint`'s `expected_frame + 1`. Source-view checkpoints are therefore comparable across hosts.
- [ ] 1.2 Record the answer in `docs/` alongside the existing frame-base notes, since this is the third time frame-base semantics have cost an investigation

## 2. xStudio receiver — NOT NEEDED, already works

Verified against the re-recorded `xstudio_selects_v2.jsonl` (2026-08-02): two of its
three checkpoints are source-mode against clip-timeline guids, one asserting frame 28
inside an isolated clip, and the test **passes**. No receiver change is required.

`unify-view-state-sync` closed this incidentally: `view_mode="source"` and `clip_guid`
now arrive on the same message, so the receiver's view block runs `apply_selection`
first, which sets `active_timeline_guid = get_or_create_clip_timeline(clip_guid)` — the
same derived guid the sender used. The mismatch guard is never reached.

- [x] 2.1 ~~Manager helper resolving a `timeline_guid` by derivation~~ — unnecessary; `apply_selection` already establishes the match
- [x] 2.2 ~~Attempt resolution before declaring a mismatch~~ — the mismatch never occurs
- [x] 2.3 ~~Keep the ignore path~~ — unchanged, still in place
- [x] 2.4 ~~Verify applying does not echo back~~ — unchanged behaviour, exercised by the passing two-app run

## 3. RV

- [x] 3.1 Diagnose why RV also failed to follow the stop, given `_apply_playback` has no timeline guard. Check first whether it received the message at all, then whether `_frame_base` translated a clip-local frame against a sequence-relative base

  **Answer: RV is not defective. The recording is stale.**
  `sync_test/recordings/xstudio_selects.jsonl` was recorded **2026-06-02**, before `d18ec21` (2026-07-01) retired `SELECTION_1.0` and folded view state into `PLAYBACK_SETTINGS_1.0`. Consequences on replay:
  - Its `PLAYBACK_SETTINGS_1.0` messages carry only `playing`, `current_time`, `playback_mode`, `timeline_guid`, `sync_timestamp` — **no `view_mode`, no `clip_guid`**.
  - Its separate `SELECTION_1.0` messages are dropped on the floor: `SelectionSet` no longer exists ([protocol_messages.py:721](python/otio_sync_core/protocol_messages.py#L721)).
  - So RV hits `view_mode is None` and skips the entire view-switch block. It never learns it should be in source view, applies the frame against the sequence it is still showing, and `_last_applied_*` is never updated. Nothing in RV's code is wrong; it was never told.
  - A **current** sender always includes both fields on the hot path ([playback_sync.py:636-637](xstudio_plugin/ori_sync/playback_sync.py#L636-L637)), so RV's `_switch_to_source_view` would fire.

- [x] 3.2 Fix per that diagnosis — do not assume it is the same defect as xStudio's

  No RV-side defect. Confirmed by the re-recorded run: RV follows source-view playback and the suite passes.

## 4. Verify

- [x] 4.1 `xstudio_selects` passes — with `xstudio_selects_v2.jsonl`. **Still to confirm it passes repeatedly**, not just once
- [ ] 4.2 Isolate a clip, scrub, and confirm the peer lands on the same *image*, not merely the same frame number. **Still genuinely open**: both source-mode checkpoints have `timeline_name=None` (a clip-timeline guid is never declared in the snapshot), so only the frame number is asserted — the pass proves frame following, not image following
- [x] 4.3 Two-peer run: no echo observed in the passing run
- [ ] 4.4 Confirm playback for a genuinely unknown timeline is still ignored — untouched code path, but unexercised by this recording
- [x] 4.5 Sequence-view playback unchanged — the recording is a near-even 236 sequence / 230 source split and passes both

## 5. Test-side follow-ups found alongside this

- [ ] 5.1 `sync_test` has three intermittent tests — `add_media`, `delete_media_xstudio`, `reorder_media` all failed in a suite run and passed in isolation or on re-run. Flakiness at that rate makes every suite result ambiguous and is worth its own investigation
- [x] 5.2 Decide whether `derive_checkpoints` should skip checkpoints whose `timeline_guid` was never declared in the recording. **Resolved: do not skip.** They are satisfiable — two such checkpoints pass in the current recording. The revert was correct; skipping would have hidden the fact that the failure was a stale recording. Note they remain *weak* (frame only, no clip assertion), which is what task 4.2 covers

## 6. Recording hygiene learned here

A snapshot asserts whatever state the session happened to be in when recording began. The first `xstudio_selects_v2` attempt failed at the t=0.2 s structural checkpoint for two reasons unrelated to playback, both worth avoiding when recording:

- [x] 6.1 Park the playhead at frame 0 before recording — the first attempt captured `current_time.value = 87.0`, a position nothing in the recording ever drives the apps to
- [x] 6.2 Record against a real sequence, not a bare flat playlist with clip timelines already materialised — RV always creates its own `'Default Sequence'`, which then shows up as an undeclared extra timeline
- [ ] 6.3 Consider whether the projection should ignore an app-created default sequence regardless, so recording technique is not load-bearing for a passing suite
