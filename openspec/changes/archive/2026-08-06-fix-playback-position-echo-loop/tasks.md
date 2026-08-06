## 1. Plugin: echo guard covers the whole driven period

- [x] 1.1 Add `_PLAYBACK_ECHO_GUARD_S` (0.4 s) and use it at every arming site instead of repeated literals.
- [x] 1.2 Arm `_playback_apply_suppress_until` at the top of `apply_playback_state`, ahead of every early return, so a dropped message still marks this peer as being driven. Re-arm at the position-apply site, since the view switch / Loop Mode set / bounded reads in between can consume most of the window.
- [x] 1.3 Re-check the guard in `flush_pending_scrub_broadcast` and discard a position captured before a peer began driving; log the discarded frame.
- [ ] 1.4 **Superseded — do not implement.** `host-owned-visibility` §5.1 deletes the visibility-side echo guards entirely: a follower will not broadcast visibility at all, so there is no position for this path to withhold. Implementing it would be completing work already scheduled for removal. (Position-side guards in §1.2/§1.3 survive — position stays multi-writer.) Original scope: make `broadcast_view_state` withhold the position while the guard is armed, still delivering the view/mode change. This is the last known unguarded broadcast path — evidenced by `RECV frame=61.0` at `16:56:08.003` followed by `SEND frame=0.0` at `16:56:08.185`, with a sibling path logging `→ suppressed (echo guard)` in the same millisecond.

## 2. Plugin: do not infer a local play action from a peer-driven transition

- [x] 2.1 In the `Pinned Source Mode` `True→False` branch, pass `playing_override=True` only when the echo guard is not armed; otherwise pass `None` so the broadcast does not assert play. Log which case applied.

## 3. Plugin diagnostics

