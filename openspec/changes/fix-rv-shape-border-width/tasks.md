## 1. Fix the codec (rect/ellipse)

- [x] 1.1 In `python/otio_sync_core/rv_annotation_codec.py`'s `_box_shape_spec`, expand `min`/`max` outward by `size / 2.0` on every edge (subtract from `min[0]`/`min[1]`, add to `max[0]`/`max[1]`) and write `borderWidth = stroke["size"]` directly (drop the `/ 2.0`).
- [x] 1.2 In `python/otio_sync_core/rv_paint_applier.py`'s `read_stroke` rect/ellipse branch, contract the read-back `min`/`max` inward by `border_width / 2.0` on every edge (add to `min`, subtract from `max`) and read `"size": border_width` directly (drop the `* 2.0`).
- [x] 1.3 Confirm (already checked during design, re-verify after the edit) that no other code path reads RV's raw `min`/`max` node properties expecting the true (unexpanded) geometry — `annotation_sync.py::_apply_shape_annotation` and `rv_annotation_codec.py::rv_strokes_to_sync_events` are the only two sites and both funnel through `_box_shape_spec`/`read_stroke` respectively.

## 2. Update the existing unit test

- [x] 2.1 In `tests/otio_sync/test_rv_annotation_codec.py::test_shape_forward_reverse_roundtrip`, extend the assertion to also check `min`/`max` round-trip to their original values (not just `size`) — the current size-only assertion would keep passing even with a broken/mismatched min-max expand-contract pair, since the size formula alone self-cancels regardless.

## 3. Extend `draw_annotation` to ellipse and arrow (OpenRV only)

- [x] 3.1 In `sync_test/python/sync_test/openrv_hook.py::_draw_openrv_annotation`, add an `ellipse` kind: same prop shape as `rect` (`min`, `max`, `borderColor`, `innerColor`, `borderWidth`, `startFrame`, `duration`, `eye`, `uuid`, `softDeleted`), accepting `border_width`/`border_rgba`/`inner_rgba` payload fields identically to rect.
- [x] 3.2 Add an `arrow` kind: `startPos`, `endPos`, `borderColor`, `innerColor`, `borderWidth=0.0`, `thickness` (from a new `thickness` payload field), `startFrame`, `duration`, `eye`, `uuid`, `softDeleted` — matching `_arrow_spec`'s prop shape. Accept `start`/`end` payload fields for the two points (with reasonable defaults), and a `color` field for `borderColor`/`innerColor`.
- [x] 3.3 Update `sync_test/README.md`'s `draw_annotation` documentation to list `ellipse`/`arrow` alongside `pen`/`rect`, noting (as already stated for rect) that all shape kinds are OpenRV-driver-only.

## 4. Extend the assertion helper

- [x] 4.1 In `sync_test/python/sync_test/annotation_assertions.py`, add `expected_xstudio_thickness_from_rv_ellipse_border_width` (identical formula to the rect one — same `_box_shape_spec`/`read_stroke` code path) and `expected_xstudio_thickness_from_rv_arrow_thickness` (mirroring `_arrow_spec`'s `thickness = size / 2.0` and its `read_stroke` inverse `size = thickness * 2.0`, feeding into xStudio's shared shape-tessellation thickness formula).
- [x] 4.2 In `sync_test/python/sync_test/runner.py`'s `Runner._ANNOTATION_GEOMETRY_FORMULAS`, register the new `("ellipse", "openrv_to_xstudio")` and `("arrow", "openrv_to_xstudio")` entries.

## 5. Add yaml test cases

- [x] 5.1 Add `openrv_draws_ellipse_xstudio_verifies` to `sync_test/sync_tests.yaml`, mirroring `openrv_draws_rect_xstudio_verifies`'s structure (`add_media`, `set_selection`, `draw_annotation` with `kind: "ellipse"`), using a realistic nominal `border_width` (in the same small-magnitude range established for rect, e.g. `0.005`) and the same transparent-fill / mid-intensity-border color convention established for rect (so color isn't a confound and fill-vs-outline asymmetry doesn't distort the visual comparison).
- [x] 5.2 Add `openrv_draws_arrow_xstudio_verifies`, using a realistic nominal `thickness` (same small-magnitude range as pen, e.g. `0.005`–`0.01`) and a mid-intensity, non-primary color.

## 6. Live verification

- [ ] 6.1 Re-run `openrv_draws_rect_xstudio_verifies` after the task 1 fix; confirm the automated round-trip check still passes (it should — task 1 keeps forward/reverse as exact inverses) *and* have the user visually confirm the border now reads the same thickness in both apps, not 2x.
- [ ] 6.2 Run `openrv_draws_ellipse_xstudio_verifies`; confirm the round-trip check passes and have the user visually confirm the ellipse border thickness now matches between apps (this is the live confirmation that D2's "ellipse shares rect's rendering behavior" assumption holds — if it doesn't visually match, that's new information, not an implementation error, and should be recorded rather than silently patched further).
- [ ] 6.3 Run `openrv_draws_arrow_xstudio_verifies`; confirm the round-trip check passes and have the user visually confirm whether the arrow shaft already matches (per D3, arrow is *not* touched by the task 1 fix — this test's job is to reveal whether it needs its own, separate fix, not to prove the existing one).
- [ ] 6.4 Run the full existing `tests/otio_sync/` suite; confirm no new failures beyond the pre-existing 4 (selection-sync feature in progress + one ordering-sensitive test, both predating this change).

## 7. Record findings

- [ ] 7.1 Record whether ellipse needed any deviation from the rect fix (task 6.2's result).
- [ ] 7.2 Record whether arrow needs its own fix, and if so, open that as a follow-up rather than expanding this change's scope reactively.
