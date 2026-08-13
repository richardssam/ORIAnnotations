## Purpose

Defines what a participant in a review session is permitted to emit, as a per-peer role — driver, reviewer, or viewer — enforced at the shared broadcast choke point so that a large session can be run without every participant being able to change what everyone else sees.

## ADDED Requirements

### Requirement: A participant holds exactly one of three session roles

Every peer SHALL hold exactly one session role: `driver`, `reviewer`, or `viewer`.

- A **driver** MAY emit every field group the protocol carries, subject to category authority.
- A **reviewer** MAY annotate and move within what is currently shown, and SHALL NOT change what is shown or modify session content.
- A **viewer** SHALL emit nothing session-visible, while continuing to receive all session state.

Role SHALL be a property of the participant, distinct from the three authority concepts that already exist: master (who holds the canonical session state), host (who chooses what the session looks at), and lease owner (who is emitting a given category right now). A session MAY contain several drivers and exactly one host, and a change of master SHALL NOT change any peer's role.

#### Scenario: A role is one of exactly three values

- **WHEN** a peer's role is read
- **THEN** it SHALL be one of `driver`, `reviewer`, or `viewer`
- **AND** no peer SHALL hold more than one role at a time

#### Scenario: Role is independent of master and host

- **WHEN** the session master changes, or a new host is elected
- **THEN** no peer's role SHALL change as a result

#### Scenario: Several peers may hold the driver role

- **WHEN** more than one peer in a session holds the `driver` role
- **THEN** all of them SHALL be permitted to emit driver-permitted field groups
- **AND** contention between them SHALL be resolved by the existing category ownership mechanism rather than by role

### Requirement: Role permits field groups, not message types

Role SHALL be evaluated against field groups, not against whole message types. The visibility boundary already runs inside the playback settings message, between the fields that say *what is shown* and the fields that say *where in it we are*, so a role table keyed on message type cannot express the reviewer tier at all.

The permitted field groups are:

| Field group | Category | Driver | Reviewer | Viewer |
|---|---|---|---|---|
| Playback settings — view mode / clip identity | visibility | Yes (host only) | No | No |
| Playback settings — current time / playing / playback mode | position | Yes | Yes | No |
| Display state (channel, exposure, pan/zoom) | position | Yes | Yes | Yes |
| Timeline add, remove, replace, rename | structure | Yes | No | No |
| Property set, structural child insert and remove | structure | Yes | No | No |
| Annotation strokes and annotation-track insert | annotation | Yes | Yes | No |
| Destructive annotation operations (clear all paint) | annotation | Yes | No | No |

Where a role forbids a field group, that group SHALL be stripped from the outgoing message rather than the message being withheld, and the call SHALL report the existing suppressed status — which already means "sent, with fields stripped" rather than "not sent". A message MAY have one field group stripped by role and another retained.

Stripping SHALL remove a whole field group. A message that retained the clip identity while dropping the view mode would still be asserting what the session looks at.

Where role stripping leaves a message carrying **no** permitted field group at all, that message SHALL NOT be emitted. An emptied playback message is not silence: it still carries the timeline it was sent about, which passive peers follow, and a receiver that reads an absent position field as a value reads the absence as an assertion of the first frame. Emitting it therefore lets a participant drive the very thing its role forbids. This applies to a message emptied by **role**; a message emptied by a category lease is the existing ownership behaviour and is unchanged.

Receiving peers SHALL treat an absent field group as *no assertion*, not as a default value. This is a property of every enforcement axis, not only role — a field group is absent precisely when its sender was not permitted to assert it.

Display state SHALL be permitted to every role including viewer, because it is per-peer presentation rather than a session event. This is a statement about role only; display state remains subject to its own category lease.

#### Scenario: A reviewer may scrub but may not change the shot

- **WHEN** a reviewer emits a playback settings message carrying both position and visibility field groups
- **THEN** the position fields SHALL be retained
- **AND** the visibility fields SHALL be stripped

#### Scenario: A viewer emits nothing session-visible

- **WHEN** a viewer scrubs, plays, annotates, or edits structure locally
- **THEN** no position, annotation, or structure field group SHALL leave that peer

#### Scenario: Every role may emit display state

- **WHEN** a viewer changes its own channel, exposure, or pan and zoom
- **THEN** the display state field group SHALL NOT be stripped on account of role

#### Scenario: A destructive annotation operation is driver-only

- **WHEN** a reviewer initiates an operation that clears annotations belonging to other participants
- **THEN** that operation SHALL be suppressed
- **AND** ordinary annotation strokes from the same reviewer SHALL continue to be emitted

