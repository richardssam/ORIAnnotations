## MODIFIED Requirements

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
