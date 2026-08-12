## Purpose

Defines the session-level policy that decides which role a peer holds — the role given to participants the session does not recognise, and the session's memory of who has held a role before — together with how that policy reaches a joining peer.

## ADDED Requirements

### Requirement: A session declares the role given to participants it does not recognise

A session SHALL carry a default role, applied to any joining peer the session has no record of.

The default SHALL be `driver` when the session declares none. A session that has not opted into a role policy therefore behaves exactly as a session without roles: every peer may emit everything, bounded only by category authority. This is both the backwards-compatibility guarantee and the rollback — restoring the permissive default restores present behaviour without a rebuild.

Because the default is permissive, a defect in role evaluation SHALL fail towards a session that behaves as it does today rather than towards a session in which nobody may drive.

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

### Requirement: The session remembers roles by participant identity, not by peer GUID

A session SHALL hold a map from participant identity to role, and SHALL consult it before applying the default.

The key SHALL be the participant's account identity, not the peer's GUID and not a session-specific token. A driver who disconnects and rejoins their own session receives a new GUID, so GUID-keyed memory fails at the only case it exists for: the driver would be assigned the default role and locked out of the session they are running.

The key SHALL be the account identity alone, not the account and machine together. One person working from two machines therefore holds the same role on both, which is the behaviour expected of a supervisor with a workstation and a laptop.

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

### Requirement: Role policy reaches a joining peer in session state

Role policy — the default role and the identity-keyed memory — SHALL be carried in the session state a joining peer receives, alongside the timeline, playback, host, peer roster, and ownership state that channel already carries.

It SHALL follow the compatibility convention those existing sections share: the section SHALL be omitted when the session declares no policy, and an absent or null section on receipt SHALL be ignored rather than interpreted as an empty policy. A peer running code that predates roles therefore cannot clear a session's role policy by sending or relaying session state, and a session that has declared a policy cannot lock out a peer that predates it.

Adoption of received role policy SHALL go through a single named operation rather than direct assignment, mirroring how the elected host and the ownership state are adopted.

#### Scenario: A joiner receives the session's role policy

- **WHEN** a peer joins a session that declares a role policy
- **THEN** the session state it receives SHALL carry the default role and the identity-keyed memory
- **AND** the joiner SHALL apply the same assignment rule every other peer applies

#### Scenario: A session declaring no policy omits the section

- **WHEN** session state is built for a session that declares no role policy
- **THEN** the role policy section SHALL be omitted rather than sent empty

#### Scenario: Session state from an old peer cannot clear a policy

- **WHEN** session state arrives with no role policy section
- **THEN** the receiving peer SHALL leave its role policy unchanged

### Requirement: Role policy is scoped to the session and does not outlive it

Role policy SHALL live for the duration of the session and SHALL NOT be persisted to the broker or to external storage. If every peer leaves, the default role and the identity-keyed memory are lost with the session.

This is accepted rather than overlooked: a session organiser sets the policy for the session they are running, and the alternative — broker-held or externally-stored policy — adds an infrastructure dependency to a mechanism whose failure mode should be "behaves like today". Nothing needs redistributing when a session ends, only re-declaring.

#### Scenario: Policy does not survive an empty session

- **WHEN** every peer leaves a session that declared a role policy
- **AND** a new session is started with the same name
- **THEN** the new session SHALL carry no role policy until one is declared
- **AND** SHALL therefore assign `driver` to every joiner
