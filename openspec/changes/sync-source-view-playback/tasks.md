## 1. Establish the frame convention

- [x] 1.1 Confirm on both senders whether a source-view `current_time.value` is clip-local or sequence-relative (design.md's open question). Everything else depends on this; write the receiver's translation only once it is answered

  **Answer: clip-local on both senders, and no receiver translation is needed.**
  - xStudio sends `ph.position` where `ph` is the *isolated clip's* playhead (source view switches the on-screen source to the single clip), so the value is 0-based within the clip.
  - RV sends `current_frame - self._frame_base()`, and `_frame_base()` is `rv.commands.frameStart()` of the **current view** — in source view that is the source's own first frame (89899 for timecode media, 1 otherwise). So RV's value is also 0-based within the clip.
  - RV's receive path already re-reads `frameStart()` *after* the view switch ([playback_sync.py:175-180](rvplugin/ori_sync/playback_sync.py#L175-L180)), so the same base conversion runs in reverse. The translation is already correct **provided the view switch lands first** — which is the actual dependency, not the frame convention.
  - The harness normalises RV to the same space: `frame() - frameStart() + 1` ([openrv_hook.py:40](sync_test/python/sync_test/openrv_hook.py#L40)), matching `validate_checkpoint`'s `expected_frame + 1`. Source-view checkpoints are therefore comparable across hosts.
- [ ] 1.2 Record the answer in `docs/` alongside the existing frame-base notes, since this is the third time frame-base semantics have cost an investigation

## 2. xStudio receiver

- [ ] 2.1 Add a manager helper that resolves a `timeline_guid` to a clip guid by derivation (`uuid5(NAMESPACE_OID, f"clip_timeline:{clip_guid}")`), checking the clips the peer knows rather than only `_clip_timelines`
- [ ] 2.2 In `playback_sync.apply_playback_state`, attempt that resolution before declaring a mismatch; on success apply the playback as source-view playback for the resolved clip
- [ ] 2.3 Keep the ignore path for a guid that resolves to nothing, and keep the existing "apply a play command even on mismatch" behaviour
- [ ] 2.4 Make sure applying does not echo back — the capability already requires this for incoming clip changes

## 3. RV

- [x] 3.1 Diagnose why RV also failed to follow the stop, given `_apply_playback` has no timeline guard. Check first whether it received the message at all, then whether `_frame_base` translated a clip-local frame against a sequence-relative base

  **Answer: RV is not defective. The recording is stale.**
  `sync_test/recordings/xstudio_selects.jsonl` was recorded **2026-06-02**, before `d18ec21` (2026-07-01) retired `SELECTION_1.0` and folded view state into `PLAYBACK_SETTINGS_1.0`. Consequences on replay:
  - Its `PLAYBACK_SETTINGS_1.0` messages carry only `playing`, `current_time`, `playback_mode`, `timeline_guid`, `sync_timestamp` — **no `view_mode`, no `clip_guid`**.
  - Its separate `SELECTION_1.0` messages are dropped on the floor: `SelectionSet` no longer exists ([protocol_messages.py:721](python/otio_sync_core/protocol_messages.py#L721)).
  - So RV hits `view_mode is None` and skips the entire view-switch block. It never learns it should be in source view, applies the frame against the sequence it is still showing, and `_last_applied_*` is never updated. Nothing in RV's code is wrong; it was never told.
  - A **current** sender always includes both fields on the hot path ([playback_sync.py:636-637](xstudio_plugin/ori_sync/playback_sync.py#L636-L637)), so RV's `_switch_to_source_view` would fire.

- [ ] 3.2 Fix per that diagnosis — do not assume it is the same defect as xStudio's

  Per 3.1 there is no RV-side defect to fix. Close this once 1.1/3.1 are accepted, or re-open if a re-recorded run shows a genuine RV failure.

## 4. Verify

- [ ] 4.1 `xstudio_selects` passes, and passes repeatedly (it has been failing on the t=36.2 s checkpoint, which asserts exactly this behaviour)
- [ ] 4.2 Isolate a clip, scrub, and confirm the peer lands on the same *image*, not merely the same frame number
- [ ] 4.3 Two-peer run: confirm an applied clip-timeline playback does not echo back to the sender
- [ ] 4.4 Confirm playback for a genuinely unknown timeline is still ignored
- [ ] 4.5 Confirm sequence-view playback is unchanged, including the deliberate non-actioning of a sequence-mode `clip_guid`

## 5. Test-side follow-ups found alongside this

- [ ] 5.1 `sync_test` has three intermittent tests — `add_media`, `delete_media_xstudio`, `reorder_media` all failed in a suite run and passed in isolation or on re-run. Flakiness at that rate makes every suite result ambiguous and is worth its own investigation
- [ ] 5.2 Decide whether `derive_checkpoints` should skip checkpoints whose `timeline_guid` was never declared in the recording. It was implemented and then reverted deliberately: while source-view playback is unfollowable those checkpoints are unsatisfiable, but skipping them would have hidden this very bug. Once this change lands they become satisfiable and the question may be moot
