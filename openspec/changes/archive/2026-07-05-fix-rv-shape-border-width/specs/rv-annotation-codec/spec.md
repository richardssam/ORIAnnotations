## ADDED Requirements

### Requirement: Shape Border Width Renders as a Centered Line Width

The codec SHALL make an OTIO shape annotation's `size` produce a visually centered stroke in OpenRV for rect and ellipse kinds — matching xStudio's centered tessellated-stroke rendering for the same `size` — rather than the inward-only stroke OpenRV's native box-border renderer produces from an unadjusted bounding box. `_box_shape_spec` SHALL expand the written `min`/`max` outward by `size / 2` on every edge and write `borderWidth = size` directly; `rv_paint_applier.read_stroke`'s rect/ellipse branch SHALL contract the read-back `min`/`max` inward by `border_width / 2` and read `size = border_width` directly, as the exact inverse.

#### Scenario: A rectangle's rendered border matches xStudio's centered stroke width
- **WHEN** a `RectangleAnnotation` with a given `size` is rendered to an OpenRV paint node via `_box_shape_spec`
- **THEN** the written `min`/`max` SHALL be expanded outward by `size / 2` on every edge and `borderWidth` SHALL equal `size`, so OpenRV's inward-only border rendering spans the same `[boundary − size/2, boundary + size/2]` region xStudio's centered tessellated stroke would for the same `size`

#### Scenario: Rectangle geometry and size round-trip through OpenRV unchanged
- **WHEN** a `RectangleAnnotation` is rendered to an OpenRV paint node and then read back via `rv_paint_applier.read_stroke` and `rv_strokes_to_sync_events`
- **THEN** the resulting `RectangleAnnotation`'s `min`, `max`, and `size` SHALL equal the original values

#### Scenario: Ellipse geometry and size round-trip through OpenRV unchanged
- **WHEN** an `EllipseAnnotation` is rendered to an OpenRV paint node and then read back via `rv_paint_applier.read_stroke` and `rv_strokes_to_sync_events`
- **THEN** the resulting `EllipseAnnotation`'s `min`, `max`, and `size` SHALL equal the original values
