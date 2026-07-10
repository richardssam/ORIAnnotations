# otio-annotation-sync

## Purpose

Specification for synchronizing review annotations (drawings and text annotations) in real-time over RabbitMQ and preserving them in OpenTimelineIO timelines.
## Requirements
### Requirement: OTIO Annotation State Storage

The system SHALL store annotation data in the OTIO state tree using the `SyncEvent` flat schema (e.g. `PaintStart`, `PaintPoints`, `TextAnnotation`) to represent strokes.

Annotation strokes SHALL reach remote peers within one hot-scan interval (~33 ms) of pen-up for both new bookmarks and additional strokes on existing bookmarks. The previous 1-second fallback scan latency for existing-bookmark strokes is no longer acceptable.

#### Scenario: Appending new strokes

- **WHEN** an annotation is created and broadcast to the session
- **THEN** the Master peer SHALL append the corresponding flat view representation of the stroke to the OTIO state tree.

#### Scenario: Late joiner annotation sync

- **WHEN** a new client joins the session and requests the state snapshot
- **THEN** the snapshot SHALL include all previously stored annotations in the flat view schema
- **AND** the joining client SHALL apply these annotations locally.

#### Scenario: Second stroke on existing bookmark latency

- **WHEN** the user draws a second stroke on a frame that already has an annotation
- **THEN** the stroke SHALL reach a remote peer within 250 ms of pen-up (debounce + one hot-scan cycle)
- **AND** SHALL NOT require waiting for the 1-second fallback scan

### Requirement: xstudio Stroke Coordinate Mapping

When converting xstudio annotation data to SyncEvent types, the coordinate system SHALL be transformed by the aspect-ratio scale `aspect_half = W / (2 * H)`. xstudio stores W-normalized coordinates with `(0,0)` at image centre, `x ∈ [−1, +1]`, and Y increasing downward; the OTIO/RV convention is H-normalized with `x ∈ [−W/(2H), +W/(2H)]` and Y increasing upward. The conversion SHALL therefore be `x_otio = x_xs * aspect_half` and `y_otio = −y_xs * aspect_half`, using `coords.aspect_half(W, H)` derived from the target media resolution. The prior statement that "no transformation is applied" was incorrect.

#### Scenario: Stroke coordinates are aspect-scaled

- **WHEN** an xstudio pen stroke point `(x_xs, y_xs)` is converted to a `PaintVertices` entry for media of width `W` and height `H`
- **THEN** `PaintVertices.x` SHALL equal `x_xs * aspect_half` and `PaintVertices.y` SHALL equal `−y_xs * aspect_half`, where `aspect_half = coords.aspect_half(W, H)`

#### Scenario: Inverse transform on import

- **WHEN** a `PaintVertices` entry `(x_otio, y_otio)` is converted back to xstudio coordinates
- **THEN** the result SHALL be `x_xs = x_otio / aspect_half` and `y_xs = −y_otio / aspect_half`

### Requirement: xstudio Per-Point Width Computation

When converting xstudio pen stroke width data, the per-point width SHALL be computed from the stroke's `thickness` scalar and per-point `size_pressure` value.

#### Scenario: Pressure-sensitive stroke

- **WHEN** a stroke's point array contains non-zero `size_pressure` values (every 4th element starting at index 2)
- **THEN** each entry in `PaintVertices.size` SHALL equal `thickness * size_pressure` for that point

#### Scenario: Flat-width stroke

- **WHEN** all `size_pressure` values in a stroke are zero
- **THEN** each entry in `PaintVertices.size` SHALL equal `thickness`

### Requirement: TextAnnotation Font Sizing Symmetry

When converting font sizes between application-specific caption layouts and the `SyncEvent.TextAnnotation` format, the conversion factor SHALL be symmetric to guarantee lossless roundtrip syncing. For the RV host, the factor `RV_FONT_SCALE = 5000.0` SHALL be defined in `rv_annotation_codec` (not in the shared `coords` module), and if the text size is scaled by that factor upon export it MUST be unscaled by the same factor upon import.

#### Scenario: Roundtrip font size stability

- **WHEN** a client receives a `TextAnnotation` event and applies it locally, then subsequently exports the same node
- **THEN** the resulting `TextAnnotation.font_size` MUST be exactly equal to the originally received `font_size`.

### Requirement: TextAnnotation Scale Round-Trip

The `TextAnnotation.scale` field SHALL round-trip on hosts that have a native scale concept, and SHALL default to `1.0` on hosts that do not. For RV (which has a text-node `scale` property) `scale` MUST survive the OTIO→RV→OTIO round-trip unchanged. For xStudio (which has no per-caption scale field) emitting `scale = 1.0` on export is correct and the field MAY be dropped on import.

#### Scenario: RV preserves scale

- **WHEN** a `TextAnnotation` with `scale = 1.5` is rendered to an RV text node and later read back
- **THEN** the exported `TextAnnotation.scale` MUST equal `1.5`

#### Scenario: xStudio defaults scale to 1.0

- **WHEN** an xStudio caption is converted to a `TextAnnotation`
- **THEN** the emitted `TextAnnotation.scale` SHALL be `1.0`
- **AND** no user-facing behavior change SHALL result from this on the xStudio→OTIO direction

### Requirement: TextAnnotation UUID Persistence

When converting `SyncEvent.TextAnnotation` commands to a client-native format (e.g., xStudio caption dictionaries), the unique identifier (`uuid`) MUST be explicitly carried over into the native structure. This guarantees that subsequent modification broadcasts can correctly merge against the original node.

