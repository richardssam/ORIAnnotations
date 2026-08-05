## Context

`sync_test` already has a proven pattern (`sync-test-draw-annotation` / `sync-test-frame-capture` changes) for catching exactly this class of bug: a `draw_annotation` script command makes OpenRV natively produce an annotation via its real broadcast path, and a round-trip assertion computes the expected peer-side value by feeding the driver's nominal input through the *actual* production codec functions, not a hardcoded number. This exists today for `pen`, `rect`, `ellipse`, and `arrow`. `text` was never added.

Initial investigation (source-reading `RV_FONT_SCALE`/`XS_FONT_SCALE`/`PaintIPNode.cpp` in isolation, without rendering anything) produced a plausible-looking but **wrong** hypothesis: that the two independently-tuned scale constants simply didn't compose correctly. Empirical testing (rendering real frames and measuring pixels) disproved it — the default-size case was already close (~9% off). A second hypothesis (RV's interactive resize uses a `scale` property xStudio's codec ignores) was also empirically real (verified: xStudio's received `font_size` was bit-for-bit identical whether RV's `scale` was 1.0 or 0.1) but turned out not to be what caused the user's actual reported symptom, since RV's real interactive resize handle changes `size`/`fontSize`, not `scale` (confirmed by reading `annotate_mode.mu` directly — no drag-resize code path touches `scale` at all).

The real mechanism was only found by inspecting the user's actual saved repro files (`examples/bad.rv`/`examples/bad.xst`) and the *fork's* C++ source (`/Users/sam/git/openrv_annotations`, not vanilla `/Users/sam/git/OpenRV` — a different repo with a materially different `PaintIPNode.cpp`):

```cpp
// PaintIPNode.cpp::compileTextComponent
// ptsize and scale are retained for session-file compatibility but are no
// longer used for rendering (FTGL removed; all text renders via QPainter).
p.ptsize = readProp<FloatProperty>(c, "size", 0.01f) * 100.0f * 100.0f;   // dead for rendering
...
// Legacy (pre-QFont) sessions have no fontSize property; fontSize is a WCS
// fraction of image height, the old size prop was a point value normalized
// for a 1080p image
p.fontSize = fontSizeProp exists ? fontSizeProp.front()
                                 : (p.ptsize / 1080.0f) * scale;          // what's ACTUALLY rendered
```

`annotate_mode.mu`'s `newText()` writes both `size` (legacy) and `fontSize = size * scale` (new, authoritative) when creating a text annotation. `bad.rv`'s real "FOO" had `size == fontSize == 0.0421717986`, `scale == 1` — i.e. a small (~4.2% of frame height), perfectly reasonable on-screen size, exactly matching what the user saw in RV. The sync codec (`rv_paint_applier.read_stroke`, `rv_annotation_codec.rv_to_font_size`) read only the dead `size` and multiplied by the old `RV_FONT_SCALE` (≈3846, calibrated for the *old* ptsize convention) — producing an OTIO `font_size` of ~162 and an xStudio `font_size` of 405.5 (reproduced and confirmed bit-exact against `bad.xst`), wildly disconnected from what RV itself rendered.

## Goals / Non-Goals

**Goals:**
- Make the sync codec read the same property (`fontSize`) that actually governs on-screen rendering, with a correct fallback for sessions/broadcasts that predate it.
- Recalibrate `RV_FONT_SCALE` and `XS_FONT_SCALE` against the *real* mechanism, verified empirically (real screenshots, real pixel measurement, real reproduction of `examples/bad.rv`/`bad.xst`) rather than by further constant archaeology.
- Add `kind: "text"` to script-driven `draw_annotation` (OpenRV driver only, matching the existing rect/ellipse/arrow precedent), covering both the modern (`fontSize`-bearing) and legacy (fallback) paths.
- Add numeric round-trip checks for font size and position.
- Keep the independently-real `TextAnnotation.scale`-drop fix (still correct and needed for any caption whose `scale` differs from 1.0, e.g. non-interactive/API-driven authoring), even though it isn't what caused the reported symptom.

**Non-Goals:**
- Pixel/visual measurement of rendered glyph size as a *permanent, automated* check — text has no straight-edge cross-section the existing line-segment sampler (`visual_geometry.py`) can use. The empirical pixel measurements in this investigation were one-off, throwaway verification scripts, not new `visual_geometry.py` infrastructure.
- xStudio-as-driver text coverage. xStudio's `draw_annotation` harness only supports `kind: "pen"` today (no native text-drawing broadcast path wired up), same limitation already accepted for rect/ellipse/arrow.
- Rewriting `annotate_mode.mu` or `PaintIPNode.cpp` — those are the fork's own (already-shipped) QPainter migration; only the *sync* side needed to catch up to it.

## Decisions

### D1: `read_stroke` reads `fontSize` with the same fallback the C++ reader uses
`rv_paint_applier.read_stroke`'s `text:` branch now mirrors `PaintIPNode.cpp::compileTextComponent` exactly: use the `fontSize` property if present; otherwise reconstruct `(size * 100 * 100 / 1080) * scale`. This guarantees the sync path and the rendering path always agree, for both current and legacy sessions, rather than requiring the sync layer to independently track every future rendering-side migration.

