# xs-event-annotation

## Purpose

Defines how the xStudio sync plugin uses AnnotationsCore's draw events to drive immediate annotation detection and broadcast, replacing the previous polling-only approach with an event-driven path. Mid-stroke partials broadcast directly from event-carried geometry (no bookmark read or per-tick scan); pen-up flushes the committed stroke immediately. A 30-second fallback scan remains as a safety net only.

---

## Requirements

### Requirement: AnnotationsCore event subscription

The xStudio plugin SHALL subscribe to AnnotationsCore's draw-events broadcast group at session connect time, via the API xStudio provides for that purpose (`subscribe_to_annotation_draw_events`). The plugin SHALL NOT subscribe to any plugin's `plugin_events_` group for annotation traffic: nothing is broadcast there, so such a subscription is silently inert.

That group carries two kinds of event, which the subscription API delivers through one callback signature `(event_data, user_id, stroke_completed)`:

- a **live stroke** — the serialised in-progress annotation, with `user_id` and a `stroke_completed` flag;
- a **draw interaction** — the raw interaction payload whose `"event"` field names the action, delivered with `user_id` and `stroke_completed` both absent.

The plugin SHALL discriminate the two by whether `stroke_completed` is present, and SHALL NOT discriminate by tuple length or by inspecting CAF message element types.

#### Scenario: Subscription succeeds
- **WHEN** the plugin connects to a session
- **THEN** the plugin SHALL log "Subscribed to AnnotationsCore plugin events [2C]"
- **AND** SHALL receive PaintStart, PaintPoint, and PaintEnd events during drawing

#### Scenario: Live stroke with geometry
- **WHEN** an event arrives carrying a `stroke_completed` flag
- **THEN** the plugin SHALL treat the payload as serialised annotation geometry
- **AND** SHALL take the direct live-stroke broadcast path using that geometry

#### Scenario: Draw interaction
- **WHEN** an event arrives with no `stroke_completed` flag
- **THEN** the plugin SHALL treat the payload as a draw interaction and dispatch on its `"event"` field

#### Scenario: Live stroke carries no geometry
- **WHEN** a live-stroke event arrives whose payload is empty, as AnnotationsCore sends for shape tools before the shape is completed
- **THEN** the plugin SHALL NOT broadcast a partial for that event
- **AND** the committed stroke SHALL still be broadcast by the pen-up flush

#### Scenario: Subscription fails gracefully
- **WHEN** the subscription call raises
- **THEN** the plugin SHALL log the exception and continue without the subscription
- **AND** the 30-second fallback scan path SHALL remain active as the only safety net

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

### Requirement: Draw interaction event discrimination

The xStudio plugin SHALL inspect the `"event"` field of each draw interaction received from AnnotationsCore's draw-events group, rather than treating every interaction identically as a generic "schedule a bookmark scan" trigger. `PaintClear`, `HideDrawings`, and `ShowDrawings` SHALL each be handled according to their own requirements (deletion detection, visibility broadcast) in addition to — or instead of — the generic scan scheduling.

These interactions originate in AnnotationsUI, which sends them point-to-point to the AnnotationsCore actor; AnnotationsCore re-broadcasts them on its draw-events group. They are therefore only observable there, never on AnnotationsUI's own event group.

#### Scenario: PaintClear schedules the debounced scan

- **WHEN** a draw interaction with `"event" == "PaintClear"` is received
- **THEN** the plugin SHALL schedule the existing debounced flush scan

#### Scenario: HideDrawings/ShowDrawings broadcast visibility instead of scanning bookmarks

- **WHEN** a draw interaction with `"event"` equal to `"HideDrawings"` or `"ShowDrawings"` is received
- **THEN** the plugin SHALL broadcast the corresponding `annotations_visible` boolean via `display_settings`
- **AND** SHALL NOT schedule a bookmark scan for this event

#### Scenario: Unrecognised interactions schedule the generic scan

- **WHEN** a draw interaction whose `"event"` is not one of the recognised values (e.g. a tool switch or display-mode change) is received
- **THEN** the plugin SHALL schedule the generic debounced flush scan