- [x] 3.1 Distinguish, in the log, `broadcast_view_state`'s two frame-0 sources: a fabricated 0 from an unreadable `current_playback_state()`, and the deliberate 0 of a new source-clip isolation. Both look identical on the wire; only one is correct. (These log lines falsified two hypotheses and located the real cause — keep them.)
- [x] 3.2 Decide what an unreadable `current_playback_state()` should broadcast. It currently fabricates `frame=0` and peers seek there, so "I cannot read my position" becomes "my position is 0". Withholding the position is the likely answer; confirm against a run where the fabricated-0 log line actually fires (it did not in any run so far).

  Implemented: withhold to `_last_received_frame` (the driver's position) if a peer is/was driving, else `_last_polled_frame` (our own last observed frame), and only fabricate `frame=0` — now logged distinctly — when neither is known. Still unconfirmed against a live-fire of that path; the log line has not appeared in any run to date, so this rests on the same reasoning as the withhold-while-driven fix below it, not on trace evidence.

## 4. Harness: assertions that cannot lie

- [x] 4.1 `_format_observed`: report every app's frame, timeline and playback state; used on both the pass and fail paths.
- [x] 4.2 `validate_checkpoint`: skip the frame comparison for a playing app; name the timeline a wrong frame was read on; always show all apps.
- [x] 4.3 `_verify_frame_sync`: poll for a *parked* playhead matching the expectation; on a playhead that never parks, fail with "playback still active … no frame assertion is possible" rather than a frame diff.
- [x] 4.4 Frame-checkpoint loop: keep waiting rather than banking a pass that compared nothing when an app is playing; fail explicitly at the deadline.
- [x] 4.5 Verify every `set_frame` in the script-driven command loop, not just the last.
- [x] 4.6 `_poll_until`: take a `label` and report both the wait and the outcome, so a silent retry is no longer indistinguishable from an immediate pass.
- [x] 4.7 Relabel the observed/mismatch output `timeline=` rather than `clip=`. Both hooks populate `state["clip"]` with the active *timeline* name and `validate_checkpoint` compares it against `timeline_name`; the old label made a `set_selection <clip>` look like it had silently failed.

## 5. Harness: xStudio state reporting

- [x] 5.1 Report real `playing` via `ph.playing` (was hard-coded `False`, so a frame assertion could not tell "parked on the wrong frame" from "playing, frame meaningless").
- [x] 5.2 Move the playhead read out of the container-read `try` into a sibling block. Nested, one `Could not read container: invalid_argument` produced `frame=None, playing=False`, and because `validate_checkpoint` skips a `None` frame, a run of four green seeks meant xStudio was never compared at all.
- [x] 5.3 Seed `media_path`/`media_exists` in the initial state dict so a failed container read cannot drop them entirely.

## 6. Logging resolution

- [x] 6.1 Millisecond timestamps in `cli.py`, the runner's `runner.log` handler, and `run_xstudio_inspector.py`. At whole-second resolution a seek, the broadcast it triggers and the state read that checks it collapse onto one instant, hiding the ordering that is the whole question.

## 7. Verification

- [x] 7.1 Verify each plugin fix by the disappearance of its own signature in the MQ trace, not by overall pass/fail — the failure modes are independent and each masks the next. Done for §1.2 (stale burst after `RECV` gone; next send 1.4 s later) and §2.1 (`playing=True` broadcasts gone).
- [x] 7.2 §1.3's discard has never fired (0 hits across four runs). Either construct the race deliberately or accept it as defensive and say so.

  Constructed deliberately in `tests/xstudio_plugin/test_playback_echo_guard.py::test_stale_pending_scrub_is_discarded_when_guard_arms_before_flush` — capture a pending scrub, arm the guard as if a peer's message had arrived after capture, flush, and assert the state is cleared and nothing reaches `_cmd_queue`. `test_pending_scrub_is_released_when_no_peer_is_driving` is the sibling control proving the discard isn't vacuous (same flush, no guard, message goes out).
- [x] 7.3 After §1.4, confirm a clean MQ trace: no `SEND` of this peer's position within the guard window of any `RECV`.

  Confirmed with a fresh live run, 2026-08-06 21:51 — temporarily restored the retired `xstudio_selects_script_rv` block into `sync_tests.yaml` (reverted immediately after; `git diff` on that file is empty) since it's still the only config driving OpenRV's script-derived `set_frame` sequence against a following xStudio, and raw position sync (unlike selection) stays multi-writer under host-owned-visibility so this part of it is still valid signal. `sync_test/logs/xstudio_selects_script_rv/xstudio_plugin_19001.log` (this run) contains zero instances of the original signature (a `SEND` carrying `frame=0` shortly after a `RECV` of a nonzero frame) — contrast with the Aug-5 pre-fix log's dozens of such pairs (e.g. `RECV value=101` → eight `SEND value=0.0` sends over the next ~2s). The one `RECV value=63` near the end is applied (`RECV playback: set Loop Mode=Play Once`) with no follow-up `SEND` at all before the plugin log's activity ends.

  The test still fails end-to-end (`state_mismatch`: xStudio settles on `frame=0 timeline=None`), but this is the pre-existing, already-tracked reason it was retired (a view/selection divergence under host-owned-visibility, not a position echo) — no new `MQ SEND` appears anywhere after the seek, so the mismatch is local state loss on xStudio's side, not a broadcast. Confirms 7.3 as scoped; the state-loss symptom itself is out of scope for this change.
- [x] 7.4 **Full suite run 2026-08-05, 675s — baseline established: 20 pass / 4 fail of 24.** Failures: `add_media` and `xstudio_selects_script_rv` (both now declared `known_broken` with `blocked_by`), plus `xstudio_selects` and `otio_xstudio_timeline_changes` on `structural_consensus`.

  The run caught a regression introduced by this session's harness work: adding `view_mode` to both hooks' `/state` payloads silently made it a structural-equality criterion in `compare_states`, because that function treats every key not in `ignore_keys` as significant. A sequence/isolated-clip split — deliberately treated as a *warning* in `_verify_frame_sync`, since `/state` cannot distinguish the harmful case from the harmless one — became a hard `state_mismatch`. It failed `delete_media_openrv`, `reorder_media` and `xstudio_selects_script_xstudio`, the last of which had been 10/10. Fixed by adding `view_mode` to `ignore_keys`; all three re-verified green.

  **Generalise before adding more state fields:** a new key in the state payload changes structural equality by default. `host-owned-visibility` adds host identity to `/state` (its §2.4) and must make an explicit `ignore_keys` decision for it rather than inherit one.

  Remaining two failures are *not* attributable to this change's scope and need separate triage — `xstudio_selects` has been flaky at ~47% across 30 runs and is a known quantity, but `otio_xstudio_timeline_changes` changed character (all 10 prior failures were `checkpoint_timeout`; rate 13/23 → 1/6), which is the profile of something introduced today rather than long-standing.

  Original scope: judge `xstudio_selects_script_rv` end to end, and run the full suite against `run_history.jsonl`. Note the comparison is not like-for-like: the harness changes convert former false passes into failures.
- [x] 7.5 Build a stub harness so `xstudio_plugin/ori_sync/playback_sync.py` can be imported and unit-tested, as `tests/otio_sync/test_playback_view_dispatch.py` already does for the RV controller. All four plugin changes currently rest on log evidence from one test.

  Built as `tests/xstudio_plugin/test_playback_echo_guard.py`, following `tests/xstudio_plugin/test_sequence_reconciliation_convergence.py`'s pattern rather than the RV test's: `playback_sync.py` imports real `xstudio.core`/`xstudio.api.session.*` at module level (unlike the RV controller, which only needs `rv.commands` stubbed), so it requires xStudio's bundled interpreter — `xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 -m pytest tests/xstudio_plugin/test_playback_echo_guard.py -v` — not the repo's own `.venv`, no live session needed. 8 tests, covering §1.2 (guard arms ahead of the dropped-message early return, and its sibling playing=True-is-not-dropped case), §1.3/§7.2 (stale-scrub discard, plus two sibling controls), and §3.2 (withhold-to-driver, withhold-to-own-last-frame, and last-resort frame=0). §2.1's `owns_visibility()`-gated auto-play inference is not yet covered — it needs a `manager.owns_visibility()` stub, left for a follow-up pass on this harness.

## 8. Follow-ups (not in this change)

- [ ] 8.1 `set_selection` has no verification anywhere — neither hook exposes selection state, so the four `set_selection` commands in these tests are entirely unchecked. Needs a selection field in both hooks plus a per-command check.
- [ ] 8.2 Rename the state key `clip` → `timeline` across hooks, `compare_states`, `project_state` and the checkpoint schema. Only the display labels were changed here; the key still lies.

  **Its *value* was made consistent on 2026-08-05, which is separate from the rename and does not close this task.** `get_xstudio_state` reported the *timeline* name when a timeline was focused and the *playlist* name when a bin was, so two perfectly-synced peers could report different strings purely from differing UI focus — seen in `otio_xstudio_timeline_changes` as `'Sequence 1'` versus `'Added Media'`, both correct, neither comparable. It now resolves the name through `full_state.timelines[active_timeline_guid].name`, which is keyed by the shared sync GUID and therefore agrees across peers by construction, falling back to the focus-dependent value only when no active timeline exists yet. This brings xStudio into line with OpenRV's hook, which already anchors at sequence level for the same reason.

  The rename itself is still owed: the key is named `clip` while holding a timeline name, which is what made the confusion possible in the first place.