### D2: The returned "size" stays scale-exclusive
The returned dict's `"size"` key is `font_size_wcs / raw_scale` (dividing the already-scale-inclusive `fontSize` back down), keeping `scale` as a separately-carried field — symmetric with how every other kind separates nominal geometry from `scale`, and preserving the existing `TextAnnotation.scale` round-trip requirement (RV's own `scale` property must survive an OTIO→RV→OTIO round-trip unchanged) without entangling it with the font-size fix.

### D3: `RV_FONT_SCALE` becomes the reference height (1080), not a tuned fudge factor
Both `fontSize` (WCS fraction of image height) and xStudio's native `font_size` (pixels at a 1920-wide/1080-tall reference frame, per `font.cpp`'s `text_size * 2.0 / 1920.0`) are already anchored to the *same* 1920×1080 reference. Setting `RV_FONT_SCALE = 1080.0` makes RV's OTIO `font_size` land in that same reference-pixel convention directly, rather than needing an independently-tuned constant on either side.

### D4: `XS_FONT_SCALE` becomes 1.0, confirmed (not assumed) empirically
After D1-D3, measuring real rendered pixels for the exact `bad.rv` reproduction gave an xStudio/RV height ratio of ~2.45 — matching the *old* `XS_FONT_SCALE = 2.5` almost exactly. That is direct evidence the extra multiplier was never doing anything except compensating in the wrong direction; the correct value is 1.0. (Residual ~8% variance after the fix is comparable to what the default-size case showed from the start, and attributable to ordinary cross-renderer font-metric differences, not a formula bug.)

### D5: `_text_spec` (OTIO → RV) writes both `fontSize` and a reconstructible legacy `size`
For round-trip symmetry: `fontSize = font_size_to_rv(font_size) * scale` (authoritative), and `size = font_size / 10000.0` (chosen so the C++ fallback, applied by an older RV build lacking `fontSize` support, reconstructs the same `fontSize` — the scale cancels out of that reconstruction algebraically, so this legacy value does not itself need to multiply by `scale`).

### D6: `draw_annotation kind="text"` supports both paths via one payload switch
Default behavior writes `fontSize` (+ a reconstructible legacy `size`), matching current real RV sessions. A `legacy_size` payload option, when present, omits `fontSize` entirely and writes only that raw legacy value — driving `read_stroke`'s fallback path deliberately, per the user's request to keep older-session compatibility covered, not just the modern path.

### D7: Keep the `TextAnnotation.scale`-drop fix, scoped correctly
`sync_events_to_xs_captions` folds `scale` into `font_size` before applying `XS_FONT_SCALE`. This remains correct and real (verified: xStudio's `font_size` was previously invariant to RV's `scale`), even though RV's interactive resize handle doesn't currently exercise a non-1.0 `scale` — some other authoring path (a different tool, a future UI feature, direct API use) could, and this closes that gap regardless.

## Risks / Trade-offs

- **[Risk] BREAKING (visual)**: recalibrating both constants changes the rendered size of every existing `TextAnnotation` synced into xStudio, including ones already saved in sessions/recordings — in the *correct* direction (much smaller, matching RV), but still a visible change. → **Mitigation**: call out explicitly in release notes.
- **[Risk]** The fallback formula (`size*10000/1080*scale`) is specific to this fork's particular migration history; if RV's rendering changes again (another renderer migration, another property), the sync layer will again silently drift from what's on screen unless someone remembers to update it. → **Mitigation**: the new round-trip test is the regression guard for *this* mechanism; the general lesson (verify empirically against real rendered pixels, not source-code archaeology alone) is captured here for next time.
- **[Trade-off]** Numeric-only scope won't catch renderer-level discrepancies (kerning, glyph metrics, wrap behavior) a pixel measurement would — the ~8% residual variance is accepted as ordinary cross-renderer variance, not chased further.
- **[Risk]** `otio-annotation-sync`'s existing spec text hardcodes `RV_FONT_SCALE = 5000.0`, already stale before this change (actual code was `5000.0/1.3`). → **Mitigation**: corrected in the same delta-spec edit that documents the new value and mechanism.

## Migration Plan

1. Fix `rv_paint_applier.read_stroke` (D1-D2) and `rv_annotation_codec` (`RV_FONT_SCALE`, D3; `_text_spec`, D5) — pure codec fixes, verified against `examples/bad.rv`/`bad.xst`'s real values before touching xStudio's constant.
2. Recalibrate `XS_FONT_SCALE` (D4), verified via real pixel measurement of the same reproduction.
3. Add `draw_annotation kind="text"` (D6) with both payload modes, xStudio state surfacing, round-trip formulas, and two `sync_tests.yaml` entries (real-world values; legacy fallback).
4. Run both new tests plus a spot-check of an existing pen test to confirm no regression from the shared-file edits.
5. Update `openspec/specs/ui-sync-testing/spec.md`, `otio-annotation-sync/spec.md`, and `rv-annotation-codec/spec.md`.
6. Release notes: call out the visual-breaking change.

No rollback complexity: touches two codec files, one test-harness file, and one yaml config; reverting the two constant changes alone is a one-line revert each if ever needed.

## Open Questions

None outstanding — the mechanism was confirmed end-to-end against real repro files rather than left as a hypothesis.
