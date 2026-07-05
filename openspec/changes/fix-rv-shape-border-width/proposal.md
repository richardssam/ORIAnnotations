## Why

Live testing (`openrv_draws_rect_xstudio_verifies`, from the `sync-test-draw-annotation` change) surfaced a real, visually-confirmed bug: an OpenRV-drawn rectangle's border renders **exactly twice as thick** in xStudio for the same nominal `size`. Root cause: OpenRV's native rect/ellipse border rendering paints **inward only** from the `min`/`max` bounding box (nothing is drawn outside it), while xStudio's tessellated-stroke rendering (its only shape rendering path — it has no native shape primitives) paints a normal **centered** stroke, half the thickness on each side of the path. `rv_annotation_codec.py`'s current mapping (`_box_shape_spec`: `borderWidth = size / 2.0`; its exact inverse in `rv_paint_applier.read_stroke`: `size = border_width * 2.0`) treats `size` as if handing each app a symmetric half is equivalent — it isn't, because the two apps spend that half in geometrically different places (RV: all inward; xStudio: half in, half out). The observed evidence matches exactly: the two apps' *inner* edges coincide (both include the same inward component), while xStudio's outer edge extends further with nothing corresponding in RV.

The desired fix, per the user (who has visibility into RV's rendering source): a shape annotation's `size` should behave like a genuine line-width — the same way `PaintVertices.size` already does for pens, producing a normal centered-stroke appearance — for every shape kind, not just rect.

## What Changes

- Fix `rv_annotation_codec.py`'s `_box_shape_spec` (rect/ellipse) and, pending verification, `_arrow_spec`, plus their exact inverses in `rv_paint_applier.read_stroke`, so that a `RectangleAnnotation`/`EllipseAnnotation`/`ArrowAnnotation.size` produces a visually-centered stroke of that width in RV, matching xStudio's rendering — likely requiring both (a) a corrected `borderWidth`/`thickness` ↔ `size` formula and (b) compensating the RV-side `min`/`max` (or `start`/`end`) outward, since RV's rendering has no way to paint outside the box it's given. The exact geometric approach is a design decision to pin down, not assumed up front.
- Extend `sync_test`'s `draw_annotation` action (OpenRV side only — xStudio still has no native shape-drawing broadcast path, per the `sync-test-draw-annotation` change's design D3) to also support `ellipse` and `arrow` kinds, alongside the existing `pen`/`rect`.
- Add `openrv_draws_ellipse_xstudio_verifies` and `openrv_draws_arrow_xstudio_verifies` test cases to `sync_tests.yaml`, following the same `annotation_geometry` round-trip pattern as the existing rect test, extending `annotation_assertions.py` with the corresponding expected-value formulas.
- Use these three shape tests (rect, ellipse, arrow) as the live verification harness for the fix — re-running `openrv_draws_rect_xstudio_verifies` (and the two new ones) is how the fix gets confirmed, not just unit-tested in isolation.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `rv-annotation-codec`: corrects the shape border-width/line-width semantics so `size` produces a centered-stroke appearance consistent with xStudio's rendering, for rect/ellipse (and arrow, pending verification of whether arrows have the same inward-only rendering behavior as bounding-box shapes).
- `ui-sync-testing`: extends the `draw_annotation` script-driven action to `ellipse` and `arrow` kinds (OpenRV driver only) and the round-trip geometry verification to cover them.

## Impact

- `python/otio_sync_core/rv_annotation_codec.py`: `_box_shape_spec`, possibly `_arrow_spec`.
- `python/otio_sync_core/rv_paint_applier.py`: `read_stroke`'s rect/ellipse/arrow branches (the exact inverse of the above).
- `tests/otio_sync/test_rv_annotation_codec.py`: the existing `test_shape_forward_reverse_roundtrip` test encodes the *current* (buggy) semantics as the "correct" round-trip — it will need updating to assert the corrected geometry instead.
- `sync_test/python/sync_test/openrv_hook.py`: extend `_draw_openrv_annotation` with `ellipse`/`arrow` kinds.
- `sync_test/python/sync_test/annotation_assertions.py`: add ellipse/arrow expected-value formulas.
- `sync_test/sync_tests.yaml`: two new test cases.
- Possibly `testchart/` (vector_primitives/vector_shapes reference charts) if the fix changes what "correct" rendering looks like there too — to be scoped in design.md.
