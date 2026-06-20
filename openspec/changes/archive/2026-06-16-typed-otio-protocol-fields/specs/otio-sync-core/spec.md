## ADDED Requirements

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
