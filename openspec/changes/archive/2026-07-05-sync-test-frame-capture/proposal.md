## Why

The `fix-rv-shape-border-width` change's verification loop currently ends in "have the user visually inspect the rendered frame" — which is exactly how the 2x rect-border bug was found, but it doesn't scale: every future regression in this class needs a human to look at a screen. `sync_test` already drives real, live app instances and can make them draw known-geometry annotations (`draw_annotation`); the missing piece is asking those same live instances to render their current frame to an image, and a comparison step that checks the annotation actually appears where/how big it should, using the geometry the test itself specified — the same *technique* `testchart/compare_testchart.py` already uses (cross-section sampling, centroid offset in pixels), but driven by the test's own known ground truth instead of that script's hardcoded reference-chart geometry.

Separately, researching this surfaced that xStudio has a direct, purpose-built render API (`OffscreenViewport.render_bookmark_with_transparency`, `include_image=True` for a fully composited frame) that `testchart/batch_xstudio.py` doesn't use — it instead exports an annotation-only overlay (`include_image=False`, via the `ori_annotations` plugin's `export_annotations()`) and composites it onto a separately-known background PNG with an external PIL subprocess. That two-step dance exists only because `batch_xstudio.py` predates (or never adopted) the direct API; switching it to call `render_bookmark_with_transparency` directly with `include_image=True` removes an entire compositing step for free.

## What Changes

- Add a `capture_frame` script-driven action to both hooks:
  - **xStudio**: resolve the bookmark at the target media/frame and call `OffscreenViewport(conn).render_bookmark_with_transparency(output_path, bookmark_id, include_image=True, include_drawings=True)` — a single direct call, no compositing.
  - **OpenRV**: an in-process Qt grab of the live viewport (`rv.commands.sessionGLView()` widget `.grab().save(path)`), mirroring `testchart/grab_frame.py`'s already-proven fallback technique — chosen over external `rvio` because `rvio` has known crash issues in this build (see `testchart/`'s font-crash fallback) and, more importantly, the sync_test app is already running live, so an in-process grab needs no save/reload round-trip.
- Add a geometry-driven pixel comparison helper (extending `annotation_assertions.py` or a sibling module) that, given the same `draw_annotation` payload geometry (`min`/`max`, `points`, `start`/`end`) and the media's actual output resolution, projects the expected on-screen region via `coords.otio_to_px` and measures whether annotation-colored ink appears where/how thick expected — the same cross-section/centroid technique as `compare_testchart.py`, driven by the test's own known geometry rather than a hardcoded reference chart.
- Wire this into the existing `annotation_geometry` yaml block (or a sibling `visual_check` block) so shape tests can optionally assert on rendered pixels, not just round-tripped numbers — closing the gap that let a visually-2x-wrong-but-numerically-self-consistent bug through undetected until manual inspection.
- Modernize `testchart/batch_xstudio.py` to call `render_bookmark_with_transparency(..., include_image=True, include_drawings=True)` directly instead of `export_annotations()` + external PIL compositing. Implementation-only change (same kind of output image, produced more directly); no capability-level requirement changes to `xstudio-annotation-export` (its own "Optional Annotation Image Export" feature, used for its own OTIO-interchange purpose, is untouched).

## Capabilities

### New Capabilities
- `sync-test-visual-verification`: capturing a live app's current frame to an image and comparing rendered annotation geometry/thickness against the test's own known ground truth.

### Modified Capabilities
- `ui-sync-testing`: adds the `capture_frame` script-driven action alongside the existing action vocabulary.

## Impact

- `sync_test/python/sync_test/openrv_hook.py`: new `capture_frame` branch (Qt grab).
- `sync_test/python/sync_test/xstudio_hook.py`: new `capture_frame` branch (`OffscreenViewport.render_bookmark_with_transparency`).
- `sync_test/python/sync_test/annotation_assertions.py` (or a new sibling module): pixel-space projection + cross-section comparison helper.
- `sync_test/python/sync_test/runner.py`: wiring to invoke capture + comparison as part of a test, reusing the existing `logs_dir` for output images.
- `sync_test/sync_tests.yaml` / `README.md`: document `capture_frame`; extend the shape tests from `fix-rv-shape-border-width` (or add new ones) with a visual check.
- `testchart/batch_xstudio.py`: simplified rendering call (implementation-only, no behavior-visible change to its output).
- No changes to `xstudio-annotation-export`'s own capability — its `render_bookmark_with_transparency(include_image=False, ...)` usage for the plugin's own export feature is a separate, unaffected caller.
