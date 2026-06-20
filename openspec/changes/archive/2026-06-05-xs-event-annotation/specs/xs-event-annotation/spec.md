## ADDED Requirements

### Requirement: AnnotationsCore event subscription
The xStudio plugin SHALL subscribe to AnnotationsCore's `plugin_events_` broadcast group at session connect time to receive `(event_atom, annotation_data_atom, user_id, stroke_completed)` events.

#### Scenario: Subscription succeeds
- **WHEN** the plugin connects to a session
- **THEN** the plugin SHALL log "Subscribed to AnnotationsCore plugin events [2C]"
- **AND** SHALL receive PaintStart, PaintPoint, and PaintEnd events during drawing

#### Scenario: Subscription fails gracefully
- **WHEN** `get_plugin("AnnotationsCore")` raises or returns nothing
- **THEN** the plugin SHALL log the exception and continue without the subscription
- **AND** the fallback scan path SHALL remain active

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

### Requirement: PaintPoint triggers immediate hot-scan
The xStudio plugin SHALL enqueue an immediate hot-scan command when a `stroke_completed=False` event is received, so partial strokes reach remote peers without waiting for the next 33 ms poll tick.

#### Scenario: Mid-stroke partial broadcast
- **WHEN** the user is drawing and PaintPoint events arrive
- **THEN** the hot-scan SHALL run as soon as the poll thread next drains the command queue
- **AND** partial strokes SHALL be broadcast to peers at each new point, not just once per 33 ms tick

### Requirement: AnnotationsCore event observability
The xStudio plugin SHALL count received AnnotationsCore events and log the first one per session, so operators can confirm the subscription is live from the log file.

#### Scenario: First event log
- **WHEN** the first AnnotationsCore event arrives after connect
- **THEN** the plugin SHALL log "[2C] First AnnotationsCore event received"

### Requirement: Fallback scan is a safety net, not a primary path
The fallback scan interval (`ANNOTATION_SCAN_INTERVAL`) SHALL be at least 30 seconds. The fallback scan SHALL NOT be the primary detection path for annotation completions.

#### Scenario: Fallback scan rate
- **WHEN** no annotation events have fired for 30 seconds
- **THEN** the plugin SHALL perform one full bookmark scan as a safety net
- **AND** the scan rate SHALL NOT approach the previous 1-second rate during normal drawing

#### Scenario: Fallback does not regress when events are firing
- **WHEN** AnnotationsCore events are being received normally
- **THEN** the fallback scan MUST NOT trigger between strokes, only after 30 s of inactivity
