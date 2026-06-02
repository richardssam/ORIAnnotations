## MODIFIED Requirements

### Requirement: Command-Based Messaging (ASWF PRWG)
The system SHALL use a nested message envelope for all payloads to strictly align with the ASWF Synchronized Review Messaging standard, replacing the legacy flat structure. The payload MUST include a top-level `payload` key containing a `command_schema` and `command`.

#### Scenario: Dispatching a sync payload
- **WHEN** a client broadcasts a timeline patch or playback state
- **THEN** the message SHALL be wrapped in a nested envelope structured as `payload.command_schema` and `payload.command.event`.
