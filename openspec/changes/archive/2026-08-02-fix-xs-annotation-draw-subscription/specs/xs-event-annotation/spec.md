# xs-event-annotation (delta)

## MODIFIED Requirements

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

## ADDED Requirements

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

## REMOVED Requirements

### Requirement: AnnotationsUI event type discrimination

**Reason**: The requirement was written against a subscription to the AnnotationsUI plugin's `plugin_events_` group, which never delivered any traffic — `AnnotationsUI::send_event()` sends these payloads point-to-point to the AnnotationsCore actor. Its behavioural content is preserved verbatim by the new "Draw interaction event discrimination" requirement, which names the group that actually carries them.

**Migration**: None for peers or protocol. The same three event names are handled with the same outcomes; only the group they are observed on changes.

### Requirement: A local clear is detected even though the AnnotationsUI event channel does not deliver it

**Reason**: This required recognising `AnnotationsCore::clear_annotation()`'s 3-tuple `(event_atom, annotation_data_atom, AnnotationBasePtr)` broadcast. That broadcast goes to `live_edit_event_group_`, which xStudio exposes no Python accessor for, so the tuple could never arrive. The `PaintClear` draw interaction is the reachable signal for the same user action and schedules the same debounced flush.

**Migration**: None. `PaintClear` fires on the same Ctrl+D gesture, and the subsequent scan's count-decrease detection still produces the replace broadcast.