#### Scenario: A message emptied by role is not emitted

- **WHEN** role stripping removes every field group from an outgoing playback message
- **THEN** no message SHALL be emitted
- **AND** no peer SHALL change its position or its view as a result

#### Scenario: An absent field group is not read as a value

- **WHEN** a peer receives a playback message carrying no position field group
- **THEN** it SHALL leave its own position and play state unchanged
- **AND** SHALL NOT interpret the absence as an assertion of the first frame

#### Scenario: A field group is stripped whole

- **WHEN** any field group is stripped by role
- **THEN** every field in that group SHALL be absent from the outgoing message
- **AND** no partial group SHALL be emitted

### Requirement: Role is a ceiling, category authority is a gate, and role is checked first

A broadcast SHALL pass both checks. Role decides what a participant may ever emit; category authority decides whether this peer is the one emitting it right now. A driver has permission to emit visibility; only the host actually does.

The role check SHALL be evaluated **before** the category authority check. A ceiling evaluated after a gate is not a ceiling, and category authority carries a side effect: holding a category is confirmed by a broadcast in that category actually going out, and a confirmed hold is the one state a competing claim will not preempt. Evaluating category first would let a role-blocked peer harden its hold on a category while the message it sent was stripped by role.

#### Scenario: Permission does not confer authority

- **WHEN** a driver that is not the elected host emits a visibility field group
- **THEN** that field group SHALL be stripped
- **AND** the reason SHALL be category authority, not role

#### Scenario: Authority does not confer permission

- **WHEN** a peer holds a category's lease and its role forbids that category
- **THEN** the field group SHALL be stripped

#### Scenario: A role-blocked broadcast does not confirm a category hold

- **WHEN** a peer's field group is stripped by the role check
- **THEN** that peer's hold on the corresponding category SHALL NOT be confirmed or extended by the attempt

### Requirement: A role that forbids a category forbids claiming it

A peer SHALL NOT claim a category's lease for a category its role forbids it from broadcasting. The claim SHALL be refused at the shared claim operation, on the same role table the broadcast guard uses.

This is required rather than merely tidy. Host applications invoke the claim operation unconditionally from every input-driven path, as they must, since they never test authority themselves; and non-drivers keep interacting locally by design. Without this rule a viewer's local scrubbing takes the position category from a driver, and can then never confirm it — because confirmation happens only when a broadcast in that category actually goes out, and role has stripped it. The category then sits held-but-unconfirmed for its full duration, and an earlier claim outranks the driver's fresh one, so position sync is dead for that interval every time a viewer touches its playhead.

A role-blocked claim SHALL be a refusal to claim, not a release. Releasing would let a non-driver's local activity take a category away from a driver, which is the same defect with its sign reversed.

Categories every role may broadcast SHALL remain claimable by every role.

#### Scenario: A viewer's local interaction does not take a category

- **WHEN** a viewer interacts locally in a way that would ordinarily claim the position category
- **THEN** no claim SHALL be emitted
- **AND** the peer that currently holds that category SHALL continue to hold it

#### Scenario: A refused claim does not release an existing hold

- **WHEN** a peer's claim is refused because its role forbids the category
- **THEN** any existing owner of that category SHALL be unaffected

#### Scenario: An ungated category stays claimable by every role

- **WHEN** a viewer interacts in a way that claims the display category
- **THEN** that claim SHALL proceed, because every role may emit display state

#### Scenario: Host applications are unchanged by the claim gate

- **WHEN** a host application handles local user input
- **THEN** it SHALL invoke the claim operation without consulting role
- **AND** the refusal SHALL happen inside the shared operation

### Requirement: Role reaches every peer by every path that carries a peer

Role SHALL travel by each path through which a peer becomes known to another: the peer's own announcements, and the peer set carried in session state. Role SHALL be carried as a field on the existing announcement rather than as a message of its own.

Both paths are required because host eligibility is evaluated against the peer table. A peer that has gone quiet becomes known to a joiner only through the peer set in session state, so a role omitted from that path leaves those peers role-less for up to the announcement interval — and an eligibility filter reading that table concludes the session has no drivers, which is not a delayed election but a false report of a driverless session.

A peer whose role is absent — because it is running code that predates roles, or because of the order in which it was learned — SHALL be treated as holding the session's default role. Absence SHALL NOT resolve to the most restrictive role.

#### Scenario: A joiner knows a quiet peer's role

