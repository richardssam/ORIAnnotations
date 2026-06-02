## ADDED Requirements

### Requirement: xstudio Stroke Coordinate Mapping
When converting xstudio annotation data to SyncEvent types, the coordinate system SHALL be treated as directly compatible — xstudio stores normalized coords with `(0,0)` at image center and `±0.5` spanning half the image width, which matches the RV annotation coordinate convention. No transformation is applied.

#### Scenario: Stroke coordinates pass through unchanged
- **WHEN** an xstudio pen stroke point `(x, y)` is converted to a `PaintVertices` entry
- **THEN** the x and y values SHALL be written to `PaintVertices.x` and `PaintVertices.y` without modification

### Requirement: xstudio Per-Point Width Computation
When converting xstudio pen stroke width data, the per-point width SHALL be computed from the stroke's `thickness` scalar and per-point `size_pressure` value.

#### Scenario: Pressure-sensitive stroke
- **WHEN** a stroke's point array contains non-zero `size_pressure` values (every 4th element starting at index 2)
- **THEN** each entry in `PaintVertices.size` SHALL equal `thickness * size_pressure` for that point

#### Scenario: Flat-width stroke
- **WHEN** all `size_pressure` values in a stroke are zero
- **THEN** each entry in `PaintVertices.size` SHALL equal `thickness`
