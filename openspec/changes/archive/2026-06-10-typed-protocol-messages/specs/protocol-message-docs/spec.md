## ADDED Requirements

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
The generator SHALL emit a standalone HTML page, consistent with the existing OTIO message documentation output and independent of the Sphinx build.

#### Scenario: HTML page produced
- **WHEN** the generator completes
- **THEN** it SHALL write a self-contained HTML document presenting the protocol messages.

### Requirement: Examples and Categories from a Side-File
The generator SHALL support categorizing messages and attaching example payloads via an external configuration side-file, in the same style as the OTIO documentation's `config.yml`.

#### Scenario: Categorized messages with examples
- **WHEN** a configuration side-file assigns categories and example payloads to messages
- **THEN** the generated documentation SHALL group messages by their assigned category and display the provided examples.

#### Scenario: Messages without configured examples still documented
- **WHEN** a registered message has no entry in the configuration side-file
- **THEN** the message SHALL still appear in the documentation using its class-derived schema, event, and fields.
