# rv-annotation-codec

## Purpose

The pure SyncEvent ⇄ RV-paint-node structure codec (`otio_sync_core.rv_annotation_codec` + `otio_sync_core.rv_paint_applier`): the `PaintNodeSpec` intermediate representation plus a thin `apply_specs` edge. Owns which properties, node-name conventions, per-frame `order` lists, shape geometry, gauss/splat, and width for pen · erase · text · ellipse · rect · arrow. Shared by the testchart batch renderer, both OTIO load-plugin directions, and the live-sync renderer — the sole authoritative implementation of this mapping, replacing four previously-duplicated inline copies.

## Requirements

### Requirement: Single RV Annotation Codec

The system SHALL provide a single module `otio_sync_core.rv_annotation_codec` that is the sole authoritative implementation of the OTIO `SyncEvent` ⇄ RV paint-node mapping. All RV code that renders SyncEvents to paint nodes, or reads paint nodes back to SyncEvents, SHALL route through this module and SHALL NOT set or read RV paint-node properties for annotations directly.

#### Scenario: All RV call sites use the codec

- **WHEN** the testchart batch helper, the OTIO load plugin (import and export), or the live-sync renderer renders or parses annotations
- **THEN** each SHALL call the codec's conversion functions
- **AND** no annotation paint-node property SHALL be constructed inline at those call sites

#### Scenario: RV units owned by the codec

- **WHEN** a value is an RV-specific unit conversion (`RV_FONT_SCALE = 5000.0`, `RV_WIDTH_SCALE = 0.6`, `font_size_to_rv`, `rv_to_font_size`)
- **THEN** it SHALL be defined in `rv_annotation_codec`, mirroring how the xStudio codec owns xStudio's font factor

### Requirement: PaintNodeSpec Intermediate Representation

The codec function `sync_events_to_rv_specs(events, ctx)` SHALL be a pure function that returns an ordered list of `PaintNodeSpec` dictionaries and SHALL NOT import or call `rv.commands`. Each `PaintNodeSpec` SHALL carry a `kind`, a stable `uuid`, and an ordered list of `props` (each `(name, rv_type, value, dim)`), fully describing one RV paint child node independent of the target paint node or strokeid.

#### Scenario: Pure conversion is testable outside RV

- **WHEN** `sync_events_to_rv_specs` is called with a list of SyncEvents in an environment where `rv.commands` is unavailable
- **THEN** it SHALL return the `PaintNodeSpec` list without error
- **AND** the result SHALL be assertable in a unit test without launching RV

#### Scenario: Property superset per kind

- **WHEN** a spec is produced for a given kind
- **THEN** its `props` SHALL include every property RV's own native annotate tool writes for a component of that kind: pen/erase (`brush, color, debug, join, cap, splat, mode` always present (`0` normal / `1` erase), `width, points, startFrame, duration, softDeleted`), text (`position, color, spacing, size, font, text, scale, rotation, origin, debug, startFrame, duration, mode, uuid, softDeleted`), ellipse/rect (`min, max, borderColor, innerColor, borderWidth, startFrame, duration, eye, uuid, softDeleted`), arrow (`startPos, endPos, borderColor, innerColor, borderWidth, thickness, startFrame, duration, eye, uuid, softDeleted`)

#### Scenario: Pen spec receives the target frame like every other kind

- **WHEN** `sync_events_to_rv_specs` dispatches a pen/erase stroke
- **THEN** it SHALL pass `ctx["frame"]` into the pen spec builder exactly as it already does for text, ellipse/rect, and arrow
- **AND** the resulting spec's `startFrame` property SHALL equal that frame value, so a pen stroke's active-interval metadata is present under the same conditions the other three kinds already guarantee

### Requirement: Schema-Name Event Dispatch

The codec SHALL dispatch on `event.schema_name()` when classifying SyncEvents, and SHALL NOT use `isinstance(event, otio.schemadef.SyncEvent.X)`, because `isinstance` silently returns `False` when the SyncEvent schemadef is registered more than once.

#### Scenario: Double-loaded schemadef still classifies

