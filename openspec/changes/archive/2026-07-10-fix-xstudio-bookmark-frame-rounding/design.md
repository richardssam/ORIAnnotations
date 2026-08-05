## Context

`apply_remote_annotation` (and its sibling bulk re-hydration path) converts a received annotation's clip-local frame number into an xStudio `Bookmark` by calling `add_bookmark(target=media)` and then setting `BookmarkDetail.start` to a `datetime.timedelta`. `BookmarkDetail.start`/`.duration` are the only bookmark-positioning fields exposed to Python (`src/python_module/src/py_register.cpp`'s `register_bookmark_detail_class` binds `start_`/`duration_`, both `timebase::flicks`, via a seconds/`timedelta`-accepting caster) — the frame-exact field xStudio uses internally, `BookmarkDetail::logical_start_frame_`, is not bound to Python at all.

xStudio derives that internal frame-exact value (and, by the same function, whatever frame a bookmark visually resolves to) via `FrameRateDuration::frame(flicks)`:

```cpp
int FrameRateDuration::frame(const timebase::flicks flicks) const {
    return static_cast<int>(std::floor(flicks / rate_.to_flicks()));
}
```

This floors. It does not round. The frame's true leading-edge time (`frame / fps` seconds) is therefore the *worst* possible value to request: it has zero margin, and `datetime.timedelta`'s constructor truncates any float `seconds=` argument to microsecond resolution, which for a repeating fraction like `1/24` almost always discards a small positive remainder. The value that actually reaches xStudio is a hair *below* the true boundary, and `floor()` sends it to `frame - 1`.

This was confirmed empirically, not just from reading the source: temporary `[FRAME-DEBUG]` logging on both hosts (added then removed as part of this change) showed RV always computed the correct clip-local frame, and reproduced the exact mechanism —

- `frame=52, fps=24`: requested `2.1666666666666665`s, `timedelta` stored `2.166666s` (not `.166667`), read back unchanged; `2.166666 * 24 = 51.999984` — `floor()` gives `51`.
- `frame=41, fps=24`: same pattern, `40.999968` floors to `40`.
- `frame=0`: `0/24 = 0.0` exactly, no fractional remainder to lose — always worked, which is why the symptom initially looked "first-clip-only" rather than "any-non-multiple-of-fps-frame."

## Goals / Non-Goals

**Goals:**
- Make xStudio-side bookmark placement land on the exact requested frame for any frame number, not just exact multiples of the media's fps.
- Fix this without needing a bound `logical_start_frame_` Python property or a lower-level exact-flicks message (`logical_frame_to_flicks_atom`/`media_frame_to_flicks_atom`) — both exist in xStudio's C++/atom layer but require binding or actor-messaging work disproportionate to the problem.

**Non-Goals:**
- Changing xStudio's `FrameRateDuration::frame()` floor behavior itself — it is correct and used consistently elsewhere in xStudio; our code was feeding it a value with no margin for its own precision limits, not xStudio misbehaving.
- Exposing `logical_start_frame_` or a raw-flicks constructor to Python. A pure-Python, call-site-local fix is sufficient and keeps the change scoped to the one place that was actually wrong.

## Decisions

### D1: Request the frame's midpoint, not its leading edge
Changed `frame / fps` → `(frame + 0.5) / fps`. The true window for frame `N` at `floor()` semantics is `[N/fps, (N+1)/fps)`; the midpoint sits `0.5/fps` seconds (≈20.8ms at 24fps) inside that window on both sides — many orders of magnitude larger than the sub-microsecond truncation error `datetime.timedelta` introduces. This gives floor-based frame recovery a large, deliberate margin instead of none.

**Alternatives considered:**
- *Round instead of floor on the read side* — not our code to change; `FrameRateDuration::frame()` is xStudio's own internal conversion, used for every bookmark in the application, not just ours.
- *Bind `logical_start_frame_` to Python and set it directly* — would require a C++/pybind change to xStudio itself, out of scope for a plugin-side fix, and slower to ship.
- *Use the `logical_frame_to_flicks_atom`/`media_frame_to_flicks_atom` exact-conversion messages* — these exist and would be more "correct" in the sense of avoiding floating-point seconds entirely, but require resolving the right actor reference and message signature from Python; the midpoint approach fixes the actual observed bug with a one-line, easily-verified change and no new actor-messaging surface.
- *Add a fixed small epsilon (e.g. `frame/fps + 1e-6`)* — works numerically but is a magic-number band-aid tied to `timedelta`'s specific microsecond truncation behavior; the midpoint is self-documenting (visibly "the middle of this frame's window") and robust to any precision behavior on either side, not just the one currently observed.

### D2: Fix at the call site, not via a shared coords/codec constant
Unlike `RV_FONT_SCALE`/`XS_FONT_SCALE` (paired constants owned by each host's codec module), this is a single-host, single-mechanism bug: only xStudio's bookmark-write path is affected, and only because of how `datetime.timedelta` + `floor()` interact. A local helper (`_frame_start_timedelta`) in `xstudio_plugin/ori_sync/annotation_sync.py`, used at both call sites that build a `BookmarkDetail.start`, is the right scope — there is no cross-host pairing to maintain.

## Risks / Trade-offs

- **[Risk]** The 0.5-frame margin assumes `datetime.timedelta`'s truncation error is always well under half a frame duration (true for any practical fps — even at 120fps the margin is ~4.2ms vs. sub-microsecond error, a >1000x safety factor). → **Mitigation**: verified numerically across a spread of frame values (0, 1, 24, 29, 41, 48, 52, 100) including both frames that reproduced the original bug; both now floor to the exact requested frame.
- **[Trade-off]** A bookmark's stored `start` time is now `(frame + 0.5) / fps` rather than `frame / fps` — any other code that reads `Bookmark.start`/`.detail.start` directly (rather than going through frame-derivation) and expects the frame's exact leading edge would see a half-frame offset. No such consumer exists in this codebase today (`_frame_base_for_paint_node`-style code always converts back via frame math, never compares raw seconds), but worth noting for future readers.

## Migration Plan

1. Add `_frame_start_timedelta(frame, fps)` to `xstudio_plugin/ori_sync/annotation_sync.py`.
2. Replace both `detail.start = datetime.timedelta(seconds=frame / fps)` call sites with `detail.start = _frame_start_timedelta(frame, fps)`.
3. No RV-side change, no schema/protocol change, no data migration — this only affects how a locally-computed frame number is converted to a `timedelta` at the moment a bookmark is created; already-placed bookmarks are unaffected (and, if they were affected by the bug, will self-correct next time that annotation clip is re-applied).

No rollback complexity: a one-line revert per call site.

## Open Questions

None outstanding — the mechanism was confirmed end-to-end via live debug logging reproducing the exact failure, and the fix was verified live by the user after reinstalling both plugins.
