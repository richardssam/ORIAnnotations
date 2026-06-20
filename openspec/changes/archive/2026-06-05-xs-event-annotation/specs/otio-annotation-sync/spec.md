## MODIFIED Requirements

### Requirement: OTIO Annotation State Storage
The system SHALL store annotation data in the OTIO state tree using the `SyncEvent` flat schema (e.g. `PaintStart`, `PaintPoints`, `TextAnnotation`) to represent strokes.

Annotation strokes SHALL reach remote peers within one hot-scan interval (~33 ms) of pen-up for both new bookmarks and additional strokes on existing bookmarks. The previous 1-second fallback scan latency for existing-bookmark strokes is no longer acceptable.

#### Scenario: Appending new strokes
- **WHEN** an annotation is created and broadcast to the session
- **THEN** the Master peer SHALL append the corresponding flat view representation of the stroke to the OTIO state tree.

#### Scenario: Late joiner annotation sync
- **WHEN** a new client joins the session and requests the state snapshot
- **THEN** the snapshot SHALL include all previously stored annotations in the flat view schema
- **AND** the joining client SHALL apply these annotations locally.

#### Scenario: Second stroke on existing bookmark latency
- **WHEN** the user draws a second stroke on a frame that already has an annotation
- **THEN** the stroke SHALL reach a remote peer within 250 ms of pen-up (debounce + one hot-scan cycle)
- **AND** SHALL NOT require waiting for the 1-second fallback scan
