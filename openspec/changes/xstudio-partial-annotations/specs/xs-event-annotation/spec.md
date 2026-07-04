## MODIFIED Requirements

### Requirement: AnnotationsCore event subscription

The xStudio plugin SHALL subscribe to AnnotationsCore's `plugin_events_` broadcast group at session connect time. The plugin SHALL consume the geometry-bearing 5-tuple `(event_atom, annotation_data_atom, JsonStore, user_id, stroke_completed)` as the primary shape, and SHALL tolerate the legacy geometry-less 4-tuple `(event_atom, annotation_data_atom, user_id, stroke_completed)` from older builds. Shape discrimination SHALL be by tuple length, not by inspecting the type of the third element.

#### Scenario: Subscription succeeds
- **WHEN** the plugin connects to a session
- **THEN** the plugin SHALL log "Subscribed to AnnotationsCore plugin events [2C]"
- **AND** SHALL receive PaintStart, PaintPoint, and PaintEnd events during drawing

#### Scenario: New 5-tuple with geometry
- **WHEN** a mid-stroke event arrives from a build carrying serialized geometry
- **THEN** the plugin SHALL read the `JsonStore` from `data[2]`
- **AND** SHALL take the direct live-stroke broadcast path using that geometry

#### Scenario: Legacy 4-tuple without geometry
- **WHEN** a mid-stroke event arrives from a build that sends no geometry
- **THEN** the plugin SHALL detect the shorter tuple length
- **AND** SHALL degrade to pen-up-only broadcasting (final stroke on flush), without a per-tick bookmark hot-scan

#### Scenario: Subscription fails gracefully
- **WHEN** `get_plugin("AnnotationsCore")` raises or returns nothing
- **THEN** the plugin SHALL log the exception and continue without the subscription
- **AND** the 30-second fallback scan path SHALL remain active as the only safety net

---

## ADDED Requirements

### Requirement: PaintPoint triggers direct live-stroke broadcast

When a `stroke_completed=False` event carrying a `JsonStore` is received, the xStudio plugin SHALL broadcast the in-progress stroke to peers directly from the event geometry via `broadcast_live_stroke_from_json`, without reading or scanning any bookmark. The plugin SHALL assign a stable UUID to the gesture so that each successive partial replaces the prior one in place on the receiver.

#### Scenario: Mid-stroke partial broadcast from event JSON
- **WHEN** the user is drawing and PaintPoint events with geometry arrive
- **THEN** the plugin SHALL emit a partial annotation built from `Data.pen_strokes` in the event
- **AND** SHALL NOT read `bookmark.annotation_data` to obtain the geometry

#### Scenario: Stable UUID across a gesture
- **WHEN** successive PaintPoints for one gesture are broadcast
- **THEN** each partial SHALL carry the same stroke UUID
- **AND** the receiver SHALL update the existing partial in place rather than accumulate duplicates

#### Scenario: Pen-up reuses the gesture UUID
- **WHEN** PaintEnd fires and the final committed stroke is flushed
- **THEN** the flushed stroke SHALL reuse the gesture's UUID so it supersedes the last partial without duplication

---

### Requirement: No per-tick bookmark polling during drawing

The plugin SHALL NOT perform per-poll-tick bookmark scanning to obtain in-progress stroke geometry. Partial-stroke delivery SHALL be driven entirely by AnnotationsCore geometry events, not by a hot-scan loop.

#### Scenario: No hot-scan during an active gesture
- **WHEN** the user draws a multi-point stroke
- **THEN** the plugin SHALL NOT iterate the session bookmark list on each poll tick to find in-progress geometry
- **AND** partial broadcasts SHALL originate from the event JSON path only

#### Scenario: Idle cost unchanged by drawing
- **WHEN** drawing is active versus idle
- **THEN** the poll loop SHALL NOT add per-tick bookmark enumeration work attributable to partial-stroke detection

---

## REMOVED Requirements

### Requirement: PaintPoint triggers immediate hot-scan

**Reason**: The hot-scan path read in-progress geometry from a committed bookmark, but no bookmark exists mid-stroke, so partials never rendered. It is replaced by the direct live-stroke JSON broadcast, which also eliminates per-tick bookmark polling during drawing.

**Migration**: Partial strokes are now broadcast via `broadcast_live_stroke_from_json` using the `JsonStore` geometry carried in the AnnotationsCore 5-tuple. Builds that still send the geometry-less 4-tuple degrade to pen-up-only broadcasting rather than falling back to hot-scan.
