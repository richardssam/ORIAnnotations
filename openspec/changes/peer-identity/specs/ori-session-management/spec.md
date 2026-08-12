## ADDED Requirements

### Requirement: Identity is resolved once when a session starts

A peer's own identity SHALL be resolved when its session starts and held for the life of that session, rather than being recomputed per message.

Resolution SHALL be best-effort and SHALL NOT fail a session start: a source that yields no personal name, or no identity at all, SHALL leave those fields empty and the session SHALL proceed.

#### Scenario: Identity is resolved at session start

- **WHEN** a peer starts or joins a session
- **THEN** its identity SHALL be resolved once
- **AND** every message that carries identity SHALL carry that same resolved identity

#### Scenario: An unresolvable identity does not block a session

- **WHEN** no identity can be resolved for the local peer
- **THEN** the session SHALL start
- **AND** the peer SHALL participate carrying no identity

### Requirement: A user may declare their identity when joining

The join and create flows SHALL allow a user to supply an identity in place of the one resolved from their machine, for shared workstations, loaner seats, and machines named after a previous occupant.

A supplied identity SHALL be recorded as user-entered rather than resolved, and SHALL NOT be verified. An identity supplied for one session SHALL NOT be assumed to apply to the next.

#### Scenario: A user overrides the machine-resolved identity

- **WHEN** a user supplies an identity while joining a session
- **THEN** peers SHALL see the supplied identity rather than the machine-resolved one
- **AND** its provenance SHALL record that it was entered by the user

#### Scenario: No override supplied

- **WHEN** a user joins a session without supplying an identity
- **THEN** the machine-resolved identity SHALL be used
