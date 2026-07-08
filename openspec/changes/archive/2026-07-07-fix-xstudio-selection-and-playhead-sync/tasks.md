## 1. RV-side: apply incoming sequence-mode clip highlights (D1)

- [x] 1.1 **(Do this first — blocking spike.)** Confirm RV's Python-exposed command for *writing* selection (mirroring `rv.commands.selection()`'s getter, already used in `on_selection_changed`) — check `rv.commands`/`rv.extra_commands`. The entire RV side (D1) rests on such a setter existing that does NOT go through `setViewNode`; if it doesn't exist, stop and rework D1 before touching 1.2. The design doc flags this as its top open question.
  - **SPIKE RESULT (2026-07-06): No such setter exists — AND the assumed getter doesn't either.** The authoritative RV `commands` module is `OpenRV/src/lib/ip/IPMu/CommandsModule.cpp` (164 commands; it's where the working `viewNode`/`setViewNode` the plugin uses are registered). It contains **no** `selection`, `setSelection`, `select`, `deselect`, or `selected` command. Same result across every other command-registration site (`RvApp/CommandsModule.cpp`, `RvApp/PyCommandsModule.cpp`, `MuTwkApp/CommandsModule.cpp`, `PyTwkApp/PyCommands.cpp`, `RvCommon/{Py,Mu}UICommands.cpp`) and in the `LiveReviewOpenRV` tree. The C++ `TwkApp::Document::selection()/setSelection()` exist but are **not bound to Mu/Python**. Implication: the existing `on_selection_changed`'s `rv.commands.selection()` call is not backed by a real command in these builds (would `AttributeError` if a `"selection-changed"` event fired — and no source emits that Mu event either), so it's effectively dead. **D1 as written is not implementable. Tasks 1.2–1.4 are blocked pending a D1 rework — do not proceed on a guessed command name.**
- [x] 1.2 **(Reworked several times, then reverted — see D3, final.)** Live testing established that in sequence view xStudio's `clip_guid` follows the **playhead** (media-change), not a selection, and has no distinct selection signal — so any receiver action on it fires on scrubbing / connect. Final: `rvplugin/ori_sync/playback_sync.py::_apply_playback` does **not** act on a sequence-mode `clip_guid` change; RV stays on the sequence and only `mode_changed`/`tl_changed` drives `_switch_to_sequence_view`. This is the pre-proposal RV behaviour. Explicit isolation still flows through source mode (`elif view_mode == "source"`), unchanged. Covered by `test_playback_view_dispatch` (5 tests, incl. `test_sequence_clip_change_stays_on_sequence`).
- [x] 1.3 N/A — no RV sequence-mode isolation remains, so no echo guard is needed there. (Source-mode `_switch_to_source_view` keeps its own `_rv_updating` bracketing, unchanged.)
- [x] 1.4 N/A — superseded by the revert (RV no longer branches on a sequence-mode clip change).

## 2. xStudio-side: timeline-stability guard for the existing highlight mechanism (D2)

