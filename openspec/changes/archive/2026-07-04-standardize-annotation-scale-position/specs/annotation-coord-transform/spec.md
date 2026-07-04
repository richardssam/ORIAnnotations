## ADDED Requirements

### Requirement: Host-Neutral Coordinate Module

The system SHALL provide a single module `otio_sync_core.coords` that is the sole authoritative source for host-neutral, OTIO-normalized annotation geometry. This module SHALL own the aspect-ratio scale, pixel↔OTIO-norm conversions, and shared annotation defaults, and SHALL NOT contain host-specific unit conversions (which belong in each host's codec).

#### Scenario: Geometry is shared across hosts

- **WHEN** the RV codec, the xStudio codec, and `generate_testchart.py` each need to convert between pixel, OTIO-normalized, and host coordinate spaces
- **THEN** they SHALL all import the conversion functions and constants from `otio_sync_core.coords`
- **AND** no coordinate or aspect constant SHALL be defined inline in those call sites

#### Scenario: Host units are excluded from coords

- **WHEN** a value is a host-specific unit conversion (for example RV's `font_size → .size` factor or RV's pen width factor)
- **THEN** it SHALL be defined in that host's codec module, NOT in `otio_sync_core.coords`

### Requirement: Aspect-Half Computation

The `coords.aspect_half(width, height)` function SHALL return `width / (2 * height)`, guarding against a zero or missing height by returning `DEFAULT_ASPECT_HALF`.

#### Scenario: 16:9 media

- **WHEN** `aspect_half(1920, 1080)` is called
- **THEN** it SHALL return `8/9` (≈ 0.8889)

#### Scenario: Missing or zero height

- **WHEN** `aspect_half(1920, 0)` is called
- **THEN** it SHALL return `DEFAULT_ASPECT_HALF` rather than raising

### Requirement: Pixel to OTIO-Norm Conversion

The `coords.px_to_otio` and `coords.otio_to_px` functions SHALL convert between image pixel coordinates and the OTIO-normalized (H-normalized, Y-up, centre-origin) space where `x ∈ [−W/(2H), +W/(2H)]`. The conversion SHALL normalize by image **height**, not width.

#### Scenario: Round-trip is stable

- **WHEN** a pixel coordinate `(px, py)` is passed through `px_to_otio(px, py, W, H)` and then `otio_to_px(x, y, W, H)`
- **THEN** the result SHALL equal the original `(px, py)` within floating-point tolerance

#### Scenario: Centre pixel maps to origin

- **WHEN** `px_to_otio(W/2, H/2, W, H)` is called
- **THEN** it SHALL return `(0.0, 0.0)`

### Requirement: Canonical Annotation Defaults

The `coords` module SHALL define single canonical values for annotation defaults that previously disagreed across the codebase, including `DEFAULT_SPACING = 0.8` (RV-neutral letter spacing) and `DEFAULT_FONT_SIZE`.

#### Scenario: xStudio-originated caption spacing

- **WHEN** an xStudio caption (which has no spacing concept) is converted to a `TextAnnotation`
- **THEN** the emitted spacing SHALL be `coords.DEFAULT_SPACING` (0.8), not `0.0`

### Requirement: Shared Shape Tessellation

The system SHALL provide shared, host-neutral shape tessellation (`otio_sync_core.shapes`, or a `coords` submodule) that converts ellipse, rectangle, and arrow annotations into OTIO-normalized point polylines. This geometry SHALL NOT be duplicated inside any single host's codec, so that a host lacking native shape rendering can degrade gracefully by reusing it.

#### Scenario: Ellipse tessellation is host-neutral

- **WHEN** any codec whose native capabilities exclude shapes needs to render an `EllipseAnnotation`
- **THEN** it SHALL obtain the polyline points from the shared tessellation helper
- **AND** the helper SHALL return points in OTIO-normalized space, independent of any host coordinate convention
