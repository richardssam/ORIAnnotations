## Why

Playback-sync convergence for clip isolation — a peer picking a clip and frame, another peer joining or catching up to match it — has broken several times in ways only found by manual live testing, worked around with ad hoc fixes, and never locked down by an automated test. This change adds that test, as a `sync_test` scripted scenario.

It does not also fix the one thing the test would currently catch — xStudio sometimes misreporting its own `view_mode`/`playback_mode`. Live testing has seen this go both ways for the identical scenario: sometimes correct, sometimes not. That rules out a simple, always-wrong classification bug, and nothing yet explains what actually determines it. This change's job is to turn that from "something occasionally noticed by watching the UI" into something repeatable enough to diagnose — the fix comes after, once there's an actual mechanism to fix.

The test case:
1. Open xStudio, set up a session with test media, e.g.:
   - `test_media/source/encoded/car_ACES_sRGB.mov`
   - `test_media/source/encoded/graphic_ACES_sRGB.mov`
   - `test_media/source/encoded/laser_ACES_sRGB.mov`
2. Pick the `graphic_ACES_sRGB.mov` clip and move to frame 89950.
3. Open a second session as a client — `ORI_SYNC_USER=sam2`, `role=viewer` — once against another xStudio instance, and separately against OpenRV.
4. Once settled, the client SHALL be showing `graphic_ACES_sRGB.mov` at frame 89950.
5. On the client, pick clip `laser_ACES_sRGB` at frame 91785 — this SHALL NOT affect the host's session.
6. On the host, change frame to 89900 (still on `graphic_ACES_sRGB`) — the client SHALL switch back to `graphic_ACES_sRGB` at frame 89900.

**Steps 1-6 are regression coverage for bugs already fixed** (2026-08-18, this session): a joiner's first view-switch attempt landing before playlists exist used to get recorded as successful and permanently block the real retry (step 4); a viewer's own local pick used to never invalidate the dedup guard the host's later messages are checked against, so step 6's reassertion never took effect; and `_apply_sequence_view`'s flat-playlist fallback used to re-point the container without resetting its selection, so the "switch back" in step 6 was a visual no-op even when the code path was reached. All three are fixed in `xstudio_plugin/ori_sync/playback_sync.py` — this test is what stops them from regressing silently.

**One bug this test would still catch, not yet fixed**: live testing during the above also caught xStudio broadcasting `"view_mode": "sequence"` and `"playback_mode": "play-once"` for a session that has no genuine sequence at all — a flat, unordered playlist (`xs_flat_playlist: true`, every clip `source_range: null`, no xStudio Timeline child). Both fields are wrong: there is nothing to be "in sequence" *of*, and the project's own established rule is that an isolated single clip forces Loop, not play-once. But the same live testing has also seen this same scenario report *correctly*, which rules out a plain always-wrong classification bug — this change diagnoses it, it does not guess a fix (see design.md).

A third, single-app test (no peer, no join) sharpens that diagnosis: `sync_test`'s inspector already reads xStudio's *actual* `Pinned Source Mode` attribute independently of anything the sync plugin broadcasts, so comparing that ground truth against the plugin's own wire message directly answers whether the bug is in xStudio's state or in the plugin's tracking of it — and it's fast enough to run many times in a loop.

## What Changes

- Add two script-driven `sync_test` scenarios implementing the 6 steps above, extending `sync_test/sync_tests.yaml`: xStudio↔xStudio and xStudio↔OpenRV.
- Add a third, single-app `sync_test` scenario (xStudio only, no peer) that picks the same clip/frame and asserts xStudio's own ground-truth state (via the inspector) against the plugin's actual broadcast — the fast, repeatable vehicle for diagnosing the `view_mode`/`playback_mode` anomaly.
- Extend the inspector (`xstudio_hook.py`) to expose Loop Mode — it currently reports `view_mode` (from `Pinned Source Mode`) but nothing about loop status, so `playback_mode` isn't assertable by any of the three tests yet.
- Diagnose the `view_mode`/`playback_mode` anomaly using the new tests; mark the relevant assertions `status: known_broken` until it's understood and fixed (a follow-up, scope depending on what's found — see tasks.md).

## Capabilities

### Modified Capabilities

- `xstudio-clip-selection-sync`: adds a requirement documenting the full pick → join/catch-up → local-pick-isolation → host-reassertion convergence flow (steps 1-6 above) as the capability's own acceptance test — this locks in behavior already fixed in code, previously unverified by any automated test. Does **not** yet add a requirement for the `view_mode`/`playback_mode` classification — that's deferred until diagnosed (see design.md), so no spec claims a fix that hasn't been earned.

## Impact

- `sync_test/sync_tests.yaml` — three new scripted test entries (xStudio↔xStudio, xStudio↔OpenRV, xStudio solo).
- `sync_test/python/sync_test/xstudio_hook.py` — add Loop Mode to the inspector's reported state.
- `xstudio_plugin/ori_sync/playback_sync.py` — likely affected once the anomaly is diagnosed (task 4, follow-up); not touched by this change's own tasks.
- `openspec/specs/xstudio-clip-selection-sync/spec.md` — one requirement added (the convergence flow). A second, for the classification fix, is deferred to the follow-up.
