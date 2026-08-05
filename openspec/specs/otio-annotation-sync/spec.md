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

When converting font sizes between application-specific caption layouts and the `SyncEvent.TextAnnotation` format, the conversion factor SHALL be symmetric to guarantee lossless roundtrip syncing. For the RV host, the factor `RV_FONT_SCALE` (`1080.0` — see "RV Font Size Reads The Authoritative On-Screen Property" below) SHALL be defined in `rv_annotation_codec` (not in the shared `coords` module), and if the text size is scaled by that factor upon export it MUST be unscaled by the same factor upon import.

This requirement covers only a single host's own round-trip self-consistency (RV export then re-import, or xStudio export then re-import, yields the same `font_size`). It does NOT by itself guarantee that RV and xStudio agree with each other on apparent on-screen size for the same `font_size` — see "Cross-Host TextAnnotation Font Size Parity" below.

#### Scenario: Roundtrip font size stability

- **WHEN** a client receives a `TextAnnotation` event and applies it locally, then subsequently exports the same node
- **THEN** the resulting `TextAnnotation.font_size` MUST be exactly equal to the originally received `font_size`.

### Requirement: RV Font Size Reads The Authoritative On-Screen Property

RV's text paint-node carries two size-like properties: `fontSize` (a WCS fraction of image height — the property that actually governs on-screen rendering in the current QPainter-based text renderer) and a legacy `size` (retained only for session-file compatibility with builds predating the QPainter migration; no longer read by any current rendering code). The OTIO sync codec's RV-side conversion (`rv_paint_applier.read_stroke`, `rv_annotation_codec.rv_to_font_size`/`font_size_to_rv`) SHALL read/write `fontSize` as the authoritative value, and SHALL reconstruct the same value via a fallback (`size * 100 * 100 / 1080 * scale`) for sessions/broadcasts that predate `fontSize` — mirroring the fallback the C++ renderer itself uses, so the synced value always matches what is actually on screen regardless of session age.

Using the legacy `size` property directly (without this reconstruction) for any current session/broadcast is a defect: it silently converts a value the renderer itself no longer uses for display, producing a synced size disconnected from what the sending host actually shows.

#### Scenario: Reading a current (fontSize-bearing) text annotation

- **WHEN** an RV paint-node text component has a `fontSize` property set
- **THEN** the sync codec SHALL use that value directly (divided by `scale` to keep the nominal size scale-exclusive) as the basis for the exported `TextAnnotation.font_size`

#### Scenario: Reading a legacy (pre-fontSize) text annotation

- **WHEN** an RV paint-node text component has no `fontSize` property, only a legacy `size`
- **THEN** the sync codec SHALL reconstruct the WCS-fraction value as `size * 100 * 100 / 1080 * scale` before converting to `TextAnnotation.font_size`, rather than treating raw `size` as if it were already a WCS fraction

#### Scenario: Writing a text annotation preserves both properties

- **WHEN** a `TextAnnotation` event is applied to a new RV paint node
- **THEN** the codec SHALL write both the authoritative `fontSize` (the value actually rendered) and a legacy `size` chosen so that an older RV build's own fallback formula would reconstruct the same `fontSize`

### Requirement: Cross-Host TextAnnotation Font Size Parity

The RV and xStudio font-size unit conversions (`RV_FONT_SCALE` in `rv_annotation_codec`, `XS_FONT_SCALE` in `xs_annotation_codec`) SHALL be calibrated so that a `TextAnnotation` of a given `font_size` renders at approximately the same apparent on-screen glyph height on both hosts — not merely round-trip symmetrically within a single host (see "TextAnnotation Font Sizing Symmetry" above, which does not by itself guarantee this), and not merely self-consistent between a test's expected-value formula and the production code if that formula and the code share the same underlying defect (see "RV Font Size Reads The Authoritative On-Screen Property" above — the actual historical failure was exactly this: a numeric round-trip check that derives its expected value from the same wrong property/formula the production code also uses will pass without detecting anything).

Both hosts' native units are anchored to the same reference frame: RV's `fontSize` is a fraction of image height that `RV_FONT_SCALE = 1080` maps to reference-frame pixels; xStudio's own `font_size` is already pixels at a 1920-wide (1080-tall, for 16:9) reference frame. With both sides anchored the same way, `XS_FONT_SCALE = 1.0` — no additional per-host fudge factor is needed or should be introduced without being validated by actually rendering and measuring both hosts' output, not by tuning either constant in isolation.

#### Scenario: OpenRV-drawn text renders at a comparable size in xStudio

- **WHEN** OpenRV draws a native text annotation at a given nominal `fontSize` and it converges to an xStudio peer
- **THEN** the apparent on-screen glyph height xStudio renders for the received `TextAnnotation` SHALL be approximately equal to the glyph height OpenRV itself renders for the same nominal size
- **AND** this SHALL be verified numerically by `sync_test`'s round-trip check (composing OpenRV's reverse text codec, including its `fontSize`/legacy fallback, with xStudio's forward text codec against the real production constants)

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

### Requirement: Authoritative Empty Replace Semantics

A `REPLACE_ANNOTATION_COMMANDS` message whose command list is completely empty for a given annotation clip SHALL be treated by all receivers as an authoritative statement that the clip now has zero annotations, distinct from a non-empty replace that merely omits a kind (e.g. a text-only edit that says nothing about pen strokes, which SHALL continue to leave that kind untouched).

#### Scenario: Empty command list clears the clip everywhere

- **WHEN** the Master peer broadcasts `REPLACE_ANNOTATION_COMMANDS` for an annotation clip with an empty `annotation_commands` list
- **THEN** every receiving peer SHALL end up with zero rendered annotations for that clip's frame
- **AND** the local OTIO state tree's copy of that clip's `annotation_commands` SHALL also become empty

#### Scenario: Non-empty partial replace does not imply other kinds are empty

- **WHEN** a `REPLACE_ANNOTATION_COMMANDS` message contains only text or only shape commands for a clip that also has pen strokes
- **THEN** receivers SHALL NOT interpret the absence of pen-stroke commands in that message as "delete all pen strokes"

