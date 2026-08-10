# protocol-message-docs

## Purpose
Generate human-readable documentation of the transport-layer sync protocol messages directly from their typed message classes, so the protocol reference cannot drift from the implementation — the envelope-layer analogue of the existing OTIO SyncEvent documentation.

## Requirements

### Requirement: Protocol Message Documentation Generation
The system SHALL provide a documentation generator that produces human-readable documentation of the transport-layer protocol messages directly from the typed message classes, mirroring the existing OTIO SyncEvent documentation pipeline. The generator SHALL read the message definitions rather than a manually-maintained message list, so the documentation cannot drift from the implemented messages.

#### Scenario: Documentation generated from message classes
- **WHEN** the generator runs against the protocol message module
- **THEN** it SHALL emit documentation derived from the registered message classes, their declared schema and event, and their fields.

#### Scenario: New message appears in docs automatically
- **WHEN** a new protocol message class is added and registered
- **THEN** regenerating the documentation SHALL include that message without any other manual edit to the generator.

### Requirement: Documented Message Detail
For each protocol message, the generated documentation SHALL present its `command_schema`, `event` name, and each payload field with the field's name, type, and description. Field descriptions SHALL be sourced from the message class definitions (e.g. docstrings or field metadata).

#### Scenario: Message entry shows schema, event, and fields
- **WHEN** the documentation for a given message is rendered
- **THEN** it SHALL display the message's command schema, event name, and a list of payload fields with their types and descriptions.

### Requirement: Standalone HTML Output
The generator SHALL produce a single self-contained HTML document presenting the protocol messages as part of the unified documentation output produced by `doc_generator.py`. The protocol message section SHALL use the same rich HTML format as the OTIO SyncEvent section (sidebar navigation, tabbed examples, copy buttons), replacing the previous simple dark-theme single-purpose output. The output SHALL be independent of the Sphinx build.

#### Scenario: HTML page produced
- **WHEN** the generator completes
- **THEN** it SHALL write a self-contained HTML document presenting both OTIO events and protocol messages in a unified, consistently styled layout

### Requirement: Examples and Categories from a Side-File
The generator SHALL support categorizing messages and attaching example payloads via the `protocol_messages` section of the unified `config.yml` file, in place of the previous standalone `protocol_messages_config.yml`. The configuration format (class-name keys, `_category`, named example sub-keys) SHALL remain identical; only the file and section structure changes.

#### Scenario: Categorized messages with examples
- **WHEN** the unified config assigns categories and example payloads to protocol messages under `protocol_messages:`
- **THEN** the generated documentation SHALL group those messages by their assigned category and display the provided examples

#### Scenario: Messages without configured examples still documented
- **WHEN** a registered message has no entry in the `protocol_messages` section of the unified config
- **THEN** the message SHALL still appear in the documentation using its class-derived schema, event, and fields

### Requirement: Python instantiation examples for protocol messages
For each protocol message, the generator SHALL produce a Python example tab alongside the JSON example tab. The Python example SHALL show how to import and instantiate the message class from `otio_sync_core.protocol_messages`, with a comment on the first line reading `# Protocol Message (transport layer)`.

#### Scenario: Python tab present for protocol message with examples
- **WHEN** a protocol message has example parameters in the unified config
- **THEN** the rendered documentation SHALL include a Python instantiation tab for that message

#### Scenario: Python example identifies the layer
- **WHEN** the Python tab for a protocol message is displayed
- **THEN** the first line of the code block SHALL be `# Protocol Message (transport layer)`

### Requirement: The departure message is documented as best-effort

The generated protocol documentation SHALL describe the peer departure message:
its schema and event name, the peer it identifies, and that it is emitted when a
peer closes its session.

The documentation SHALL state that delivery is **best-effort** and that
correctness does not depend on it — a departure that is never delivered is
resolved by peer inactivity instead. A reader who assumes the message is
guaranteed would be entitled to skip the inactivity path, which is the one that
covers crashes.

#### Scenario: Departure message appears in the generated protocol reference

- **WHEN** the protocol reference is generated
- **THEN** it SHALL include the departure message with its schema, event name,
  and fields
- **AND** SHALL state that the message is best-effort and backed by an
  inactivity fallback

### Requirement: The announcement message documents its periodic cadence

The generated protocol documentation for the peer announcement message SHALL
describe that it is sent on joining and periodically thereafter, and SHALL NOT
describe an answering behaviour that no longer exists.

The existing documentation explains that answers are suppressed to avoid an
announcement storm, and that answering is what lets a late joiner discover peers
that have gone quiet. Both statements SHALL be replaced rather than left to
contradict the implementation: periodic announcement is now what makes a quiet
peer discoverable, and the cascade the suppression guarded against no longer has
a mechanism to occur through.

#### Scenario: Announcement cadence is documented

- **WHEN** the protocol reference is generated
- **THEN** the peer announcement entry SHALL describe both emission occasions:
  on joining, and periodically
- **AND** SHALL NOT refer to answering another peer's announcement
- **AND** SHALL identify periodic announcement as what makes a quiet peer
  discoverable

### Requirement: Session state documents the peer roster it carries

The generated protocol documentation for the session state message SHALL
describe the peer roster it carries: that it identifies the peers present when
the state was taken, and that it exists so a joining peer learns the peer set
without other peers answering its announcement.

The documentation SHALL state that the roster is **not** the only means of
discovery — a joiner that receives no session state learns peers from their
periodic announcements instead.

#### Scenario: Roster appears in the generated protocol reference

- **WHEN** the protocol reference is generated
- **THEN** the session state entry SHALL document the peer roster field
- **AND** SHALL state that periodic announcement is the fallback when no session
  state is received

### Requirement: Ownership claim and release messages are documented

The generated protocol documentation SHALL include `CLAIM_OWNERSHIP` and `RELEASE_OWNERSHIP`: their schema, event name, and fields (`category`, `peer_guid`, and — for `CLAIM_OWNERSHIP` — `claim_ts`), generated the same way as every other typed message, with no manually-maintained addition beyond registering the classes.

#### Scenario: Ownership messages appear in the generated reference
- **WHEN** the protocol reference is generated after the ownership messages are registered
- **THEN** it SHALL include `CLAIM_OWNERSHIP` and `RELEASE_OWNERSHIP` with their fields, schema, and event name
- **AND** no manual edit to the generator SHALL have been required beyond registering the message classes

### Requirement: Session state documents the ownership section it carries

The generated protocol documentation for the session state message SHALL describe the `broadcast_ownership` section: that it reports, per leased category, the current owner and remaining lease time, and that the section is omitted when a category has no owner — mirroring the existing documentation of how the message's peer roster and host fields behave when unset.

#### Scenario: The ownership section appears in the generated protocol reference
- **WHEN** the protocol reference is generated
- **THEN** the session state entry SHALL document the `broadcast_ownership` section, its per-category fields, and that an omitted category means no reported owner rather than an explicitly free one
