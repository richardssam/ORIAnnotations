## 1. Wire protocol

- [x] 1.1 In `python/otio_sync_core/protocol_messages.py::PlaybackSettingsSet`, replace `looping: bool | None` with `playback_mode: str | None`, updating `_KNOWN`, `to_payload`, and `from_payload` accordingly. No dual-read of the old `looping` key (design D2).
- [x] 1.2 Update `python/otio_sync_core/manager.py`'s docstrings referencing `looping` to reference `playback_mode`.

## 2. OpenRV side

- [x] 2.1 In `rvplugin/ori_sync/playback_sync.py::_broadcast_playback`, replace `looping = rv.commands.playMode() == 0` with a direct 3-way map from `rv.commands.playMode()` (0/1/2) to `"loop"`/`"play-once"`/`"ping-pong"`, sent as `playback_mode`.
- [x] 2.2 In `rvplugin/ori_sync/playback_sync.py`'s receive path (`apply_playback_state` or equivalent), replace `target_play_mode = 0 if looping else 1` with a direct 3-way map from the received `playback_mode` string back to `rv.commands.setPlayMode()`'s 0/1/2.
- [x] 2.3 Rename `self._cur_looping` (bool) to `self._cur_playback_mode` (string) throughout `playback_sync.py`, updating its use in `plugin.py::on_rv_play_stop` (see task 3.1) accordingly.

## 3. Remove and re-verify the restart fallback

- [x] 3.1 In `rvplugin/ori_sync/plugin.py::on_rv_play_stop`, delete the restart-on-boundary block (the `if not self._rv_updating and self.playback._cur_looping: ... rv.commands.play() ...` fallback added in `d18ec21`), leaving only the unconditional `self.playback._broadcast_playback()` / `event.reject()`.
- [x] 3.2 Reinstall the rvpkg (`rvplugin/ori_sync/reinstall.csh` or equivalent) before live-testing, per this project's standing convention that RV loads the installed copy, not repo source.

## 4. xStudio side

- [x] 4.1 In `xstudio_plugin/ori_sync/playback_sync.py::_get_loop_mode`, replace the `str(mode).strip() == "Loop"` bool check with a direct 3-way map from the native `"Loop Mode"` attribute (`"Play Once"`/`"Loop"`/`"Ping Pong"`) to `"play-once"`/`"loop"`/`"ping-pong"`, and rename the method (e.g. `_get_playback_mode`) so its return type is no longer misleadingly bool-shaped.
- [x] 4.2 Update all three call sites of `_get_loop_mode()` (lines ~567, ~837, ~869) to use the renamed method and the `playback_mode` key instead of `looping`.
- [x] 4.3 Confirm whether xStudio's receive path needs to actively set its native `"Loop Mode"` attribute on receipt of a peer's `playback_mode` (mirroring what OpenRV's `setPlayMode` call does), or whether xStudio already applies it implicitly elsewhere — check `playback_sync.py`'s full receive path before assuming symmetry with OpenRV's explicit set.

## 5. Test fixtures and recordings

- [x] 5.1 Update `tests/otio_sync/test_protocol_messages.py`'s `{"playing": True, "looping": False, ...}` fixture to use `"playback_mode": "play-once"` (or `"loop"`, matching whatever the test's intent was).
- [x] 5.2 Write a one-off migration script that walks `sync_test/recordings/*.jsonl` and replaces every literal `"looping": true` / `"looping": false` key with `"playback_mode": "loop"` / `"playback_mode": "play-once"` respectively, in both standalone `PLAYBACK_SETTINGS_1.0` `SET` payloads and nested `STATE_SNAPSHOT.playback_state` objects (design D3). The script SHALL assert it only ever transforms recognized `"looping"` literal occurrences and fail loudly (not silently skip) on any other shape.
- [x] 5.3 Run the migration script against all 18 affected recordings; grep to confirm zero remaining `"looping"` occurrences across `sync_test/recordings/`. (One exception found and left as-is: `add_media_notc.jsonl` lines 54-55 are pre-existing corrupt/unparseable JSON — not introduced by this migration, already silently skipped by `sync_test`'s existing readers — see session notes.)

## 6. Live verification

- [x] 6.1 Run a script-driven or recorded `sync_test` test that exercises `looping`/`playback_mode` end-to-end (e.g. `color_tests` or another recording carrying `PLAYBACK_SETTINGS_1.0`); confirm no failures introduced by the field rename.
- [x] 6.2 Live-test loop mode specifically with the restart fallback removed (task 3.1): drive one app to set loop mode, let playback run past the clip boundary repeatedly, and confirm it keeps looping correctly on both peers without the manual restart. This is the empirical re-verification design D4 calls for — if it fails, diagnose the actual mechanism rather than reintroducing the old hack. (Confirmed: native loop continues correctly on both peers once `playback_mode` agrees, after fixing the xStudio-side "ignore rapid play-after-stop" guard that was swallowing every native restart. A separate, pre-existing xStudio issue — its own playhead's native mode not staying consistent across clip switches — is tracked as a follow-up, not a defect in the wire protocol or the restart-hack removal.)
- [x] 6.3 Live-test ping-pong mode: drive one app to set ping-pong, let playback run to both ends of the range, and confirm both peers' native engines reverse direction correctly and stay in sync (frame position matches on both sides across at least one full forward-reverse cycle). (Confirmed: same fix as 6.2 — RV's native ping-pong reversal fires the identical stop/restart pattern as loop, and was equally fixed by removing the stale xStudio-side guard.)
- [x] 6.4 Live-test play-once mode: confirm playback stops at the boundary on both peers (no regression from the default case). (Confirmed working — this is the default/simplest case and showed no issues during testing.)
- [x] 6.5 Run the full existing `tests/otio_sync/` suite and `sync_test` suite; confirm no new failures beyond already-known pre-existing ones. (`tests/otio_sync`: same 5 pre-existing failures with and without this change. `sync_test` full suite: ran twice with this change and once on baseline — `xstudio_selects` failed reproducibly in all three runs (pre-existing flake, unrelated to `playback_mode`); every other failure seen in any single run did not reproduce in the others, confirming general suite timing-flakiness rather than a regression from this change.)
