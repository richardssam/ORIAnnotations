## 1. Investigate the real root cause

- [x] 1.1 Confirmed the initial "recalibrate XS_FONT_SCALE directly" hypothesis was wrong: a numeric round-trip test built from the same production constants is tautological with respect to a pure magnitude/calibration bug (it will pass regardless of whether the constant is "correct"), and empirical pixel measurement of the default-size case showed only ~9% variance, not a dramatic mismatch.
- [x] 1.2 Investigated a `TextAnnotation.scale`-drop hypothesis (xStudio's codec ignoring RV's `scale` property); confirmed real via empirical test (xStudio's `font_size` was bit-for-bit identical for `scale=1.0` vs `scale=0.1`), but confirmed via `annotate_mode.mu` that RV's real interactive resize does not exercise `scale` — so this was a real, independent latent bug, not the cause of the reported symptom.
- [x] 1.3 Obtained real repro files (`examples/bad.rv`/`examples/bad.xst`) from the user and inspected the actual `text:` paint-node properties directly, rather than continuing to guess.
- [x] 1.4 Traced the real mechanism in the `openrv_annotations` fork's C++ source (`PaintIPNode.cpp::compileTextComponent`) and Mu source (`annotate_mode.mu::newText`): FTGL was removed, text renders via QPainter using a new `fontSize` property (WCS fraction of image height); the legacy `size`/`ptsize` convention is dead for rendering but still populated for compatibility. The sync codec (`rv_paint_applier.read_stroke`, `rv_annotation_codec.rv_to_font_size`) was still reading the dead `size` property.
- [x] 1.5 Reproduced `bad.rv`/`bad.xst`'s exact values through the harness and confirmed the broadcast `font_size` (405.5) matched `bad.xst` bit-for-bit, confirming the reproduction was faithful before fixing anything.

## 2. Fix the RV-side codec

- [x] 2.1 `python/otio_sync_core/rv_paint_applier.py`: `read_stroke`'s `text:` branch now reads `fontSize` when present; falls back to `(size * 100 * 100 / 1080) * scale` (mirroring `PaintIPNode.cpp`'s own fallback) when absent. Returns a scale-exclusive nominal `"size"` (`font_size_wcs / raw_scale`), keeping `scale` a separately-carried field.
- [x] 2.2 `python/otio_sync_core/rv_annotation_codec.py`: `RV_FONT_SCALE` changed from `5000.0 / 1.3` (≈3846, calibrated for the dead ptsize convention) to `1080.0` (the real WCS-height-fraction reference, matching xStudio's own reference frame). `font_size_to_rv`/`rv_to_font_size` docstrings updated to describe the new meaning.
- [x] 2.3 `_text_spec` (OTIO → RV write path) now writes both the authoritative `fontSize` (`font_size_to_rv(font_size) * scale`) and a reconstructible legacy `size` (`font_size / 10000.0`, chosen so an older RV build's own fallback reconstructs the same `fontSize`).

## 3. Fix the xStudio-side constant

- [x] 3.1 Verified empirically (real pixel measurement of the `bad.rv` reproduction, post-2.2) that the residual xStudio/RV rendered-height ratio (~2.45x) matched the old `XS_FONT_SCALE=2.5` almost exactly.
- [x] 3.2 `python/otio_sync_core/xs_annotation_codec.py`: `XS_FONT_SCALE` changed from `2.5` to `1.0`. Re-verified: xStudio's `font_size` for the `bad.rv` reproduction went from `405.5` to `45.5` (xStudio's own UI default is `40`); measured pixel-height ratio went from ~10-12x (real screenshots) / 2.45x (post RV-fix) down to ~0.92 (comparable to the residual cross-renderer variance already present at default size).

## 4. Keep the independently-real scale-drop fix

- [x] 4.1 `xs_annotation_codec.sync_events_to_xs_captions` folds `TextAnnotation.scale` into `font_size` before applying `XS_FONT_SCALE` (kept from the earlier investigation — real and correct for any caption whose `scale` differs from 1.0, independent of the primary fix above).

## 5. OpenRV script-driven text drawing (both conventions)

- [x] 5.1 `sync_test/python/sync_test/openrv_hook.py`: `_draw_openrv_annotation`'s `text` branch writes a native `text:` paint component. Default payload (`font_size_wcs`, `position`, `text`, `color`, `scale`) writes both `fontSize` (authoritative) and a reconstructible legacy `size`, matching current real RV sessions.
- [x] 5.2 Added a `legacy_size` payload option that, when present, omits `fontSize` entirely and writes only that raw legacy value — driving `read_stroke`'s fallback path deliberately, per the request to keep older-session compatibility tested, not just the modern path.
- [x] 5.3 Confirmed `openrv_hook.py`'s existing `annotation_count`/`_GEOMETRY_PREFIXES` bookkeeping (already includes `"text:"`) and `rv_paint_applier.read_stroke` pick up the new component correctly in both payload modes.

## 6. xStudio state surfacing

- [x] 6.1 `sync_test/python/sync_test/xstudio_hook.py`: `get_xstudio_state`'s per-bookmark loop collects `caption_font_size` (raw `font_size` values) and `caption_position` (raw `[x_xs, y_xs]` pairs), mirroring the existing `stroke_thickness` list for pen.

## 7. Round-trip formulas and runner wiring

- [x] 7.1 `sync_test/python/sync_test/annotation_assertions.py`: imports `XS_FONT_SCALE` from `otio_sync_core.xs_annotation_codec` (not duplicated). Added `expected_xstudio_font_size_from_rv_size` (composing the now-fixed `rv_to_font_size` with `XS_FONT_SCALE`) and `expected_xstudio_caption_position_from_rv_position` (aspect_half transform, reused from the existing pen/shape pattern).
- [x] 7.2 `sync_test/python/sync_test/runner.py`: added `("text", "openrv_to_xstudio")` to `_ANNOTATION_GEOMETRY_FORMULAS`; extended `_verify_annotation_geometry` to read `caption_font_size` (not `stroke_thickness`) for `kind == "text"`, and added an additive position check gated on the yaml block's `position` field.

## 8. sync_tests.yaml entries

- [x] 8.1 `openrv_draws_text_xstudio_verifies`: drives `font_size_wcs: 0.0421717986` — the exact value from `examples/bad.rv`'s real "FOO" annotation that motivated this change — plus a position check. Verified passing end-to-end via `run_tests.sh`.
- [x] 8.2 `openrv_draws_legacy_text_xstudio_verifies`: drives `legacy_size: 0.01` (no `fontSize`), exercising the fallback path, with `nominal` set to the reconstructed value (`0.01 * 10000/1080`). Verified passing end-to-end via `run_tests.sh`.
- [x] 8.3 Spot-checked `openrv_draws_pen_xstudio_verifies` to confirm the shared-file edits (`rv_paint_applier.py`, `rv_annotation_codec.py`) introduced no regression to non-text kinds.

## 9. Spec/doc cleanup

- [x] 9.1 Rewrote `openspec/changes/sync-test-text-annotation-scale/{proposal,design}.md` to describe the real, verified root cause (not the original "recalibrate XS_FONT_SCALE" hypothesis, nor the intermediate "scale-drop" hypothesis).
- [x] 9.2 Rewrote delta specs: `specs/ui-sync-testing/spec.md` (text kind + legacy-fallback scenarios), `specs/otio-annotation-sync/spec.md` (new "RV Font Size Reads The Authoritative On-Screen Property" requirement, corrected "Cross-Host TextAnnotation Font Size Parity"), `specs/rv-annotation-codec/spec.md` (corrected `RV_FONT_SCALE` value, new fontSize-authority scenario).
- [ ] 9.3 Note the visual-breaking change (existing sessions/recordings with `TextAnnotation`s will render smaller in xStudio after this ships) in release notes, per the precedent in `standardize-annotation-scale-position`. (Not yet done — release notes are a separate step from this repo's change artifacts.)