- [x] 2.1 **Resolved via reuse (resolves design Open Question on per-timeline vs global).** No new timestamp needed: every structural-rebuild site in `structure_sync.py` (`execute_sequence_rebuild`, `apply_sequence_insert`, the remove/reorder `load_otio(clear=True)` paths, etc.) already advances the plugin-global `_structural_mutation_suppress_until` to `now + 1.5s`. That is a ready-made "structure just changed" signal maintained at exactly the right points, so I reuse it rather than thread a per-timeline dict through ~5 call sites. It's global (any rebuild suppresses any highlight) — deliberately conservative: a dropped highlight is harmless, the segfault is not.
- [x] 2.2 Added `_timeline_recently_rebuilt()` in `xstudio_plugin/ori_sync/playback_sync.py` — returns `time.monotonic() < self.plugin._structural_mutation_suppress_until`. Uses the existing 1.5s window (not the 300ms candidate): 1.5s is the established rebuild-settle window here and is the safer choice against a C++ segfault.
- [x] 2.3 Both `item_selection_atom` sends are now gated on `not self._timeline_recently_rebuilt()`: the specific-clip highlight (extracted into `_highlight_timeline_item`, which checks the guard up front and logs-and-returns) and the empty-vector *clear*-send in the selection-clear path (inline `if _ENABLE_TIMELINE_ITEM_HIGHLIGHT and not self._timeline_recently_rebuilt():`). Both skip silently (log only).
- [x] 2.4 **Reconciled with D2's kill-switch note.** Flipped `_ENABLE_TIMELINE_ITEM_HIGHLIGHT` to `True` but *kept it as an instant kill-switch* while making the stability guard the real gate — every send is now `_ENABLE_TIMELINE_ITEM_HIGHLIGHT and not _timeline_recently_rebuilt()`, so there is no path that sends without the recency gate, and flipping the flag back to `False` still disables everything. (The earlier "don't just flip the flag" warning is satisfied because the clear-send got the guard too.)
- [x] 2.5 Kept the `try/except` around the send inside `_highlight_timeline_item` and around the clear-send — defence-in-depth, since a rebuild could still begin mid-send (D2).

## 3. xStudio-side: sequence-mode highlight trigger (D3 — reverted)

- [x] 3.1 **Reverted (see D3, final).** The sequence-mode `elif clip_changed → _highlight_timeline_item` branch in `apply_playback_state` was removed: same root cause as RV — a sequence-mode `clip_guid` follows the peer's playhead, so it would fire the highlight on every scrub, with no way to tell a selection from a scan. xStudio now only switches the sequence view on `mode_changed`/`tl_changed`. `_highlight_timeline_item` + the stability guard remain, still called from the **source-mode** `apply_selection` clip-isolation path (that is what section 2 now protects).
- [x] 3.2 `_highlight_timeline_item` still sets `_selection_broadcast_suppress_until` before its send (echo guard), now exercised only by the source-mode `apply_selection` path.

## 4. Testing and live verification

