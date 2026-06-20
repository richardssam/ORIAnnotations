# otio-sync-core

## Purpose
Coordinate OTIO timeline synchronisation across a networked review session: a command-based message envelope, typed protocol message definitions as the single source of truth for the wire format, registry-based dispatch of incoming messages, and the patching engine that builds and applies OTIO mutations.

## Requirements

### Requirement: Command-Based Messaging (ASWF PRWG)
The system SHALL use a nested message envelope for all payloads to strictly align with the ASWF Synchronized Review Messaging standard, replacing the legacy flat structure. The payload MUST include a top-level `payload` key containing a `command_schema` and `command`. The `command_schema`, the `command.event` name, and the shape of `command.payload` SHALL be derived from a typed message class rather than from inline string literals and ad-hoc dictionaries.

#### Scenario: Dispatching a sync payload
- **WHEN** a client broadcasts a timeline patch or playback state
- **THEN** the message SHALL be wrapped in a nested envelope structured as `payload.command_schema` and `payload.command.event`.

#### Scenario: Envelope fields derived from a typed message
- **WHEN** a client broadcasts any protocol message
- **THEN** the `command_schema` and `command.event` written to the envelope SHALL equal the schema and event declared on the corresponding typed message class
- **AND** the byte-level envelope structure and field names SHALL be unchanged from the prior string-literal implementation, so peers running older code interoperate without modification.

### Requirement: Typed Protocol Message Definitions
The system SHALL define each transport-layer protocol message as a typed class that is the single source of truth for that message's `command_schema`, `event` name, and payload field set. Sending a message SHALL construct the corresponding class; no protocol message SHALL be assembled from free-standing schema/event string literals at the call site.

#### Scenario: Every broadcast corresponds to a defined message class
- **WHEN** any `broadcast_*` operation sends a message
- **THEN** that message SHALL be represented by a registered message class whose declared schema and event match the envelope produced.

#### Scenario: Payload built without reflective serialization
- **WHEN** a message instance is serialized to its wire payload
- **THEN** the payload SHALL be produced by an explicit per-message conversion (not by reflective whole-object serialization), so hot-path messages incur no additional traversal cost.

### Requirement: Registry-Based Message Dispatch
The system SHALL dispatch incoming messages through a registry keyed by `(command_schema, event)` that maps to the handling logic, replacing the sequential string-comparison conditional chain. The registry SHALL be derived from the message class definitions so it cannot diverge from them.

#### Scenario: Known message is dispatched to its handler
- **WHEN** an incoming envelope carries a `(command_schema, event)` pair that matches a registered message class
- **THEN** the payload SHALL be reconstructed into that message type and routed to the registered handler.

#### Scenario: Unknown message is ignored safely
- **WHEN** an incoming envelope carries a `(command_schema, event)` pair with no registered message class
- **THEN** the system SHALL ignore the message without raising an error and SHALL continue processing subsequent messages.

### Requirement: Single Definition for OTIO Session Payloads
The system SHALL ensure the OTIO session mutation messages (`INSERT_CHILD`, `MOVE_CHILD`, `REMOVE_CHILD`, `SET_PROPERTY`, `REPLACE_ANNOTATION_COMMANDS`) are built and consumed through the same message class, so the payload shape for these messages is defined in exactly one place. The patching engine SHALL produce these messages when generating mutations and SHALL reconstruct the same message type when applying them.

#### Scenario: Mutation produces a typed message
- **WHEN** the patching engine performs a local insert, move, remove, property change, or annotation-command replacement
- **THEN** it SHALL return the corresponding typed message whose payload is the value transmitted on the wire.

#### Scenario: Mutation applied from the same definition
- **WHEN** an OTIO session mutation message is received
- **THEN** it SHALL be reconstructed into the same message type used to build it and applied, with no separately-maintained payload-shape declaration.

### Requirement: Settings Messages Declare Fields but Tolerate Extras
The system SHALL provide message classes for `PLAYBACK_SETTINGS_1.0/SET` and `DISPLAY_SETTINGS_1.0/SET` that document their known fields, while accepting messages that contain additional, unrecognized fields without failure. This preserves interoperability with independent producers that may emit extra keys.

#### Scenario: Known settings fields are documented
- **WHEN** the playback and display settings message classes are defined
- **THEN** they SHALL enumerate the established fields (playback: `playing`, `current_time`, `looping`, `timeline_guid`, `sync_timestamp`; display: `pan`, `zoom`, `exposure`, `channel`, `sync_timestamp`).

#### Scenario: Extra fields do not break parsing
- **WHEN** a settings message arrives containing fields beyond the declared set
- **THEN** the message SHALL be parsed and applied without error, and unrecognized fields SHALL be ignored rather than rejected.

### Requirement: Performance Parity on Hot Paths
The system SHALL NOT degrade the throughput of high-frequency message paths, specifically partial-annotation streaming and playback-state broadcasts. Construction and serialization of these messages SHALL avoid added per-message validation or reflective traversal.

#### Scenario: Hot-path messages avoid added overhead
- **WHEN** a partial-annotation or playback-state message is constructed and serialized
- **THEN** it SHALL not perform isinstance-style validation or reflective field walking, so per-message cost remains at parity with the prior implementation.

### Requirement: Messages Own OTIO Serialization

The protocol messages that carry OTIO content — `AddTimeline` (`timeline`), `StateSnapshot` (`timelines`), `InsertChild` (`child_data`), and `ReplaceAnnotationCommands` (`commands`) — SHALL accept the OTIO object(s) directly rather than pre-serialized dictionaries, and SHALL own the conversion to and from wire form. The producer SHALL pass the OTIO object to the message constructor; the message SHALL serialize it when building its wire payload. Callers SHALL NOT serialize OTIO before constructing these messages. The hot-path `PartialAnnotation.events` field is explicitly excluded and SHALL continue to carry serialized dictionaries.

