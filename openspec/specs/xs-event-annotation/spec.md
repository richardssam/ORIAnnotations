# xs-event-annotation

## Purpose

Defines how the xStudio sync plugin uses AnnotationsCore plugin events to drive immediate annotation detection and broadcast, replacing the previous polling-only approach with an event-driven path. Mid-stroke partials broadcast directly from event-carried geometry (no bookmark read or per-tick scan); pen-up flushes the committed stroke immediately. A 30-second fallback scan remains as a safety net only.

---

## Requirements

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

### Requirement: PaintEnd triggers immediate annotation flush

The xStudio plugin SHALL schedule an annotation flush (set `_annotation_pending_time`) when a `stroke_completed=True` event is received from AnnotationsCore, without waiting for the fallback scan interval.

#### Scenario: Pen-up on new bookmark
- **WHEN** the user lifts the pen on a frame with no prior annotation
- **THEN** `stroke_completed=True` SHALL fire within one PaintEnd event
- **AND** the flush SHALL be scheduled within that same event handler call

#### Scenario: Pen-up on existing bookmark (second stroke)
- **WHEN** the user lifts the pen on a frame that already has a remote or local annotation
- **THEN** `stroke_completed=True` SHALL fire for the new stroke
- **AND** the flush SHALL be scheduled — not deferred to the next fallback scan cycle

---

### Requirement: AnnotationsCore event observability

The xStudio plugin SHALL count received AnnotationsCore events and log the first one per session, so operators can confirm the subscription is live from the log file.

#### Scenario: First event log
- **WHEN** the first AnnotationsCore event arrives after connect
- **THEN** the plugin SHALL log "[2C] First AnnotationsCore event received"

---

### Requirement: Fallback scan is a safety net, not a primary path

The fallback scan interval (`ANNOTATION_SCAN_INTERVAL`) SHALL be at least 30 seconds. The fallback scan SHALL NOT be the primary detection path for annotation completions.

#### Scenario: Fallback scan rate
- **WHEN** no annotation events have fired for 30 seconds
- **THEN** the plugin SHALL perform one full bookmark scan as a safety net
- **AND** the scan rate SHALL NOT approach the previous 1-second rate during normal drawing

#### Scenario: Fallback does not regress when events are firing
- **WHEN** AnnotationsCore events are being received normally
- **THEN** the fallback scan MUST NOT trigger between strokes, only after 30 s of inactivity

### Requirement: AnnotationsUI event type discrimination

The xStudio plugin's `on_annotation_event` handler SHALL inspect the `data["event"]` string of each `(event_atom, annotation_atom, JsonStore)` payload received from the `AnnotationsUI` plugin, rather than treating every such payload identically as a generic "schedule a bookmark scan" trigger. `PaintClear`, `HideDrawings`, and `ShowDrawings` SHALL each be handled according to their own requirements (deletion detection, visibility broadcast) in addition to — or instead of — the existing generic scan scheduling.

#### Scenario: PaintClear still schedules the debounced scan

- **WHEN** `on_annotation_event` receives a payload with `data["event"] == "PaintClear"`
- **THEN** the plugin SHALL schedule the existing debounced flush scan, as it does today for any annotation event

#### Scenario: HideDrawings/ShowDrawings broadcast visibility instead of scanning bookmarks

- **WHEN** `on_annotation_event` receives a payload with `data["event"]` equal to `"HideDrawings"` or `"ShowDrawings"`
- **THEN** the plugin SHALL broadcast the corresponding `annotations_visible` boolean via `display_settings`
- **AND** SHALL NOT schedule a bookmark scan for this event

#### Scenario: Unrecognised events keep today's behavior

- **WHEN** `on_annotation_event` receives a payload whose `data["event"]` is not one of the recognised values (e.g. a tool-switch or display-mode change)
- **THEN** the plugin SHALL continue to schedule the generic debounced flush scan, unchanged from current behavior

### Requirement: A local clear is detected even though the AnnotationsUI event channel does not deliver it

`on_annotation_event`'s subscription (`self.get_plugin("AnnotationsUI")` + `subscribe_to_plugin_events`, which joins `plugin_events_group_atom()`) does not receive `AnnotationsUI::send_event()`'s traffic in practice — that method sends `PaintClear`/`HideDrawings`/`ShowDrawings` as a direct point-to-point message to the AnnotationsCore actor, not to AnnotationsUI's own event group. The plugin SHALL NOT depend on `on_annotation_event` alone to detect a local clear; `on_core_annotation_event` (subscribed to AnnotationsCore's `plugin_events_`, which is confirmed to deliver live-stroke events) SHALL also recognise `AnnotationsCore::clear_annotation()`'s 3-tuple `(event_atom, annotation_data_atom, AnnotationBasePtr)` broadcast — one element shorter than the 4/5-tuple live-stroke shape — as a signal to schedule the same debounced flush scan.

#### Scenario: A 3-tuple AnnotationsCore event schedules a flush

- **WHEN** `on_core_annotation_event` receives an event tuple of exactly 3 elements matching `(event_atom, annotation_data_atom, ...)`
- **THEN** the plugin SHALL schedule the existing debounced flush scan
- **AND** SHALL NOT require parsing the third element to do so