- [x] 4.1 Added `tests/otio_sync/test_playback_view_dispatch.py` (5 tests, all pass) — script-driven regression coverage for RV's `_apply_playback` view dispatch: a sequence-mode clip-only change switches to source view; a deselect (`clip_guid` None) does not switch; a repeated identical clip does not reprocess; a sequence (timeline) change still uses sequence view; source-mode selection keeps existing behaviour. (The xStudio-receive highlight can't be unit-tested here — the `xstudio` module isn't importable outside the app — so its behaviour is covered by the live tests 4.3–4.4.)
- [x] 4.2 **Live-tested (owner, extended session).** The sequence-mode highlight this task originally covered was reverted (D3), so the live testing instead validated the delivered isolated-clip (source-mode) workflow end to end — see sections 5–9. Confirmed via `xstudio_client.log` / `rv_host.log` traces: double-click / "select a clip" reliably reflects in RV, isolation seeks to the clip's first frame, and isolated clips loop on both peers.
- [x] 4.3 **Superseded by the D3 revert.** No sequence-mode highlight is applied on either receive path any more, so there is nothing to live-test here. The `item_selection_atom` highlight that remains is exercised only by the source-mode `apply_selection` path.
- [x] 4.4 **No crash observed in extended live testing.** The `item_selection_atom` sends are gated by `_timeline_recently_rebuilt()`; across the long live session (many isolations, reorders, deletes) xStudio did not segfault. A dedicated "highlight immediately after a structural rebuild" repro was not isolated, so the guard is validated by soak, not by a targeted crash repro — the kill-switch (`_ENABLE_TIMELINE_ITEM_HIGHLIGHT = False`) remains available.
- [x] 4.5 **Not triggered.** No recurring crash appeared in testing, so the RV-only fallback was not needed; the kill-switch stays in place as documented insurance.
- [x] 4.6 Ran the full `tests/otio_sync/` suite under `rez-env opentimelineio` (96 tests). Result: 2 failures + 1 error, **all pre-existing / environmental, none caused by this change** — (a) `ModuleNotFoundError: xstudio` (the xstudio module isn't installed outside the app); (b) `test_rebuild_rv_session_view_switching` exercises `sequence_sync._rebuild_rv_session`, a file this change does not touch; (c) `test_rv_paint_applier` border-width (`1.0 != 2.0`) reproduces with this change's edits reverted (known pen-width area). The new `test_playback_view_dispatch` passed within the suite. NOTE: the `sync_test` end-to-end suite drives live RV/xStudio and belongs with the 4.2–4.4 live runs (owner).

## 5. xStudio-side: reliable isolated-clip selection broadcast (D4)

- [x] 5.1 In `resolve_and_broadcast_selection`, broadcast a `mode=source` selection directly when in single-clip mode (`_last_pinned_source_mode is False`) and the playlist's `playhead_selection.selected_sources` resolves a clip that differs from `_cur_clip_guid` — driven by the reliably-fired selection-actor event, not the `show_atom` media-change. Deduped via `_cur_clip_guid`; gated on `PSM False`. Fixed the ~5/21 missed double-clicks and the 1.3–1.5 s lag seen in the logs.

## 6. xStudio-side: fresh Pinned Source Mode read (D5)

- [x] 6.1 Stamp `_last_source_atom_at` in `on_selection_event`.
- [x] 6.2 Add `_read_pinned_source_mode_fresh()` (bounded playhead read via `current_playhead()`), and in the `show_atom` single-clip decision use a fresh PSM read when a selection fired within ~3 s. Fixes the "first selection labelled sequence, second works" symptom.

## 7. xStudio-side: frame-reset to the clip's first frame on isolation (D6)

- [x] 7.1 In `broadcast_view_state`, on a *new* source-mode isolation (`view_mode == "source"` and `clip_guid != self._cur_clip_guid`), set `state["current_time"]["value"] = 0` and seek the local playhead to `0`, so both peers land on the clip's first frame. Only fires on a new isolation, not on in-clip scrubbing.

## 8. Loop mode for isolated clips (D7)

- [x] 8.1 On a new isolation set `_last_known_playback_mode = "loop"` (so `_carry_over_playback_mode` keeps Loop at the loop boundary instead of reverting to Play Once) and set the playhead `Loop Mode = "Loop"`; broadcast `playback_mode = "loop"`.
- [x] 8.2 **Send side:** `_get_playback_mode()` returns `"loop"` while in single-clip mode, so a freshly-acquired clip playhead's transient Play Once default never leaks into a broadcast.
- [x] 8.3 **Receive side:** in `apply_playback_state`, while in single-clip mode ignore an incoming `playback_mode == "play-once"` (do not set Loop Mode, do not update `_last_known_playback_mode`) — breaks the loop↔play-once oscillation caused by RV's isolated view echoing Play Once back.
- [ ] 8.4 **Known residual (accepted, not fixed):** the first playthrough of a freshly double-clicked clip still plays once (race with xStudio's native auto-play on the fresh Play Once playhead); every pass after the first loops. Winning the race would need an intrusive stop→set-Loop→play restart — deferred by owner as "odd but not a disaster".

## 9. Freeze fix: bound playhead reads/writes on the selection path (D8)

- [x] 9.1 Wrap the frame-reset (`position = 0`) and loop-force (`set_attribute("Loop Mode", …)`) writes in `broadcast_view_state` in `bounded_timeout`.
- [x] 9.2 Bound the `active_playhead` reads in `current_playback_state()` likewise. Fixes the ~100 s freeze ("ignoring everything") after the first clip ended and its playhead went stale.

## 10. Follow-ups (deferred, owner)

- [ ] 10.1 **RV-side cosmetic cleanup:** RV's isolated source view resets *its own* play mode to Play Once and broadcasts it (harmless — xStudio ignores it in single-clip mode — but it's log noise). Optionally make RV broadcast the agreed `_cur_playback_mode` for source mode instead of the live view read.
- [ ] 10.2 Retire `docs/clip_selection_sync_status.md` now that the sequence-mode navigation/highlight goal is formally not pursued.