#### Scenario: Replacing an existing caption

- **WHEN** a client receives a `broadcast_replace_annotation_commands` payload containing edited text
- **THEN** it SHALL use the text node's `uuid` to find and update the existing native caption in-place, rather than appending a duplicate copy.

### Requirement: Rectangle Annotation Schema

The system SHALL support storing rectangle annotations inside the `SyncEvent` flat schema. The rectangle annotation schema MUST include parameters for bounding box top-left corner coordinate `min` `[x, y]`, bottom-right corner coordinate `max` `[x, y]`, outline color `rgba` `[r, g, b, a]`, outline thickness `size`, fill color `inner_rgba` `[r, g, b, a]` (where alpha > 0.0 indicates a filled shape), unique identifier `uuid`, and creation timestamp `timestamp`.

#### Scenario: Serializing a rectangle
- **WHEN** a `RectangleAnnotation` object is instantiated with `min=[-0.2, 0.2]`, `max=[0.2, -0.1]`, `rgba=[1.0, 0.0, 0.0, 1.0]`, `size=2.0`, `inner_rgba=[0.0, 1.0, 0.0, 0.5]`
- **THEN** it SHALL successfully serialize to an OpenTimelineIO JSON representation containing those exact fields.

### Requirement: Ellipse Annotation Schema

The system SHALL support storing ellipse annotations inside the `SyncEvent` flat schema. The ellipse annotation schema MUST include parameters for bounding box top-left corner coordinate `min` `[x, y]`, bottom-right corner coordinate `max` `[x, y]`, outline color `rgba` `[r, g, b, a]`, outline thickness `size`, fill color `inner_rgba` `[r, g, b, a]` (where alpha > 0.0 indicates a filled shape), unique identifier `uuid`, and creation timestamp `timestamp`.

#### Scenario: Serializing an ellipse
- **WHEN** an `EllipseAnnotation` object is instantiated with `min=[-0.15, 0.05]`, `max=[0.35, -0.25]`, `rgba=[0.0, 0.0, 1.0, 1.0]`, `size=1.5`, `inner_rgba=[1.0, 1.0, 0.0, 0.8]`
- **THEN** it SHALL successfully serialize to an OpenTimelineIO JSON representation containing those exact fields.

### Requirement: Arrow Annotation Schema

The system SHALL support storing arrow annotations inside the `SyncEvent` flat schema. The arrow annotation schema MUST include parameters for start coordinate `start` `[x, y]`, end coordinate `end` `[x, y]`, line color `rgba` `[r, g, b, a]`, line thickness `size`, unique identifier `uuid`, and creation timestamp `timestamp`.

#### Scenario: Serializing an arrow
- **WHEN** an `ArrowAnnotation` object is instantiated with `start=[-0.3, -0.3]`, `end=[0.3, 0.3]`, `rgba=[1.0, 1.0, 1.0, 1.0]`, `size=3.0`
- **THEN** it SHALL successfully serialize to an OpenTimelineIO JSON representation containing those exact fields.

### Requirement: Vector Primitives Test Chart

The test chart tool `generate_testchart.py` SHALL output a new background image named `vector_primitives.png` (and its UHD version) that visualizes reference shapes for rectangles, ellipses, and arrows. The exported `testchart_annotations.otio` SHALL include a review item frame for this test chart containing corresponding `RectangleAnnotation`, `EllipseAnnotation`, and `ArrowAnnotation` objects aligned with the reference drawing.

#### Scenario: Test chart contains shape primitive annotations
- **WHEN** the test chart generation tool `generate_testchart.py` is executed
- **THEN** it SHALL generate `vector_primitives.png` and `vector_primitives_uhd.png`
- **AND** the exported `testchart_annotations.otio` timeline SHALL contain a review item for these images containing `RectangleAnnotation`, `EllipseAnnotation`, and `ArrowAnnotation` commands.

### Requirement: xStudio Bookmark Placement Is Floor-Safe

xStudio derives a bookmark's integer frame from its stored `start` time via `FrameRateDuration::frame(flicks)`, which is `static_cast<int>(std::floor(flicks / rate_.to_flicks()))` — it floors and never rounds. When converting a requested clip-local frame number into `BookmarkDetail.start`, the sync codec SHALL request a time strictly inside the target frame's window (`[frame/fps, (frame+1)/fps)`) with enough margin to absorb `datetime.timedelta`'s truncation to microsecond resolution, rather than the frame's exact leading edge (`frame/fps`), which has no such margin and floors down to `frame - 1` for almost any frame that is not an exact multiple of the fps.

#### Scenario: Placing a bookmark on a non-multiple-of-fps frame

- **WHEN** the sync codec places a bookmark for a received annotation whose clip-local frame is not an exact multiple of the media's fps (e.g. frame 29 or 41 at 24fps)
- **THEN** reading the frame back out of the resulting bookmark (via xStudio's own floor-based frame derivation) SHALL yield exactly the requested frame, not `frame - 1`

#### Scenario: Placing a bookmark on frame 0 or another exact multiple of fps

- **WHEN** the sync codec places a bookmark for a received annotation whose clip-local frame is an exact multiple of the media's fps (e.g. frame 0, 24, or 48 at 24fps)
- **THEN** reading the frame back out of the resulting bookmark SHALL still yield exactly the requested frame

