## 1. Investigate the reported symptom

- [x] 1.1 Reproduced live: annotations synced from RV landed one frame off in xStudio on non-first clips of a multi-clip sequence; frame-0 annotations were always correct.
- [x] 1.2 Ruled out a per-clip duration mismatch: pulled xStudio's per-clip `source_range` and RV's own EDL frame counts for all 8 clips in a real multi-clip session — both hosts agreed exactly (`[101, 101, 101, 20, 20, 20, 20, 101]`).
- [x] 1.3 Ruled out a `source_range=None`/branch-selection mismatch between clips: confirmed via live data that the same clip could have `source_range: null` while still round-tripping correctly on RV's send side, and that both a "good" and a "bad" clip took the identical `_frame_base_for_paint_node` fallback branch in `rvplugin/ori_sync/annotation_sync.py`.
- [x] 1.4 Added temporary `[FRAME-DEBUG]` logging on both RV (`rvplugin/ori_sync/annotation_sync.py`) and xStudio (`xstudio_plugin/ori_sync/annotation_sync.py`) sides, reinstalled the RV package, and had the user reproduce live.
- [x] 1.5 Logs showed RV's clip-local frame computation was correct in every case (e.g. `frame=89940, source_start=89899 -> otio_frame=41`, exactly right). The divergence was isolated to xStudio's `apply_remote_annotation`.
- [x] 1.6 Read back the actual bookmark timing xStudio stored (`Bookmark.detail.start`) and back-derived the frame two ways; found the read-back seconds value was consistently a hair under the true frame boundary (e.g. `51.999984` for a requested frame of `52`).
- [x] 1.7 Traced the mechanism to xStudio's own C++ source: `BookmarkDetail::logical_start_frame_` (`include/xstudio/bookmark/bookmark.hpp`) and its derivation via `FrameRateDuration::frame()` (`src/utility/src/frame_rate_and_duration.cpp:49-51`), which is `static_cast<int>(std::floor(flicks / rate_.to_flicks()))` — floor, never round.
- [x] 1.8 Confirmed `BookmarkDetail.start`/`.duration` (seconds-based, via `datetime.timedelta`) are the only bookmark-positioning fields bound to Python (`src/python_module/src/py_register.cpp`); the frame-exact `logical_start_frame_` field is not exposed to Python at all.

## 2. Fix the xStudio-side codec

- [x] 2.1 `xstudio_plugin/ori_sync/annotation_sync.py`: added `_frame_start_timedelta(frame, fps)`, requesting the midpoint of the target frame's time window (`(frame + 0.5) / fps`) instead of its exact leading edge (`frame / fps`).
- [x] 2.2 Applied the helper at both call sites that build a `BookmarkDetail.start`: `apply_remote_annotation`, and the bulk re-hydration path used when re-applying persisted annotations onto freshly-built RV nodes.
- [x] 2.3 Verified numerically (standalone script) across a spread of frame values (0, 1, 24, 29, 41, 48, 52, 100) at 24fps, including both frames that reproduced the original bug — old code failed on 29 and 41, new code correct on all.

## 3. Clean up and verify live

- [x] 3.1 Removed the temporary `[FRAME-DEBUG]` logging from both `rvplugin/ori_sync/annotation_sync.py` and `xstudio_plugin/ori_sync/annotation_sync.py` now that the root cause was confirmed and fixed.
- [x] 3.2 Confirmed both edited files compile cleanly (`python3 -m py_compile`).
- [x] 3.3 Reinstalled the RV `ori_sync` package (`rvplugin/ori_sync/reinstall.csh`) — RV loads from the installed `rvpkg`, not the repo directly.
- [x] 3.4 User restarted both apps and confirmed live: annotations on non-first-clip, non-multiple-of-fps frames now land correctly.

## 4. Documentation

- [x] 4.1 Wrote up this OpenSpec change (proposal, design, delta spec) documenting the root cause and fix, retroactively, per user request.
