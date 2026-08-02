## 1. Establish the frame convention

- [ ] 1.1 Confirm on both senders whether a source-view `current_time.value` is clip-local or sequence-relative (design.md's open question). Everything else depends on this; write the receiver's translation only once it is answered
- [ ] 1.2 Record the answer in `docs/` alongside the existing frame-base notes, since this is the third time frame-base semantics have cost an investigation

## 2. xStudio receiver

- [ ] 2.1 Add a manager helper that resolves a `timeline_guid` to a clip guid by derivation (`uuid5(NAMESPACE_OID, f"clip_timeline:{clip_guid}")`), checking the clips the peer knows rather than only `_clip_timelines`
- [ ] 2.2 In `playback_sync.apply_playback_state`, attempt that resolution before declaring a mismatch; on success apply the playback as source-view playback for the resolved clip
- [ ] 2.3 Keep the ignore path for a guid that resolves to nothing, and keep the existing "apply a play command even on mismatch" behaviour
- [ ] 2.4 Make sure applying does not echo back — the capability already requires this for incoming clip changes

## 3. RV

- [ ] 3.1 Diagnose why RV also failed to follow the stop, given `_apply_playback` has no timeline guard. Check first whether it received the message at all, then whether `_frame_base` translated a clip-local frame against a sequence-relative base
- [ ] 3.2 Fix per that diagnosis — do not assume it is the same defect as xStudio's

## 4. Verify

- [ ] 4.1 `xstudio_selects` passes, and passes repeatedly (it has been failing on the t=36.2 s checkpoint, which asserts exactly this behaviour)
- [ ] 4.2 Isolate a clip, scrub, and confirm the peer lands on the same *image*, not merely the same frame number
- [ ] 4.3 Two-peer run: confirm an applied clip-timeline playback does not echo back to the sender
- [ ] 4.4 Confirm playback for a genuinely unknown timeline is still ignored
- [ ] 4.5 Confirm sequence-view playback is unchanged, including the deliberate non-actioning of a sequence-mode `clip_guid`

## 5. Test-side follow-ups found alongside this

- [ ] 5.1 `sync_test` has three intermittent tests — `add_media`, `delete_media_xstudio`, `reorder_media` all failed in a suite run and passed in isolation or on re-run. Flakiness at that rate makes every suite result ambiguous and is worth its own investigation
- [ ] 5.2 Decide whether `derive_checkpoints` should skip checkpoints whose `timeline_guid` was never declared in the recording. It was implemented and then reverted deliberately: while source-view playback is unfollowable those checkpoints are unsatisfiable, but skipping them would have hidden this very bug. Once this change lands they become satisfiable and the question may be moot