- **WHEN** the SyncEvent schemadef has been loaded twice and a `PaintStart` event is passed to the codec
- **THEN** the codec SHALL classify it as a paint-start via `schema_name() == "PaintStart"`
- **AND** the annotation SHALL NOT be silently dropped

### Requirement: Thin RV Applier With Append and Reconcile Modes

The codec SHALL provide the only RV-touching function, `apply_specs(specs, commands, *, rv_node, frame, mode, start_id=None)`, which writes `PaintNodeSpec` entries (each carrying its own `user` field) to RV paint nodes and maintains the per-frame `order` list. It SHALL support `mode="append"` (create fresh nodes and append to order) and `mode="reconcile"` (match existing nodes by `uuid`, update in place when found, add when not, and prune managed nodes whose uuid is absent from the incoming set).

Reconcile mode's kind-inferring prune (deriving which kinds it may prune from the kinds actually present in `specs`) SHALL remain unchanged for a non-empty `specs` list — callers routinely reconcile one kind (or even a single item) at a time, and a spec list that says nothing about a kind MUST NOT be read as "no items of that kind exist anymore." Annotation deletion that empties a frame entirely SHALL NOT be expressed by calling `apply_specs` with an empty `specs` list (which reconcile mode cannot distinguish from "no opinion, prune nothing"); callers needing a full clear SHALL clear the frame's `order` property directly instead, outside `apply_specs`.

#### Scenario: Append mode adds nodes

- **WHEN** `apply_specs` is called with `mode="append"` for a frame that has no existing managed nodes
- **THEN** it SHALL create the paint child nodes and set the frame `order` to reference them

#### Scenario: Reconcile updates in place by uuid

- **WHEN** `apply_specs` is called with `mode="reconcile"` and a spec whose `uuid` matches an existing node in the frame order
- **THEN** it SHALL update that node's properties in place rather than appending a duplicate

#### Scenario: Reconcile prunes deleted annotations

- **WHEN** `apply_specs` is called with `mode="reconcile"` and an existing managed node's `uuid` is not present among the incoming specs
- **THEN** that node SHALL be removed from the frame `order`

#### Scenario: Reconcile with an empty specs list prunes nothing

- **WHEN** `apply_specs` is called with `mode="reconcile"` and `specs` is an empty list
- **THEN** no existing managed node SHALL be removed from the frame `order`
- **AND** a caller needing to fully clear the frame MUST NOT rely on this call to do so

### Requirement: RV Round-Trip Preserves Scale

The codec SHALL preserve the text annotation `scale` field on the RV round-trip: `rv_strokes_to_sync_events` SHALL read the RV text node's `scale` property (as read via `rv_paint_applier.read_stroke`/`read_frame_strokes`) into `TextAnnotation.scale`, and `sync_events_to_rv_specs` SHALL write `TextAnnotation.scale` back to the node.

#### Scenario: Scale survives read-back

- **WHEN** a `TextAnnotation` with `scale = 1.5` is rendered to an RV text node and then read back via `rv_paint_applier.read_stroke` + `rv_strokes_to_sync_events`
- **THEN** the resulting `TextAnnotation.scale` SHALL equal `1.5`

### Requirement: Common Codec Contract for Multi-Host Extensibility

The codec SHALL expose a uniform, host-agnostic surface so that host-agnostic tooling works without special-casing and future host codecs (e.g. Nuke Studio) can be added as a new spoke without editing existing codecs. Each host codec SHALL declare `HOST_ID` and `SUPPORTED_KINDS`, and SHALL provide `to_sync_events(native, ctx)` and `from_sync_events(events, ctx)`; imperative-write hosts SHALL additionally provide `apply(...)`, while single-handoff hosts MAY omit it.

#### Scenario: Codec declares its identity and capabilities

- **WHEN** `rv_annotation_codec` is inspected
- **THEN** it SHALL expose `HOST_ID == "rv"` and a `SUPPORTED_KINDS` set enumerating the SyncEvent kinds it renders natively

#### Scenario: Unsupported kinds degrade via shared tessellation

- **WHEN** a codec's `from_sync_events` receives a SyncEvent kind not in its `SUPPORTED_KINDS`
- **THEN** it SHALL route that event through the shared shape tessellation fallback rather than failing

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
