## ADDED Requirements

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
