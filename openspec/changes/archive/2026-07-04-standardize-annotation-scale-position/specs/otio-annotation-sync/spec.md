## MODIFIED Requirements

### Requirement: xstudio Stroke Coordinate Mapping

When converting xstudio annotation data to SyncEvent types, the coordinate system SHALL be transformed by the aspect-ratio scale `aspect_half = W / (2 * H)`. xstudio stores W-normalized coordinates with `(0,0)` at image centre, `x ∈ [−1, +1]`, and Y increasing downward; the OTIO/RV convention is H-normalized with `x ∈ [−W/(2H), +W/(2H)]` and Y increasing upward. The conversion SHALL therefore be `x_otio = x_xs * aspect_half` and `y_otio = −y_xs * aspect_half`, using `coords.aspect_half(W, H)` derived from the target media resolution. The prior statement that "no transformation is applied" was incorrect.

#### Scenario: Stroke coordinates are aspect-scaled

- **WHEN** an xstudio pen stroke point `(x_xs, y_xs)` is converted to a `PaintVertices` entry for media of width `W` and height `H`
- **THEN** `PaintVertices.x` SHALL equal `x_xs * aspect_half` and `PaintVertices.y` SHALL equal `−y_xs * aspect_half`, where `aspect_half = coords.aspect_half(W, H)`

#### Scenario: Inverse transform on import

- **WHEN** a `PaintVertices` entry `(x_otio, y_otio)` is converted back to xstudio coordinates
- **THEN** the result SHALL be `x_xs = x_otio / aspect_half` and `y_xs = −y_otio / aspect_half`

### Requirement: TextAnnotation Font Sizing Symmetry

When converting font sizes between application-specific caption layouts and the `SyncEvent.TextAnnotation` format, the conversion factor SHALL be symmetric to guarantee lossless roundtrip syncing. For the RV host, the factor `RV_FONT_SCALE = 5000.0` SHALL be defined in `rv_annotation_codec` (not in the shared `coords` module), and if the text size is scaled by that factor upon export it MUST be unscaled by the same factor upon import.

#### Scenario: Roundtrip font size stability

- **WHEN** a client receives a `TextAnnotation` event and applies it locally, then subsequently exports the same node
- **THEN** the resulting `TextAnnotation.font_size` MUST be exactly equal to the originally received `font_size`.

## ADDED Requirements

### Requirement: TextAnnotation Scale Round-Trip

The `TextAnnotation.scale` field SHALL round-trip on hosts that have a native scale concept, and SHALL default to `1.0` on hosts that do not. For RV (which has a text-node `scale` property) `scale` MUST survive the OTIO→RV→OTIO round-trip unchanged. For xStudio (which has no per-caption scale field) emitting `scale = 1.0` on export is correct and the field MAY be dropped on import.

#### Scenario: RV preserves scale

- **WHEN** a `TextAnnotation` with `scale = 1.5` is rendered to an RV text node and later read back
- **THEN** the exported `TextAnnotation.scale` MUST equal `1.5`

#### Scenario: xStudio defaults scale to 1.0

- **WHEN** an xStudio caption is converted to a `TextAnnotation`
- **THEN** the emitted `TextAnnotation.scale` SHALL be `1.0`
- **AND** no user-facing behavior change SHALL result from this on the xStudio→OTIO direction
