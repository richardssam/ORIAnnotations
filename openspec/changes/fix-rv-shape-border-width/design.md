## Context

`_box_shape_spec` (rect/ellipse forward, OTIO → RV) and its exact inverse in `rv_paint_applier.read_stroke` currently do:

```python
# forward
("borderWidth", TYPE_FLOAT, [stroke["size"] / 2.0], 1)
# reverse
"size": border_width * 2.0
```

Live testing (`openrv_draws_rect_xstudio_verifies`) showed the two apps' rendered borders differ by exactly 2x, with the **inner** edges coinciding and the **outer** edge diverging — only present on the xStudio side. Working through the geometry against that evidence (below) shows the current formula is not merely miscalibrated by a constant — it is compensating for the wrong thing.

### Root-cause geometry

xStudio has no native shape primitives; every shape is tessellated into a stroke polyline along the rectangle's `min`/`max` boundary and rendered as a normal, **centered** stroke (`xs_annotation_codec.py`'s `is_rect` branch: `thickness = size / (2 * aspect_half)`, using the exact same formula already validated for pens — i.e. this side is not the problem). A centered stroke of total width `size` extends `size/2` on *each* side of the boundary path: `[boundary − size/2, boundary + size/2]`.

OpenRV's native rect/ellipse-border renderer, per the user (who has visibility into its source), paints **inward only** from `min`/`max` — nothing is drawn outside the box. With the current formula (`borderWidth = size/2`), RV's rendered stripe is `[boundary − size/2, boundary]` — i.e. exactly the *inner half* of what a centered stroke of width `size` would occupy. That is precisely what the live test showed: inner edges (`boundary − size/2`) match exactly; RV simply never paints the outer half (`boundary` to `boundary + size/2`) that xStudio does. Total width: RV = `size/2`, xStudio = `size` — a clean 2x, matching the observed result exactly.

## Goals / Non-Goals

**Goals:**
- Make an OTIO shape annotation's `size` behave as a genuine line-width for rect and ellipse: rendering in RV should visually match xStudio's centered-stroke rendering for the same `size`, verified empirically (not just self-consistently) via the live `sync_test` harness.
- Extend `draw_annotation`/`sync_tests.yaml` to ellipse and arrow so the fix (and the open question of whether arrow needs the same fix) gets verified live, not assumed.
- Keep the existing RV↔OTIO round-trip exact (a shape drawn or received in RV must read back to the same `size`/`min`/`max` a peer would compute).

**Non-Goals:**
- Touching RV's own C++/native rendering code — it lives in a separate build (`openrv_annotations`) this repo doesn't own; the fix is entirely in the Python codec's wire-format/coordinates, compensating for RV's rendering behavior rather than changing it.
- Fixing arrow's `thickness`/`size` formula preemptively — arrows are a simple line stroke (structurally different from a box border), which may already render centered/correctly; see D3.
- xStudio → RV shape coverage (still gated on xStudio's native shape-drawing broadcast path not existing yet, per the prior change's design D3 — unchanged here).
- Visual/pixel-measurement tooling — the live `sync_test` round-trip check plus the user's own visual inspection is the verification loop for this change, not a new `testchart/compare_thickness.py`-style pixel-measurement tool.

## Decisions

### D1: Compensate by expanding `min`/`max` outward, and set `borderWidth`/`size` 1:1 (not `size/2.0`)

Since RV can only paint inward from whatever box it's given, and it needs to paint the *entire* desired width (not half of it) to match xStudio's full centered-stroke width, the fix moves the "half" compensation from the width formula into the geometry:

- **Forward** (`_box_shape_spec`): expand the box outward by `size/2` on every edge before writing `min`/`max`, and write `borderWidth = size` directly (no `/2.0`):
  ```python
  half = stroke["size"] / 2.0
  expanded_min = [stroke["min"][0] - half, stroke["min"][1] - half]
  expanded_max = [stroke["max"][0] + half, stroke["max"][1] + half]
  # props: min=expanded_min, max=expanded_max, borderWidth=stroke["size"]
  ```
  RV then paints the full width `size` inward from the expanded box, landing exactly on `[true_boundary − size/2, true_boundary + size/2]` — matching xStudio's centered stroke on both inner *and* outer edges.
