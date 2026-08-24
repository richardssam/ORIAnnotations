## 1. Extend the inspector to expose Loop Mode

- [x] 1.1 Add a `playback_mode` (or `loop_mode`) field to `xstudio_hook.py`'s state reporting, reading the active playhead's `Loop Mode` attribute — parallel to the existing `Pinned Source Mode` read that produces `state["view_mode"]`. Neither the two-peer nor the single-app test below can assert loop status without this; it isn't exposed today.

## 2. Add YAML-Driven Sync Tests

- [x] 2.1 Add `flat_playlist_convergence_xs_rv` test entry to `sync_test/sync_tests.yaml` — xStudio host + OpenRV viewer, 3 clips, 6-step convergence flow using `add_media`, `set_selection`, `set_frame`, `expect_no_propagation`, and `concurrent_commands`.
- [x] 2.2 Add `flat_playlist_convergence_xs_xs` test entry to `sync_test/sync_tests.yaml` — same commands, `apps: ["xstudio", "xstudio"]` topology.
- [x] 2.3 Add `flat_playlist_view_classification_xs_solo` test entry — single app (`apps: ["xstudio"]`, no peer): add the same 3 clips, pick `graphic_ACES_sRGB` at frame 89950, then assert both (a) the inspector's `fetch_state()` — xStudio's own ground-truth `Pinned Source Mode`/Loop Mode read — equals `view_mode: "source"` / Loop, and (b) the actual `PLAYBACK_SETTINGS_1.0` wire message the plugin broadcasts carries the same values. Log both independently rather than only a pass/fail, so a disagreement between them is visible, not just a failure.
- [x] 2.4 Confirm the convergence assertions in 2.1/2.2 (steps 1, 4, 5, 6 of the flow) pass reliably — this is the already-understood, already-fixed part; it should not be flaky.

## 3. Diagnose the `view_mode`/`playback_mode` anomaly (no fix yet)

- [x] 3.1 Run `flat_playlist_view_classification_xs_solo` (2.3) repeatedly (e.g. 10-20 iterations) — it's the fastest loop vehicle, no peer or join involved. Record, per run: xStudio's ground-truth state, the plugin's broadcast, and whether they agree with each other and with the expected values.
- [x] 3.2 If runs disagree with each other: capture full logs for a clean run and a failing one, and diff with `debug/merge_sync_logs.py --view state` (and `--view merge` for the raw sequence) — look specifically at timing around `Pinned Source Mode` reads and playhead/actor acquisition between the clip pick and the broadcast. If 3.1 shows xStudio's own ground-truth state and the plugin's broadcast disagreeing *with each other* even once, that alone pinpoints the plugin's `_cur_view_mode` tracking as the culprit and 3.2 can skip straight to fixing it.
- [x] 3.3 If it reproduces in the two-peer scenarios (2.1/2.2) but not the single-app one, repeat 3.1/3.2 against those instead — the join/convergence path itself is then implicated, not plain clip isolation.
- [x] 3.4 Document the finding (mechanism, what correlates with failure) — in this design.md if it's small enough to fix here, or as a new OpenSpec change if it turns out to be a separate, larger issue.
- [x] 3.5 Until 3.4 lands a fix, mark the `view_mode`/`playback_mode` assertions in all three new test entries `status: known_broken` (per `sync_test`'s own convention for a tracked-but-unfixed failure) so they still run and report without failing the suite.

## 4. Fix and close out (follow-up, scope depends on 3.4's finding)

- [x] 4.1 Implement the fix for the actual mechanism identified in 3.4.
- [x] 4.2 Add the corresponding spec requirement to `xstudio-clip-selection-sync` (or wherever 3.4 determines it belongs) describing the real behavior — not the guessed one this task list started with.
- [x] 4.3 Remove `status: known_broken` from all three test entries once the fix is verified to hold across repeated runs.
