## 1. OpenRV: broadcast the displayed view

- [x] 1.1 In `_broadcast_playback`, derive the broadcast `view_mode` from the `_displayed_view()` call already made for the log line, instead of `_cur_view_mode`.
- [x] 1.2 Keep `clip_guid` sourced from `_cur_clip_guid`, but never pair it with a mode the display contradicts: a displayed source view with no resolved clip broadcasts source-with-no-clip, the existing unresolvable-isolation behaviour.
- [x] 1.3 Add tests to `tests/otio_sync/test_playback_broadcast_guid.py` (it already stubs the RV display): a broadcast dispatched while the display shows a source group must not report `sequence`, and a settled view must broadcast unchanged.

## 2. xStudio: do not report a remote-induced selection as local

- [x] 2.1 Find where the `[SEL]` show_atom path computes the `remote-induced?` provenance and return the attribution as a value, not only a log string.
- [x] 2.2 Gate the outbound broadcast on it: an event attributed to a remote apply within the settling window is applied locally but not broadcast.
- [x] 2.3 Keep the window as tight as the existing one, and confirm a genuine local selection with no recent remote apply still broadcasts — the silent-failure case called out in design.md.
- [x] 2.4 Add coverage in `tests/xstudio_plugin/` (runs under `./run_tests_xstudio.sh`) for both the suppressed and the genuine-local case.

## 3. Verification

- [x] 3.1 Run `./run_tests_core.sh` and `./run_tests_xstudio.sh`.
- [x] 3.2 Reinstall the rvpkg (`rvplugin/ori_sync/reinstall.csh`) before testing in OpenRV.
- [x] 3.3 Reproduce deliberately: with an xStudio host on a sequence, isolate a clip in OpenRV **whose frame differs from the last broadcast frame** (design.md decision 3 — otherwise the frame-equality guard suppresses the broadcast and the bug cannot appear). Confirm the isolation survives and is not replaced ~150ms later.
- [x] 3.4 Confirm from the logs that no `SEND playback` line shows `displayed=` disagreeing with `mode=`, and that xStudio raises no `[SEL] → broadcast view-state` for an event it tagged remote-induced.
- [x] 3.5 Confirm a genuine local isolation in each application still reaches the other.
