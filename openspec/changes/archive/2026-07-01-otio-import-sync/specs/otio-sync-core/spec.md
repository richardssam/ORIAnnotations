## ADDED Requirements

### Requirement: Timeline Origin Marker

The protocol SHALL carry a timeline origin marker so peers route a timeline through the same sync model. A timeline's `metadata.sync.origin` SHALL be transmitted with the timeline and SHALL distinguish OTIO-origin timelines (synced as whole-OTIO snapshots for topology changes) from native timelines (synced via per-child patches). Receivers SHALL treat a timeline lacking the marker as native for backward compatibility.

#### Scenario: Origin marker travels with the timeline

- **WHEN** a timeline carrying `metadata.sync.origin` is broadcast
- **THEN** receiving peers SHALL read the marker and route the timeline through the matching sync model

#### Scenario: Missing marker defaults to native

- **WHEN** a timeline without `metadata.sync.origin` is received
- **THEN** the peer SHALL treat it as a native timeline

### Requirement: Whole-OTIO Snapshot Push Message

The protocol SHALL define a typed message that carries a complete OTIO timeline as a whole-timeline snapshot ("brute-force push"), distinct from the per-child mutation messages (`INSERT_CHILD`, `MOVE_CHILD`, `REMOVE_CHILD`, `SET_PROPERTY`, `REPLACE_ANNOTATION_COMMANDS`). The message SHALL be built and consumed through a single message class, and applying it SHALL replace the target timeline's structure wholesale rather than as incremental child mutations. It SHALL preserve each object's `metadata.sync.guid`.

#### Scenario: Topology change produces a snapshot push

- **WHEN** an OTIO-origin timeline undergoes a topology change (clip insert/remove or large re-edit)
- **THEN** the protocol SHALL emit the whole-OTIO snapshot push message carrying the full timeline

#### Scenario: Snapshot push applied wholesale

- **WHEN** a whole-OTIO snapshot push message is received
- **THEN** the target timeline's structure SHALL be replaced from the message
- **AND** each object's `metadata.sync.guid` SHALL be preserved so attribute patches and annotations stay resolvable

#### Scenario: Attribute changes still use per-child patches

- **WHEN** an attribute on an existing clip changes (media swap, cut trim, or CDL)
- **THEN** the protocol SHALL emit the existing per-child `SET_PROPERTY` patch, not the whole-OTIO snapshot push
