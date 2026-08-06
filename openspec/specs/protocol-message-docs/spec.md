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
