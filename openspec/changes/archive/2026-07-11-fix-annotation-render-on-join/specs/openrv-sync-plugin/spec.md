## MODIFIED Requirements

### Requirement: Synchronized Annotations
The plugin SHALL synchronize paint strokes between instances by intercepting RV drawing events, translating them into the flat view `SyncEvent` format, and broadcasting them. Upon receiving flat view annotations, the plugin SHALL apply them back to the RV property graph such that the annotation is actually rendered by RV, not merely present as unread node properties.

The plugin SHALL additionally bind RV's internal `clear-paint` and `clear-all-paint` events (in addition to the existing `graph-state-change` binding) so that local annotation deletion is detected and broadcast, and SHALL bind changes to `<node>.paint.show` so that toggling annotation visibility is detected and broadcast.

#### Scenario: Translating stroke to flat view
- **WHEN** a user completes a paint stroke in RV
- **THEN** the plugin SHALL extract the stroke properties and broadcast them as a flat view annotation payload.

#### Scenario: Applying flat view stroke
- **WHEN** the plugin receives a flat view annotation payload or snapshot
- **THEN** it SHALL translate the flat data back into OpenRV's node-based property graph
- **AND** the written properties SHALL match the property set and per-frame key convention RV's own native annotate tool uses for that annotation kind, so the stroke is actually displayed rather than silently absent from the render
- **AND** this SHALL hold identically whether the stroke arrives via a live per-event broadcast, a delta insert, or a full state-snapshot replay on join

#### Scenario: Applied strokes key their RV frame bucket and startFrame by RV's native per-source frame
- **WHEN** the plugin resolves the target `RVPaint` node for an incoming annotation via `metaEvaluateClosestByType`
- **THEN** it SHALL use the frame number that call reports for that node — not a sequence-position or clip-local frame number the plugin computed independently — as both the paint node's `frame:<N>` bucket key and the written stroke's `startFrame`
- **AND** any internal bookkeeping keyed by "the frame this annotation occupies" (e.g. mid-gesture partial-stroke tracking) SHALL use that same reported frame number, so it stays consistent with the actual RV property location

#### Scenario: Immediate text annotation broadcast
- **WHEN** a user types or modifies a text annotation in OpenRV
- **THEN** the plugin SHALL immediately reconstruct the frame's annotation state and broadcast it using `REPLACE_ANNOTATION_COMMANDS`
- **AND** the plugin SHALL NOT buffer text annotations in the pending stroke queue.

#### Scenario: Clear Frame is detected and broadcast
- **WHEN** the user chooses "Clear Frame" in RV's Annotate mode, firing the `clear-paint` internal event
- **THEN** the plugin SHALL identify the affected annotation clip and broadcast its surviving (possibly empty) commands via `REPLACE_ANNOTATION_COMMANDS`

#### Scenario: Clear All Frames on Timeline is detected and broadcast
- **WHEN** the user chooses "Clear All Frames on Timeline" in RV's Annotate mode, firing the `clear-all-paint` internal event
- **THEN** the plugin SHALL identify every affected annotation clip and broadcast each one's surviving (possibly empty) commands via `REPLACE_ANNOTATION_COMMANDS`

#### Scenario: Show Drawings toggle is detected and broadcast
- **WHEN** the user toggles "Show Drawings" for an RV source, changing `<node>.paint.show`
- **THEN** the plugin SHALL broadcast the new value as `annotations_visible` via `display_settings`
