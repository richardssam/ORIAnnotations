# OpenRV Sync Plugin Specification (Delta)

## MODIFIED Requirements

### Requirement: Network Transport
The plugin SHALL use RabbitMQ fanout exchanges for session-based broadcasting.

#### Scenario: Basic Transport
- **WHEN** message is sent
- **THEN** it reaches RabbitMQ.

### Requirement: Synchronized Playback
The plugin SHALL sync playhead (frame) and play/pause state.

#### Scenario: Scrubbing
- **WHEN** user scrubs
- **THEN** frame matches.

### Requirement: Synchronized Selection
The plugin SHALL sync active node selection.

#### Scenario: Select node
- **WHEN** user selects
- **THEN** selection matches.

### Requirement: Synchronized Annotations
The plugin SHALL sync paint strokes on release.

#### Scenario: Draw stroke
- **WHEN** user draws
- **THEN** stroke matches.
