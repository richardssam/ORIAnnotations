## Why

Annotations synced from RV into xStudio landed on the wrong frame — consistently one frame early — for any frame that isn't an exact multiple of the media's fps (e.g. frame 29 landed on 28, frame 41 landed on 40 at 24fps). Frame 0 (and other exact multiples of fps) always worked, which is why it initially looked like a first-clip-only or multi-clip-sequence-only bug; live debug logging on both hosts showed RV's own clip-local frame computation was correct in every case, and the divergence appeared entirely on xStudio's side, in the step that turns a requested frame number into the bookmark's stored time.

Root cause, confirmed by reading xStudio's own C++ source (`include/xstudio/bookmark/bookmark.hpp`, `src/utility/src/frame_rate_and_duration.cpp`): xStudio derives a bookmark's integer frame back from its stored time via `FrameRateDuration::frame()`, which is `static_cast<int>(std::floor(flicks / rate_.to_flicks()))` — it floors, never rounds. Our sync code requested the frame's exact *leading edge* (`frame / fps` seconds), and Python's `datetime.timedelta` constructor truncates that to microsecond resolution, silently dropping a sub-microsecond remainder for almost any frame that isn't an exact multiple of the fps. The value that reaches xStudio is therefore a hair under the true frame boundary, and `floor()` has zero tolerance for "a hair under" — it lands on `frame - 1`.

## What Changes

- `xstudio_plugin/ori_sync/annotation_sync.py`: add a `_frame_start_timedelta(frame, fps)` helper that requests the **midpoint** of the target frame's time window (`(frame + 0.5) / fps` seconds) instead of its exact leading edge (`frame / fps`). This keeps the value safely inside `[frame/fps, (frame+1)/fps)` even after `datetime.timedelta`'s microsecond truncation, so `floor()` always resolves back to the intended integer frame.
- Apply the helper at both call sites that build a `BookmarkDetail.start` for a received annotation (`apply_remote_annotation`, and the sibling bulk re-hydration path used when re-applying persisted annotations onto freshly-built RV nodes).
- No change needed on the RV side — its clip-local frame computation (`frame - source_range.start_time`, with a `sourceMediaInfo`-based fallback) was verified correct via the same investigation; the bug was isolated entirely to how xStudio converts a requested frame into its stored bookmark time.

## Capabilities

### Modified Capabilities
- `otio-annotation-sync`: add a requirement that xStudio's bookmark-placement code must account for `FrameRateDuration::frame()`'s floor-only (never-rounding) integer derivation when converting a requested frame into `BookmarkDetail.start`, rather than requesting the frame's exact edge and relying on exact floating-point/timedelta precision that isn't guaranteed.

## Impact

- `xstudio_plugin/ori_sync/annotation_sync.py` — `_frame_start_timedelta` helper (new); both `BookmarkDetail.start` call sites in `apply_remote_annotation` and the bulk re-hydration path
- `openspec/specs/otio-annotation-sync/spec.md` — delta spec documenting the floor-safe bookmark-placement requirement