- **WHEN** a peer joins a session containing a peer that has not announced since the joiner arrived
- **THEN** the joiner SHALL know that peer's role from the session state it received
- **AND** SHALL NOT have to wait for that peer's next announcement to evaluate host eligibility

#### Scenario: Both paths agree

- **WHEN** a peer learns another peer's role from session state
- **AND** subsequently receives that peer's own announcement
- **THEN** the role SHALL be unchanged by the second path

#### Scenario: A peer with no role is not treated as ineligible

- **WHEN** a peer table contains a peer carrying no role
- **THEN** that peer SHALL be treated as holding the session's default role
- **AND** a session that has not opted into a restrictive default SHALL elect its host exactly as it would without roles

#### Scenario: A role change propagates by re-announcement

- **WHEN** a peer's own role changes during a session
- **THEN** it SHALL re-announce
- **AND** other peers SHALL learn the new role through the same path as on joining

### Requirement: Role is enforced in the shared core, never in a host application

The role check SHALL live in the shared sync core, at the same broadcast choke point that already enforces category authority. No host application SHALL gate a broadcast on its own role.

Where a host application genuinely needs to know its role — to vary local behaviour or to label its own UI — it SHALL ask a single shared predicate, not reimplement the decision. The two host applications have measurably drifted on hand-replicated protocol behaviour, and role enforcement duplicated across them would drift the same way, silently.

#### Scenario: No host application tests its own role before broadcasting

- **WHEN** a host application emits any broadcast
- **THEN** it SHALL do so unconditionally
- **AND** the decision to strip SHALL be taken inside the shared core

#### Scenario: Role-dependent local behaviour goes through one predicate

- **WHEN** a host application varies local behaviour by role
- **THEN** it SHALL obtain the role from the shared core
- **AND** SHALL NOT derive it from application-specific state

### Requirement: Enforcement is send-side and depends on peer cooperation

Role SHALL be enforced by the sending peer suppressing its own outbound field groups. Receiving peers SHALL NOT validate the sender's role before applying a message, and no broker-side filtering SHALL be required.

The limit this places on the guarantee SHALL be stated rather than implied: a peer running modified or older code keeps broadcasting whatever it likes, and other peers will apply it. Role gates accidents in a cooperating session; it is not an access control and does not defend against a participant who does not cooperate. A panel that displays a peer's role is reporting what that peer declares, not what it is prevented from doing.

#### Scenario: A message is not rejected on receipt for the sender's role

- **WHEN** a peer receives a message whose sender's role would not have permitted it
- **THEN** the receiving peer SHALL apply it as it would any other message

#### Scenario: The limit of the guarantee is discoverable

- **WHEN** a user is shown a peer's role
- **THEN** the presentation SHALL NOT assert that the peer is prevented from emitting outside that role

### Requirement: A session with no eligible driver reports the condition and offers an exit

Restricting host eligibility to drivers makes a state reachable in which no peer is eligible to be host, so the session's view cannot be changed by anyone. The session SHALL both report that condition and provide a way out of it.

The condition SHALL be surfaced in the session state presentation. A frozen session that explains nothing is the failure the visibility authority requirements were written against, and it is reachable here by a new route.

A peer in that condition SHALL be offered an explicit action that sets its own role to `driver`. The action SHALL NOT claim host directly: host follows from the next election, which is a pure function of the peer table.

The action SHALL be offered **only** while no eligible driver is present. An always-available self-elevation would make a restrictive default advisory and the session's role memory a suggestion. Outside that condition a role comes from the session's memory or from an administrator.

Two peers taking the action at the same time SHALL be safe: both become drivers, and host election resolves onto one of them deterministically.

#### Scenario: The driverless condition is reported

- **WHEN** the peer table contains no peer eligible to be host on account of role
- **THEN** the session state presentation SHALL report that condition

#### Scenario: Self-elevation is available only in the driverless condition

- **WHEN** at least one eligible driver is present in the peer table
- **THEN** the self-elevation action SHALL NOT be available

#### Scenario: Self-elevation grants the role and lets election do the rest

- **WHEN** a peer takes the self-elevation action while no eligible driver is present
- **THEN** its own role SHALL become `driver`
- **AND** it SHALL NOT assign itself host directly
- **AND** the next host election SHALL resolve onto it

#### Scenario: Simultaneous self-elevation converges

- **WHEN** two peers take the self-elevation action before either has learned of the other
- **THEN** both SHALL hold the `driver` role
- **AND** every peer SHALL converge on the same elected host
