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
