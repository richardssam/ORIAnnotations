## ADDED Requirements

### Requirement: Role stripping mirrors visibility and position field stripping

The authority module SHALL provide role-based field-group stripping matching the shape of the existing visibility and position stripping, and the manager SHALL apply it from the same broadcast choke point, ahead of both existing enforcement steps.

A single broadcast SHALL be able to have its field groups stripped by role, by visibility authority, by the position lease, by any combination, or by none, independently. The existing suppressed status SHALL retain its present meaning — "sent, with some fields stripped" — and SHALL NOT be redefined to mean "not sent".

Stripping SHALL happen in one core function rather than at call sites, so that no host application and no individual broadcast method reimplements the decision.

#### Scenario: Role strips a field group ahead of category authority

- **WHEN** a peer whose role forbids the structure category emits a structural broadcast
- **THEN** the structure field group SHALL be stripped by the role check
- **AND** the category authority check SHALL NOT be reached for that field group

#### Scenario: A message loses one field group to role and keeps another

- **WHEN** a peer holding the `reviewer` role emits a playback message carrying both position and visibility field groups
- **THEN** the visibility fields SHALL be stripped and the position fields retained
- **AND** the call SHALL report the suppressed status

#### Scenario: A role-stripped broadcast does not confirm a lease

- **WHEN** a peer's field group is stripped by the role check
- **THEN** the lease-confirmation path for that category SHALL NOT run

#### Scenario: A permissive role policy strips nothing

- **WHEN** every peer holds the `driver` role
- **THEN** no field group SHALL be stripped by the role check
- **AND** the sequence of enforcement calls SHALL leave every outgoing message as it was before role was introduced

### Requirement: The peer table carries each peer's role by both population paths

`SyncManager`'s peer table SHALL carry a role for each peer, populated by both paths that already populate it: the periodic peer announcement, and the peer roster carried in session state.

The announcement SHALL carry role as an additional field beside the application and capability fields it already carries. No separate role message SHALL be introduced: the announcement already exists, already reaches every peer, and already serves as the liveness heartbeat, so a second message would duplicate it with its own cadence and its own storm risk.

A peer entry carrying no role SHALL resolve to the session's default role wherever role is read, rather than being treated as ineligible or as holding the most restrictive role.

#### Scenario: An announcement carries the sender's role

- **WHEN** a peer announces its presence
- **THEN** the announcement SHALL carry that peer's role alongside its application and capabilities

#### Scenario: An adopted peer carries its role

- **WHEN** a joining peer adopts the peer roster from session state
- **THEN** each adopted entry SHALL carry that peer's role
- **AND** host election evaluated against the adopted table SHALL reach the same result as one evaluated after those peers announce

#### Scenario: A roster written by older code does not empty the candidate set

- **WHEN** a peer roster arrives carrying no role for its entries
- **THEN** each entry SHALL resolve to the session's default role
- **AND** host election SHALL NOT report the session as having no eligible driver on that basis

### Requirement: STATE_SNAPSHOT carries session role policy for late joiners

`StateSnapshot` SHALL gain a role policy section carrying the session's default role and its identity-keyed role memory. The section SHALL follow the same backwards-compatibility convention already established for the elected host and the ownership section on this message: it SHALL be omitted from the payload when the session declares no role policy, and a null or absent value on receipt SHALL be ignored rather than interpreted as an empty policy — so a peer running code that predates roles cannot clear a session's role policy by sending or relaying a snapshot.

Adoption of received role policy SHALL go through a single named operation, mirroring the existing host and ownership adoption operations, rather than direct assignment to local policy state.

#### Scenario: A snapshot reports the session's role policy

- **WHEN** a peer builds a snapshot for a session that declares a role policy
- **THEN** the role policy section SHALL carry the default role and the identity-keyed role memory

#### Scenario: A session with no policy omits the section

- **WHEN** a peer builds a snapshot for a session that declares no role policy
- **THEN** the role policy section SHALL be omitted rather than sent empty

#### Scenario: A snapshot from an old peer cannot clear a policy

- **WHEN** a snapshot arrives with no role policy section
- **THEN** the receiving peer SHALL leave its role policy unchanged

### Requirement: Master self-election prefers a driver without introducing a timing window

Self-election as session master SHALL rank candidate peers so that a peer holding the `driver` role is preferred, because the master holds the session's canonical state and needs full broadcast capability to serve it.

The preference SHALL be expressed as a ranking evaluated at the existing discovery timeout, and SHALL NOT introduce a new wall-clock deferral in which a non-driver waits to see whether a driver appears. Self-election SHALL remain one operation owning every transition it entails, reached from its existing named callers.

The preference SHALL be a preference and not a restriction: where no driver is available, a peer holding another role SHALL still self-elect, so that a session always has a master. A peer promoted to master on that basis SHALL be master for state synchronisation only, and SHALL NOT thereby acquire the `driver` role.

#### Scenario: A driver is preferred as master

- **WHEN** self-election is evaluated and a peer holding the `driver` role is available
- **THEN** the ranking SHALL prefer that peer

#### Scenario: A session without a driver still has a master

- **WHEN** self-election is evaluated in a session containing no driver
- **THEN** a peer SHALL still self-elect as master
- **AND** its role SHALL be unchanged by that election

#### Scenario: No new deferral is introduced

- **WHEN** a peer's discovery timeout expires
- **THEN** self-election SHALL be decided at that point
- **AND** no additional waiting period SHALL be introduced by the role preference

### Requirement: Role and driverless state are exposed to the test inspector

The role held by this peer, the elected host, and whether the session currently has any eligible driver SHALL be readable through the test inspection surface, alongside the host and ownership state it already exposes.

Fields exposed for inspection SHALL be declared in the test runner's ignored-key set where they are not part of state comparison, following the convention established when host authority state was first exposed.

#### Scenario: A test can assert on role without reaching into internals

- **WHEN** a test inspects a running peer
- **THEN** it SHALL be able to read that peer's role, the elected host, and whether an eligible driver is present

#### Scenario: Newly exposed fields do not break state comparison

- **WHEN** the inspector exposes role and driverless state
- **THEN** existing state comparisons SHALL be unaffected
