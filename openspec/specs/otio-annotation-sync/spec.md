## ADDED Requirements

### Requirement: OTIO Annotation State Storage

The system SHALL store annotation data in the OTIO state tree using the `SyncEvent` flat schema (e.g. `PaintStart`, `PaintPoints`, `TextAnnotation`) to represent strokes.

#### Scenario: Appending new strokes

- **WHEN** an annotation is created and broadcast to the session
- **THEN** the Master peer SHALL append the corresponding flat view representation of the stroke to the OTIO state tree.

#### Scenario: Late joiner annotation sync

- **WHEN** a new client joins the session and requests the state snapshot
- **THEN** the snapshot SHALL include all previously stored annotations in the flat view schema
- **AND** the joining client SHALL apply these annotations locally.

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

### Requirement: TextAnnotation Font Sizing Symmetry

When converting font sizes between application-specific caption layouts and the `SyncEvent.TextAnnotation` format, the conversion factor SHALL be symmetric to guarantee lossless roundtrip syncing. Specifically, if the text size is scaled by a factor of 5000.0 upon export, it MUST be unscaled by a factor of 5000.0 upon import.

#### Scenario: Roundtrip font size stability

- **WHEN** a client receives a `TextAnnotation` event and applies it locally, then subsequently exports the same node
- **THEN** the resulting `TextAnnotation.font_size` MUST be exactly equal to the originally received `font_size`.

### Requirement: TextAnnotation UUID Persistence

When converting `SyncEvent.TextAnnotation` commands to a client-native format (e.g., xStudio caption dictionaries), the unique identifier (`uuid`) MUST be explicitly carried over into the native structure. This guarantees that subsequent modification broadcasts can correctly merge against the original node.

#### Scenario: Replacing an existing caption

- **WHEN** a client receives a `broadcast_replace_annotation_commands` payload containing edited text
- **THEN** it SHALL use the text node's `uuid` to find and update the existing native caption in-place, rather than appending a duplicate copy.
