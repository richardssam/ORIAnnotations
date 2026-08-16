# session-role-administration Specification

## Purpose
Defines how a participant's session role is changed while a session is running — who may issue a grant, how the grant is carried, which peer applies it, and how it reaches the session's identity-keyed role memory on every peer so that it survives the target's reconnection.
## Requirements
### Requirement: Any peer holding the driver role may grant a role

A peer holding the `driver` role SHALL be permitted to grant any recognised role to any participant in the session, including itself and including another driver. A peer holding `reviewer` or `viewer` SHALL NOT.

The permission SHALL be evaluated against the same role table the broadcast guard and the lease-claim gate already consult, so that "who may administer" cannot drift from "who may drive".

Administration is deliberately **not** restricted to the elected host. Host is an election outcome — application preference first, then GUID ascending — so restricting role administration to it would mean the person who may staff a session is decided by which application each participant happened to launch, not by anyone's choice. A session that wants a dedicated administrator alongside the person driving the review expresses that as two drivers.

Several drivers issuing grants concurrently SHALL be safe rather than ordered: because a grant is applied by its target, each participant is a single serialisation point for grants about themselves. The winner between two concurrent grants is arbitrary; the outcome is identical on every peer.

#### Scenario: A driver grants a role

- **WHEN** a peer holding the `driver` role issues a grant naming another participant and a role
- **THEN** the grant SHALL be emitted to the session

#### Scenario: A non-driver may not grant

- **WHEN** a peer holding `reviewer` or `viewer` attempts to issue a grant
- **THEN** no grant SHALL be emitted
- **AND** no peer's role SHALL change as a result

#### Scenario: A driver that is not host may grant

- **WHEN** a driver that is not the elected host issues a grant
- **THEN** the grant SHALL be emitted
- **AND** SHALL NOT be refused on account of the issuer not being host

#### Scenario: Concurrent grants converge

- **WHEN** two drivers issue conflicting grants for the same participant before either has learned of the other
- **THEN** every peer SHALL converge on the same resulting role for that participant
- **AND** that role SHALL be the one the target itself announced

### Requirement: A grant names a participant identity, not a peer GUID

A grant SHALL identify its target by the participant's account identity — the same key the session's role memory is held under — and SHALL NOT identify it by peer GUID.

GUID-addressed grants would fail at the case the identity-keyed memory exists to serve: a target that reconnects arrives with a new GUID, and a grant recorded against the old one would be unreachable. Addressing by identity also means one participant working from two machines is granted a role on both, which is the same rule the session's memory already applies.

A peer that carries no identity SHALL NOT be a valid target, and a grant naming no participant SHALL have no effect. Such a peer is running code that predates identity; there is no key to remember it under, so the grant could not survive its reconnection and the panel SHALL NOT offer the control for it.

#### Scenario: A grant reaches the target's every peer

- **WHEN** a participant is joined to the session from two machines
- **AND** a driver grants that participant a role
- **THEN** both of that participant's peers SHALL hold the granted role

#### Scenario: A grant survives the target's reconnection

- **WHEN** a participant is granted the `driver` role in a session whose default role is `viewer`
- **AND** that participant leaves and rejoins with a new peer GUID
- **THEN** it SHALL be assigned `driver` from the session's memory
- **AND** SHALL NOT be assigned the session default

#### Scenario: A peer without identity cannot be targeted

- **WHEN** the session contains a peer carrying no identity
- **THEN** no role control SHALL be offered for it
- **AND** a grant naming no participant SHALL leave every peer's role unchanged

### Requirement: A grant is applied by its target, which then re-announces

The peer whose identity a grant names SHALL be the peer that applies it to its own role. Every other peer SHALL learn the new role from that target's subsequent announcement, exactly as it learns a role on joining.

No peer SHALL write another peer's role into its own peer table on receipt of a grant. The announcement remains the single write path into the peer table; a grant that wrote the table directly would create a second, racing writer for a value the target is also announcing, and the two would disagree whenever a grant and an announcement crossed.

A grant naming a role the target already holds SHALL be a no-op: no role change, and no announcement. Otherwise a control that reasserts the current role would emit an announcement per click.

#### Scenario: The target applies the grant

- **WHEN** a peer receives a grant naming its own participant identity and a role it does not currently hold
- **THEN** its own role SHALL become the granted role
- **AND** it SHALL announce itself

#### Scenario: Other peers learn the role by announcement

- **WHEN** a peer receives a grant naming a participant other than itself
- **THEN** it SHALL NOT write that participant's role into its peer table
- **AND** it SHALL learn the new role from the target's announcement

#### Scenario: A redundant grant is silent

- **WHEN** a peer receives a grant naming its own identity and the role it already holds
- **THEN** no announcement SHALL be emitted

### Requirement: A grant is broadcast, and every peer merges it into the session's role memory

A grant SHALL be delivered to every peer in the session rather than only to its target, and every peer receiving it SHALL merge the participant-to-role pair into its own copy of the session's identity-keyed role memory.

Merging on every peer is what makes the grant durable. Role policy is carried only in the session state a joiner is sent, and that state is built by the master alone. A grant that landed only in the issuer's memory would never reach the master, so it would be absent from the state every later joiner receives, and it would evaporate the moment the target's network hiccuped — failing at precisely the case identity-keyed memory exists to serve.

Broadcasting is preferred over routing grants through the master for the same reason the memory is identity-keyed rather than GUID-keyed: it removes a dependency on transient session state. A master-routed grant has a window during a master election in which there is nobody to route to; a broadcast has no such window.

The merge SHALL be additive: receiving a grant SHALL NOT remove any other participant's remembered role.

#### Scenario: The master's memory is current without a routing hop