#### Scenario: Producer passes an OTIO object, not a dict

- **WHEN** a producer constructs one of these messages to broadcast OTIO content
- **THEN** it SHALL supply the live OTIO object(s) to the constructor
- **AND** the message's `to_payload()` SHALL emit the serialized wire form, with no `_otio_to_dict` call at the construction site.

#### Scenario: Wire payload is byte-identical to the pre-serialized form

- **WHEN** one of these messages serializes an OTIO object to its wire payload
- **THEN** the resulting payload SHALL be byte-for-byte identical to the prior implementation that pre-serialized with `otio_json`
- **AND** peers running older code SHALL interoperate without modification.

#### Scenario: Hot-path streaming is unchanged

- **WHEN** a `PartialAnnotation` is constructed and serialized
- **THEN** its `events` field SHALL still carry serialized dictionaries
- **AND** no OTIO deserialize-then-reserialize SHALL be introduced on this path.

### Requirement: Lazy OTIO Deserialization on Receive

When an OTIO-bearing message is reconstructed from a received payload, the system SHALL store the raw wire form and SHALL defer deserialization until a handler requests the OTIO object(s) through a dedicated accessor. Reconstructing the message from a payload SHALL NOT eagerly deserialize the OTIO content, so a handler can make admission decisions before paying deserialization cost.

#### Scenario: Reconstruction does not deserialize

- **WHEN** an OTIO-bearing message is reconstructed from a received wire payload
- **THEN** the OTIO content SHALL be retained in its raw wire form
- **AND** no OTIO deserialization SHALL occur until the accessor is called.

#### Scenario: Handler skips deserialization before its guard

- **WHEN** an `AddTimeline` is received for a timeline GUID the receiver already holds
- **THEN** the handler SHALL be able to reject it on the GUID check without deserializing the timeline payload.

#### Scenario: Accessor returns the OTIO object form

- **WHEN** a handler calls the message's OTIO accessor
- **THEN** it SHALL receive the OTIO object(s) for that message, deserializing any element still in wire form and passing through any element already an OTIO object.

### Requirement: Protocol Module Importable Without OTIO

The protocol message module SHALL remain importable without `opentimelineio` installed, so the documentation generator can enumerate message classes and field metadata. Any dependency on `opentimelineio` SHALL be loaded lazily inside the serialization and accessor methods, never at module import time.

#### Scenario: Doc generator imports without OTIO

- **WHEN** the protocol message module is imported in an environment without `opentimelineio`
- **THEN** the import SHALL succeed and all message classes and their field metadata SHALL be available
- **AND** `opentimelineio` SHALL only be required when a serialization or OTIO-accessor method is actually invoked.

### Requirement: Timeline Removal Message and Teardown

The `TIMELINE_1.0` family SHALL include a `RemoveTimeline` message
(`EVENT = "REMOVE_TIMELINE"`) carrying `timeline_guid` and `sync_timestamp`,
registered for dispatch alongside `AddTimeline` and `RenameTimeline`. The message
SHALL NOT carry an OTIO payload — the GUID alone identifies a timeline peers
already hold.

`SyncManager` SHALL provide `broadcast_remove_timeline(guid)`, symmetric to
`broadcast_add_timeline`, which removes the timeline locally and sends a
`RemoveTimeline` to all peers. The inbound handler SHALL perform a single-timeline,
reference-aware teardown rather than clearing all timeline state.

#### Scenario: Removal message is registered and dispatched

- **WHEN** a `REMOVE_TIMELINE` message under `TIMELINE_1.0` is received
- **THEN** it SHALL be dispatched to the timeline-removal handler via the message
  registry, the same mechanism used for `ADD_TIMELINE` and `RENAME_TIMELINE`

#### Scenario: Removing a sequence timeline tears down only its own state

- **WHEN** a `RemoveTimeline` is received for a sequence timeline GUID the receiver
  holds
- **THEN** the manager SHALL delete that GUID from `_timelines`
- **AND** SHALL remove from the shared `_object_map` only the GUIDs belonging to
  that timeline's subtree, leaving every other timeline's object-map entries intact

#### Scenario: Clip-annotation timelines cascade with their sequence

- **WHEN** the removed sequence has one or more clips that own clip-annotation
  timelines
- **THEN** the manager SHALL delete those clip-annotation timelines from both
  `_clip_timelines` and `_timelines`
- **AND** no `_clip_timelines` entry referencing the removed subtree SHALL remain

#### Scenario: Removing the active timeline clears the active pointer

- **WHEN** the removed timeline's GUID equals `active_timeline_guid`
- **THEN** the manager SHALL set `active_timeline_guid` to `None`
- **AND** SHALL NOT select a replacement timeline or carry a successor GUID in the
  message, because the active timeline is re-asserted by the next
  `PlaybackSettingsSet`

#### Scenario: Removal is idempotent for unknown timelines

- **WHEN** a `RemoveTimeline` is received for a GUID not present in `_timelines`
- **THEN** the handler SHALL make no state changes and return no host event
  (silent no-op)

#### Scenario: Real removal notifies the host to tear down its container

- **WHEN** a `RemoveTimeline` removes a sequence timeline the receiver held
- **THEN** the handler SHALL return a `("remove_timeline", tl)` action carrying the
  removed timeline object, symmetric to the `("add_timeline", tl)` action emitted
  on registration
