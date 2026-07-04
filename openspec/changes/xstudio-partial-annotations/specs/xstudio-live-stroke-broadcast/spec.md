## ADDED Requirements

### Requirement: Live-stroke broadcast carries serialized geometry

`AnnotationsCore::broadcast_live_stroke` SHALL serialize the cumulative in-progress `live_stroke` via `Annotation::serialise()` and include the resulting `JsonStore` in the `plugin_events_` broadcast, emitting the 5-tuple `(event_atom, annotation_data_atom, JsonStore, user_id, stroke_completed)`. This amends the geometry-less 4-tuple introduced by the `pr/annotation-stroke-events` branch (commit `7d679cc8`); it MUST NOT add a second, separate broadcast to the same group.

#### Scenario: PaintPoint broadcast includes geometry
- **WHEN** the user is drawing and a PaintPoint is processed
- **THEN** the `plugin_events_` broadcast SHALL be a 5-tuple whose third element is a `JsonStore`
- **AND** the `JsonStore` SHALL contain the stroke points accumulated so far in this gesture

#### Scenario: Cumulative growth across a gesture
- **WHEN** successive PaintPoint events arrive for one stroke id
- **THEN** each broadcast's serialized stroke SHALL contain all points from PaintStart up to and including the current point
- **AND** SHALL NOT contain only the latest point delta

#### Scenario: Single event per paint phase
- **WHEN** a paint phase (PaintStart, PaintPoint, or PaintEnd) is processed
- **THEN** exactly one annotation event SHALL be sent to `plugin_events_` for that phase
- **AND** a geometry-less 4-tuple SHALL NOT also be sent for the same phase

---

### Requirement: Broadcast fires on every paint phase with correct completion flag

`broadcast_live_stroke` SHALL emit a `plugin_events_` event on PaintStart, on every PaintPoint, and on PaintEnd. The `stroke_completed` flag SHALL be `False` for PaintStart and PaintPoint and `True` only for PaintEnd.

#### Scenario: Pen-up sets completion flag
- **WHEN** the user lifts the pen (PaintEnd)
- **THEN** the broadcast SHALL carry `stroke_completed=True`

#### Scenario: Mid-stroke clears completion flag
- **WHEN** a PaintStart or PaintPoint is processed
- **THEN** the broadcast SHALL carry `stroke_completed=False`

---

### Requirement: Serialized JSON matches the annotation serialiser contract

The `JsonStore` broadcast SHALL be the standard xStudio annotation serialisation, of the form `{"Annotation Serialiser Version": N, "Data": {"pen_strokes": [...]}}`, so that a Python consumer can read `Data.pen_strokes` directly without a bookmark lookup.

#### Scenario: Consumer parses pen_strokes directly
- **WHEN** a Python plugin receives the 5-tuple and loads the `JsonStore`
- **THEN** `Data.pen_strokes` SHALL be a list containing the in-progress stroke
- **AND** the plugin SHALL require no bookmark read to obtain the geometry

---

### Requirement: Existing in-process live-edit broadcast is preserved

The change SHALL NOT alter the existing `live_edit_event_group_` broadcast that carries the `AnnotationBasePtr` for in-process C++ consumers; the new geometry is added only to the Python-facing `plugin_events_` broadcast.

#### Scenario: In-process consumers unaffected
- **WHEN** a stroke event is broadcast
- **THEN** `live_edit_event_group_` SHALL still receive its `AnnotationBasePtr`-bearing event unchanged
- **AND** `plugin_events_` SHALL additionally carry the serialized geometry

---

### Requirement: Legacy consumers tolerate the new shape

The 5-tuple SHALL remain backward-tolerable: consumers that only inspect `data[0]`/`data[1]` (event and atom type) and the trailing `stroke_completed` flag SHALL continue to function, since the appended `JsonStore` occupies the third slot ahead of `user_id` and `stroke_completed`.

#### Scenario: Old consumer ignores the geometry slot
- **WHEN** a consumer written against the 4-tuple receives the 5-tuple
- **THEN** it SHALL still be able to discriminate the event by tuple length
- **AND** SHALL not crash on the additional `JsonStore` element