- **WHEN** a driver that is not the master grants a participant a role
- **AND** a new peer subsequently joins and receives session state
- **THEN** the session state it receives SHALL carry the granted role
- **AND** a participant joining under that identity SHALL be assigned it

#### Scenario: Every peer records the grant

- **WHEN** a grant is delivered to a peer that is neither its issuer nor its target
- **THEN** that peer's copy of the session's role memory SHALL record the granted role for that participant

#### Scenario: A grant does not clear other remembered roles

- **WHEN** a grant is applied to a session that already remembers roles for other participants
- **THEN** those participants' remembered roles SHALL be unchanged

### Requirement: A granted role takes effect on enforcement and on host eligibility

A target that applies a grant SHALL enforce the new role on its own subsequent broadcasts and lease claims immediately, without restarting and without rejoining the session.

Because host eligibility is evaluated against the peer table, a grant that changes a peer's role SHALL cause host election to be re-evaluated once the change is known. Demoting the peer that currently holds visibility authority therefore moves the host to another eligible driver, or leaves the session in the driverless condition, rather than leaving authority with a peer no longer permitted to exercise it.

#### Scenario: A demoted driver stops emitting

- **WHEN** a driver is granted `viewer` and applies the grant
- **THEN** its subsequent position, annotation, and structure field groups SHALL be stripped
- **AND** it SHALL NOT claim a category its new role forbids

#### Scenario: A promoted viewer may drive

- **WHEN** a viewer is granted `driver` and applies the grant
- **THEN** its subsequent broadcasts SHALL NOT be stripped on account of role

#### Scenario: Demoting the host moves host

- **WHEN** the elected host is granted a role that forbids visibility
- **AND** another eligible driver is present
- **THEN** host SHALL be re-elected onto that driver

### Requirement: Granting is distinct from self-elevation and does not relax its gate

The action by which a peer sets its **own** role to `driver` to escape a session with no eligible driver SHALL remain available only while that condition holds. Adding an administrator-issued grant SHALL NOT make self-elevation available at any other time.

The two are separate mechanisms with separate gates and SHALL stay separable in the presentation: one is a recovery from a deadlock, the other is a decision by a driver about someone else. Offering them from the same panel makes the distinction easy to blur, which is why it is stated rather than left to be inferred.

A grant SHALL NOT change the session's default role. One participant being promoted is not a decision about every future joiner.

#### Scenario: A grant does not unlock self-elevation

- **WHEN** a session contains at least one eligible driver
- **THEN** the self-elevation action SHALL remain unavailable to every peer
- **AND** a driver granting itself a role SHALL NOT be treated as self-elevation

#### Scenario: A grant leaves the default untouched

- **WHEN** a driver grants a participant the `driver` role in a session whose default role is `viewer`
- **THEN** the session's default role SHALL remain `viewer`
- **AND** a subsequent joiner the session does not recognise SHALL be assigned `viewer`

### Requirement: The last driver may be demoted, and the driverless exit is the recovery

No refusal SHALL be added for a grant that would leave the session with no eligible driver.

Such a guard would have to be evaluated against a peer table that can be stale — a driver may have departed without its notice arriving — so it would refuse legitimate grants and still admit the case it was written for. The condition it would guard against is already reported in both session panels, and the self-elevation action is already offered as the exit.

The panel SHOULD confirm before issuing a grant that would demote the last driver. That is a courtesy in the view; it SHALL NOT be relied on as the mechanism, and no other component SHALL assume a driver is always present.

#### Scenario: Demoting the last driver is permitted

- **WHEN** the only driver in a session is granted `viewer`
- **THEN** the grant SHALL be applied

#### Scenario: The resulting condition is reported and recoverable

- **WHEN** a session is left with no eligible driver by a grant
- **THEN** both session panels SHALL report the driverless condition
- **AND** the self-elevation action SHALL become available

### Requirement: Who may grant is enforced by the issuer and depends on peer cooperation

The check on the issuer's role SHALL be made by the issuing peer, in the shared sync core, before the grant is emitted. Receiving peers SHALL NOT validate the issuer's role before applying a grant, and no broker-side filtering SHALL be required.

This matches the enforcement model role already uses everywhere else, and its limit SHALL be stated rather than implied: a peer running modified or older code can emit a grant its role does not permit, and cooperating peers will apply it. Role administration gates accidents in a cooperating session; it is not access control.

No host application SHALL make the decision itself. Where a host needs to know whether to *offer* the control, it SHALL ask the shared projection rather than deriving the answer from its own state.

#### Scenario: The refusal happens in the core

- **WHEN** a host application invokes the grant operation on behalf of a user
- **THEN** the decision to emit or refuse SHALL be taken inside the shared core
- **AND** the host application SHALL NOT test its own role first

#### Scenario: A grant is not rejected on receipt for the issuer's role

- **WHEN** a peer receives a grant whose issuer's role would not have permitted it
- **THEN** the receiving peer SHALL apply it as it would any other grant

### Requirement: The grant is an additive protocol message

The grant SHALL be carried as a message of its own, distinct from the announcement that propagates the resulting role.

A peer running code that predates this change SHALL ignore the message and SHALL continue to hold whatever role it resolved, without error and without leaving the session. A session in which nobody issues a grant SHALL behave exactly as it does without this change.

#### Scenario: An older peer ignores the message

- **WHEN** a grant is delivered to a peer running code that predates it
- **THEN** that peer SHALL ignore the message
- **AND** SHALL remain in the session with its existing role

#### Scenario: A session with no grants is unchanged

- **WHEN** no grant is issued for the life of a session
- **THEN** every peer's role SHALL be exactly what the session's policy assigns
