## ADDED Requirements

### Requirement: A peer is assigned a role when its session starts

Session start SHALL resolve this peer's role before it emits anything session-visible, so that no broadcast escapes ahead of the ceiling that governs it.

Resolution SHALL apply the session's memory of the joining participant first and the session's default role otherwise. Where the session's role policy has not yet arrived — a peer that self-elects as master, or one that has requested session state and not yet received it — the peer SHALL resolve to the permissive default rather than to the most restrictive role, so that a session which has declared no policy is never briefly locked.

A peer that receives session state carrying a role policy after it has already resolved a role SHALL re-resolve against that policy, and SHALL re-announce if its role changed.

#### Scenario: Role is resolved before the first broadcast

- **WHEN** a peer starts a session
- **THEN** its role SHALL be resolved before it emits any session-visible broadcast

#### Scenario: A peer that has not yet received policy is not locked

- **WHEN** a peer has started a session and has not yet received session state
- **THEN** it SHALL hold the permissive default role
- **AND** SHALL NOT be treated as a viewer on the grounds that policy is unknown

#### Scenario: Late-arriving policy is applied

- **WHEN** session state carrying a role policy arrives after a peer has resolved a role
- **AND** that policy assigns the peer a different role
- **THEN** the peer SHALL adopt the assigned role
- **AND** SHALL re-announce so other peers observe the change

### Requirement: Leaving a session does not erase the session's memory of a participant's role

A peer's departure SHALL NOT remove that participant from the session's identity-keyed role memory, so that a participant who disconnects and rejoins is recognised on return.

Departure SHALL remove the peer from the peer table as it already does. The distinction is deliberate: the peer table records who is present, and the role memory records what the session has decided about a participant, which outlives any one connection.

#### Scenario: A departed driver is still remembered

- **WHEN** a peer holding the `driver` role leaves the session
- **THEN** it SHALL be removed from the peer table
- **AND** the session's role memory SHALL still record that participant as a driver

#### Scenario: A reconnecting participant is recognised

- **WHEN** that participant rejoins with a new peer GUID
- **THEN** it SHALL be assigned the remembered role rather than the session default