- **Reverse** (`rv_paint_applier.read_stroke`): contract the read-back box inward by `border_width/2` to recover the original geometric `min`/`max`, and read `size = border_width` directly (no `* 2.0`):
  ```python
  half = border_width / 2.0
  true_min = [min_val[0] + half, min_val[1] + half]
  true_max = [max_val[0] - half, max_val[1] - half]
  ```

This assumes `min`/`max` are literal numeric min/max (index-0 = x, index-1 = y, with `min[i] <= max[i]` on both axes) — confirmed as the convention this codebase actually maintains (`generate_testchart.py`'s `make_rect_ann` explicitly normalizes to literal min/max before constructing a `RectangleAnnotation`), so expand-by-subtracting-from-min / adding-to-max is safe without axis-direction special-casing.

*Alternative considered:* keep `min`/`max` untouched and instead double `borderWidth` (`= size`, no box expansion). Rejected — this makes RV's *inner* edge march further inward (`size` instead of `size/2`), which would break the one thing that currently *does* match (the inner edge), producing a border that's still off in a different way (matches on the *outer* edge instead, since RV's outer edge is always the box it's given) rather than agreeing on both edges.

### D2: Ellipse shares the same code path — verify, don't assume, via the new live test

`_box_shape_spec`/`read_stroke`'s combined rect/ellipse branch means D1's fix applies to both identically with no special-casing. Whether OpenRV's *ellipse*-border renderer has the exact same inward-only behavior as its rect-border renderer is not independently confirmed — the new `openrv_draws_ellipse_xstudio_verifies` test (and the user's own visual check) is how this gets confirmed rather than assumed. If ellipse turns out to differ, that's a follow-up, not blocking this change.

### D3: Arrow is not touched by the formula fix — the new live test is how we find out if it needs one

`_arrow_spec` uses a structurally different field (`thickness`, not `borderWidth`) to draw a shaft — a simple line stroke between two points, not a border traced around a box. Line strokes are typically rendered centered by construction (the same assumption already validated for pens via `RV_WIDTH_SCALE`), so arrow may already be correct. Rather than guess, `openrv_draws_arrow_xstudio_verifies` (new) exercises this live. If it shows the same 2x/inner-edge-only pattern, `_arrow_spec`/its `read_stroke` inverse get the analogous fix (expand `start`/`end` outward along the perpendicular... though for a line, "outward" doesn't parallel a box the same way — this would need its own geometric reasoning at that point, not assumed now) as a follow-up; if it matches, no change needed there.

### D4: Update the existing round-trip unit test to also assert geometry, not just size

`tests/otio_sync/test_rv_annotation_codec.py::test_shape_forward_reverse_roundtrip` currently only asserts `size` round-trips — it would keep passing after D1's fix even if the min/max expansion/contraction were wrong (e.g. swapped sign), because the size formula alone still self-cancels. Extend it to also assert `min`/`max` round-trip to their original values, since D1 introduces a real geometry transform in both directions that a size-only check can't catch a broken inverse for.

## Risks / Trade-offs

- **[Reasoned from formulas + one live data point, not fully re-verified against pixels for every case]** → Mitigated by using the existing `openrv_draws_rect_xstudio_verifies` (already proven to catch this exact bug) as the acceptance test, plus the user's direct visual re-inspection after the fix — not just a passing round-trip assertion, which (per D4) can't fully validate geometry correctness on its own.
- **[Ellipse/arrow assumptions may not hold]** → D2/D3 explicitly scope this as "verify live, fix only if needed" rather than extending the rect fix to those kinds by assumption.
- **[`min`/`max` on the wire now differ from the "true" annotation geometry between PaintNodeSpec and OTIO]** → Contained entirely within `_box_shape_spec`/`read_stroke`'s forward/reverse pair; no other code reads RV's raw (expanded) `min`/`max` property expecting the true geometry — confirmed by checking all `min`/`max` property read sites in `rv_paint_applier.py`/`annotation_sync.py` before implementing.
