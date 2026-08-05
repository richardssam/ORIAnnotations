## Why

Text annotations drawn in OpenRV render dramatically oversized once synced to xStudio (confirmed against a real user-drawn example, `examples/bad.rv`/`examples/bad.xst`: xStudio's caption `font_size` came out as `405.5` — off the top of xStudio's own 1-300 UI range — for text that renders small and legible in RV itself). Root cause, confirmed empirically by patching the fork's C++ source and reproducing the exact broadcast values: the `openrv_annotations` fork removed FTGL and now renders all text via QPainter using a new paint-node property, `fontSize` (a WCS fraction of image height), while the legacy `size`/`ptsize` convention is "retained for session-file compatibility but no longer used for rendering" (`PaintIPNode.cpp`). The sync/broadcast codec (`rv_paint_applier.read_stroke`, `rv_annotation_codec.rv_to_font_size`) never learned about `fontSize` — it kept reading the dead legacy `size` property through a scale factor (`RV_FONT_SCALE`) calibrated for the old ptsize convention, so what got broadcast to xStudio was computed from the wrong property using a stale formula. RV's own on-screen render (which correctly uses `fontSize`) stayed small; xStudio's received value did not.

Every other annotation kind (pen width, rect/ellipse border width, arrow thickness) already has a `sync_test` round-trip check that would have caught a codec/formula regression like this (see `annotation_assertions.py`'s stated purpose: "fails exactly when a codec's forward and reverse directions disagree") — text had no such check, so this shipped and went unnoticed through an RV-side rendering migration (FTGL → QPainter) that the sync layer was never updated for.

## What Changes

- Fix `rv_paint_applier.read_stroke`'s `text:` branch to read the paint node's `fontSize` property (the actual on-screen size) instead of the dead legacy `size`, with a fallback that reconstructs the same value `PaintIPNode.cpp`'s own C++ fallback computes (`size * 10000 / 1080 * scale`) for sessions/broadcasts predating `fontSize` — so both old and current RV builds/sessions convert correctly.
- Recalibrate `rv_annotation_codec.RV_FONT_SCALE`: `5000/1.3` (≈3846, tuned for the dead ptsize convention) → `1080` (the real WCS-height-fraction reference the current renderer uses, which happens to be the same reference frame xStudio's own `font_size` is already anchored to).
- `rv_annotation_codec._text_spec` (OTIO → RV, used when applying a received `TextAnnotation`) now writes both the authoritative `fontSize` and a reasonable legacy `size` (derived so the C++ fallback would reconstruct the same value on an older build), so round-tripping stays correct in both directions.
- Recalibrate `xs_annotation_codec.XS_FONT_SCALE`: `2.5` → `1.0`. Once the RV side produces a correctly-anchored OTIO `font_size`, empirical measurement showed the residual xStudio/RV rendered-size ratio (~2.45x) tracked the old `2.5` constant almost exactly — confirming no separate xStudio-side fudge factor was ever needed once RV's side stopped being wrong.
- Kept a real, independently-verified secondary fix: `xs_annotation_codec.sync_events_to_xs_captions` previously ignored `TextAnnotation.scale` entirely, so a caption whose scale differs from 1.0 rendered as if it were always 1.0 in xStudio. Now folds `scale` in before applying `XS_FONT_SCALE`. (Verified via the RV fork's own tooling that the interactive drag/resize path does not currently exercise `scale` — it goes through `size`/`fontSize` — so this fix is a latent-bug close, not the primary cause of the reported symptom, but is real and was silently wrong.)
- Add `kind: "text"` to script-driven `draw_annotation` for OpenRV, writing a native RVPaint text component (`fontSize`, legacy `size`, `position`, `scale`, ...) and broadcasting it via the same real send path pen/rect/ellipse/arrow already use. Supports a `legacy_size` payload option that omits `fontSize` entirely, to drive the pre-`fontSize` fallback path.
- Surface xStudio's native per-caption `font_size` and `position` in `/state`, mirroring the existing `stroke_thickness` list for pen.
- Add a numeric round-trip check for font size and position, following the existing formula-composition pattern, plus two `sync_tests.yaml` entries: one reproducing the exact real-world `examples/bad.rv` values, one exercising the legacy (no-`fontSize`) fallback path.
- **BREAKING (visual, not API)**: existing sessions/recordings containing `TextAnnotation`s will render at a different (corrected, much smaller) size in xStudio after this ships.

## Capabilities

### Modified Capabilities
- `ui-sync-testing`: extend `Script-Driven Annotation Drawing` and `Round-Trip Annotation Geometry Verification` to cover `kind: "text"` (font size and position), OpenRV-driving/xStudio-verifying, including the legacy-fallback path.
- `otio-annotation-sync`: correct the stale `RV_FONT_SCALE` value reference, and add a requirement that RV's font-size unit conversion must read the host's actual authoritative on-screen size property (`fontSize`, not the dead legacy `size`) — with a defined, spec'd fallback for sessions/broadcasts predating it — plus the cross-host parity requirement this enables.
- `rv-annotation-codec`: correct the stale `RV_FONT_SCALE = 5000.0` reference; document that RV owns two size properties (`fontSize` authoritative, `size` legacy-compatibility-only) and which one governs on-screen rendering.

## Impact

- `python/otio_sync_core/rv_paint_applier.py` — `read_stroke`'s `text:` branch: read `fontSize` with legacy fallback
- `python/otio_sync_core/rv_annotation_codec.py` — `RV_FONT_SCALE` (3846 → 1080), `_text_spec` (write both `fontSize` and legacy-compatible `size`)
- `python/otio_sync_core/xs_annotation_codec.py` — `XS_FONT_SCALE` (2.5 → 1.0), `sync_events_to_xs_captions` folds in `scale`
- `sync_test/python/sync_test/openrv_hook.py` — `_draw_openrv_annotation`: `text` kind, `font_size_wcs`/`legacy_size` payload options
- `sync_test/python/sync_test/xstudio_hook.py` — `get_xstudio_state`: surface `caption_font_size` / `caption_position`
- `sync_test/python/sync_test/annotation_assertions.py` — text round-trip formulas (font size, position)
- `sync_test/python/sync_test/runner.py` — `_ANNOTATION_GEOMETRY_FORMULAS`, `_verify_annotation_geometry` (font_size/position field handling for text)
- `sync_test/sync_tests.yaml` — `openrv_draws_text_xstudio_verifies` (real-world values), `openrv_draws_legacy_text_xstudio_verifies` (fallback path)
- `openspec/specs/ui-sync-testing/spec.md`, `openspec/specs/otio-annotation-sync/spec.md`, `openspec/specs/rv-annotation-codec/spec.md` — delta specs
