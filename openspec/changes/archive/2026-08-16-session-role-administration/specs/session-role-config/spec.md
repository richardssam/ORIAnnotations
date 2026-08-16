## MODIFIED Requirements

### Requirement: A session declares the role given to participants it does not recognise

A session SHALL carry a default role, applied to any joining peer the session has no record of.

The default SHALL be `driver` when the session declares none. A session that has not opted into a role policy therefore behaves exactly as a session without roles: every peer may emit everything, bounded only by category authority. This is both the backwards-compatibility guarantee and the rollback — restoring the permissive default restores present behaviour without a rebuild.

Because the default is permissive, a defect in role evaluation SHALL fail towards a session that behaves as it does today rather than towards a session in which nobody may drive.

The default SHALL be declarable **when the session is created**, through the host application that creates it, and not only through the environment. Declaring a policy by editing the environment of every participant's application before it launches is not a workable way to run a review: it makes the mechanism unreachable in practice, so every real session runs on the permissive default whether or not that is what was wanted.

The declaration SHALL be offered on the create path only, in both host applications. A peer joining an existing session SHALL NOT declare a default: the session it is joining already has one, and it will receive it in session state.

The default SHALL be fixed for the life of the session. It is set once, on the creator — who is also the initial master — and is carried unchanged in every session state sent thereafter. There is deliberately no mid-session change: a default that could move would need a propagation path of its own, and the case it would serve is served by granting individual participants a role instead.

Declaring a default that is not `driver` SHALL seed the creating participant's identity into the session's role memory as `driver`. Without this the creator resolves against their own declared default and becomes a non-driver in the session they just started — a session that immediately reports itself driverless. That condition is self-recovering, but requiring the organiser to recover from their own setup choice is not acceptable as designed behaviour.

#### Scenario: A session with no declared policy behaves as it does today

- **WHEN** a session carries no role policy
- **THEN** every joining peer SHALL be assigned the `driver` role
- **AND** no field group SHALL be stripped on account of role

#### Scenario: A screening session restricts new participants

- **WHEN** a session declares a default role of `viewer`
- **THEN** a joining peer the session has no record of SHALL be assigned `viewer`
- **AND** SHALL receive all session state while emitting nothing session-visible

#### Scenario: Restoring the permissive default restores prior behaviour

- **WHEN** a session's default role is returned to `driver`
- **THEN** role SHALL strip no field group from any peer
- **AND** no category claim SHALL be refused on account of role

#### Scenario: The creator declares the default from the host application

- **WHEN** a user creates a session from a host application and selects a default role
- **THEN** the session SHALL carry that default
- **AND** SHALL NOT require the role to have been declared in the environment beforehand

#### Scenario: Declaring a restrictive default does not lock out the creator

- **WHEN** a user creates a session declaring a default role of `viewer` or `reviewer`
- **THEN** the creating participant SHALL hold the `driver` role
- **AND** the session SHALL NOT report itself as having no eligible driver

#### Scenario: The join path declares no default

- **WHEN** a user joins an existing session
- **THEN** no default role SHALL be declared by that peer
- **AND** the default SHALL be taken from the session state it receives

#### Scenario: The default does not change during the session

- **WHEN** a session has been created with a declared default role
- **THEN** that default SHALL remain in force for the life of the session
- **AND** every session state sent SHALL carry the same value

#### Scenario: A default declared in the host takes precedence over the environment

- **WHEN** a session is created with a default role selected in the host application
- **AND** the environment also declares a default role
- **THEN** the value selected in the host application SHALL be the session's default

### Requirement: The session remembers roles by participant identity, not by peer GUID

A session SHALL hold a map from participant identity to role, and SHALL consult it before applying the default.

The key SHALL be the participant's account identity, not the peer's GUID and not a session-specific token. A driver who disconnects and rejoins their own session receives a new GUID, so GUID-keyed memory fails at the only case it exists for: the driver would be assigned the default role and locked out of the session they are running.

The key SHALL be the account identity alone, not the account and machine together. One person working from two machines therefore holds the same role on both, which is the behaviour expected of a supervisor with a workstation and a laptop.

The memory SHALL have two writers, and both SHALL write through the same merge: the policy a peer adopts from session state, and a role grant issued during the session (`session-role-administration`). Adopting policy and recording a grant SHALL both be additive — neither SHALL remove a participant already remembered — so that the two cannot undo each other and a peer's memory converges regardless of the order in which it learns things.

The limit of this SHALL be stated: identity is self-declared and may be overridden by the user, so a participant who enters another person's account name inherits that person's remembered role. This is acceptable under this capability's non-goal of adversarial security, and is not weaker than a shared secret crossing an unauthenticated broker — but it is the mechanism, so it is a decision and not an accident. Strengthening it SHALL be done by replacing the identity source with an authenticated one, not by adding verification to the role layer.

#### Scenario: A reconnecting driver keeps its role

- **WHEN** a peer holding the `driver` role leaves a session whose default role is `viewer`
- **AND** the same participant rejoins with a new peer GUID
- **THEN** it SHALL be assigned `driver` from the session's memory
- **AND** SHALL NOT be assigned the session default

#### Scenario: An unrecognised participant receives the default

- **WHEN** a participant whose identity the session has no record of joins
- **THEN** it SHALL be assigned the session's default role

#### Scenario: One participant on two machines holds one role

- **WHEN** the same participant joins a session from two machines
- **THEN** both peers SHALL hold the same role

#### Scenario: Role assignment consults memory before the default

- **WHEN** a peer joins a session that both declares a default role and remembers that participant
- **THEN** the remembered role SHALL be applied
- **AND** the default SHALL NOT override it

#### Scenario: A grant is recorded in the memory alongside adopted policy

- **WHEN** a peer that has adopted the session's role policy receives a role grant
- **THEN** the granted participant's role SHALL be recorded in the same memory
- **AND** the participants named by the adopted policy SHALL remain remembered

#### Scenario: Later policy does not undo a recorded grant

- **WHEN** a peer holds a grant for a participant and subsequently receives session state whose policy does not name that participant
- **THEN** the recorded grant SHALL remain in that peer's memory
