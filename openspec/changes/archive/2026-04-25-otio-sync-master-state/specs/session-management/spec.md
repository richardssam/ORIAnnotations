# Session Management Specification

## Requirements

### Requirement: Master Election
The system SHALL designate the eldest active peer in a session as the "Master" and primary source of truth.

#### Scenario: Joining an existing session
- **WHEN** a new peer joins and broadcasts `WHO_IS_MASTER`
- **THEN** the Master peer SHALL respond with `I_AM_MASTER`.

#### Scenario: Starting a new session
- **WHEN** a new peer joins and receives no `I_AM_MASTER` response after a timeout
- **THEN** it SHALL promote itself to Master.

### Requirement: Full State Snapshot
The Master SHALL be capable of serializing the complete session state into a `STATE_SNAPSHOT` payload.

### Requirement: Lossless Join (Buffering)
Joining peers SHALL buffer incoming delta events while waiting for the snapshot to ensure no mutations are missed during the transfer.

#### Scenario: Replaying the buffer
- **WHEN** the `STATE_SNAPSHOT` is successfully applied
- **THEN** the client SHALL apply any buffered events that were broadcast after the snapshot was generated.
