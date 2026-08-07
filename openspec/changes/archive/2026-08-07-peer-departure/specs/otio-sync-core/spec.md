## ADDED Requirements

### Requirement: A departing peer is removed from the peer table

The sync manager SHALL remove a peer from the peer table that feeds host
election when that peer leaves the session, whether it leaves cleanly or not.

A peer that has left SHALL NOT continue to be treated as a candidate for any
role elected from that table. Without removal the table is append-only, and an
authority elected onto a departed peer can never move.

Removal SHALL go through the single existing removal operation, which owns the
transition and re-runs host election, rather than through direct mutation of the
table by either detection path.

#### Scenario: A peer that disconnects cleanly is removed promptly

- **WHEN** a peer disconnects from the session in the normal way
- **THEN** every other peer SHALL remove it from the peer table
- **AND** SHALL do so without waiting for a liveness timeout

#### Scenario: A peer that vanishes without notice is removed eventually

- **WHEN** a peer stops participating without signalling departure — a crash,
  a killed process, or a lost network
- **THEN** every other peer SHALL remove it from the peer table within a bounded
  time
- **AND** the outcome SHALL be the same as for a clean disconnect

#### Scenario: Removal is a single operation

- **WHEN** a peer is removed by either detection path
- **THEN** removal SHALL go through one shared operation
- **AND** that operation SHALL re-run host election as part of the removal

### Requirement: Peer liveness is established by periodic announcement

Each peer SHALL re-announce its presence periodically, so that the absence of
announcements is meaningful evidence that a peer has gone.

Liveness SHALL NOT be inferred from a peer's other traffic. A peer that is
present but idle — observing a session without scrubbing, annotating, or
editing — emits nothing, and MUST NOT be removed on that basis.

A periodic announcement SHALL NOT request answers from other peers, so that
re-announcement cannot produce an answer cascade.

A peer removed for inactivity SHALL be restored by its next announcement, so a
peer wrongly removed after a temporary stall recovers without rejoining.

#### Scenario: An idle peer is not removed

- **WHEN** a peer is present but sends no playback, annotation, or structural
  messages for longer than the liveness timeout
- **THEN** it SHALL remain in every other peer's peer table
- **AND** SHALL remain eligible for election

#### Scenario: Re-announcement does not cascade

- **WHEN** a peer re-announces its presence periodically
- **THEN** receiving peers SHALL NOT answer that announcement
- **AND** the message volume SHALL grow no faster than the number of peers

#### Scenario: A wrongly removed peer restores itself

- **WHEN** a peer is removed for inactivity but is in fact still present
- **THEN** its next announcement SHALL restore it to the peer table
- **AND** host election SHALL be re-run against the restored table

### Requirement: Departure is signalled from the shared session layer

The departure signal SHALL be emitted by the shared session manager when a
session is closed, not by each host application's own disconnect path.

Both host applications already route their disconnect through that single close
operation. Emitting from each application instead would duplicate protocol
behaviour across two separately-written paths, which is where these two hosts
have already drifted, and the failure would be silent — one application
announcing its exit and the other not.

Loss of the departure signal SHALL degrade to the inactivity path rather than
leaving the peer present indefinitely.

#### Scenario: Both applications signal departure without application-specific code

- **WHEN** either host application disconnects from a session
- **THEN** a departure signal SHALL be emitted
- **AND** neither application SHALL implement that emission itself

#### Scenario: A lost departure signal still resolves

- **WHEN** a departure signal is not delivered
- **THEN** the departing peer SHALL still be removed by the inactivity path

### Requirement: A joining peer learns the peer set from session state

The session state a joining peer is given SHALL carry the current peer set,
alongside the elected host it already carries, so the joiner learns who is
present from the message it already requests.

Peers SHALL NOT answer one another's announcements in order to make a joiner
aware of them. That answering behaviour is the only step in the peer protocol
whose message count grows with the number of peers, and periodic announcement
makes it redundant: a joiner learns a silent peer from that peer's next
announcement regardless of whether anyone answered.

A joiner that receives no session state SHALL still learn the peer set from
subsequent announcements, within the announcement interval. Session state SHALL
NOT be the only means by which a peer becomes discoverable.

#### Scenario: A joiner learns existing peers without an answer cascade

- **WHEN** a peer joins a session and receives session state
- **THEN** that state SHALL identify the peers currently present
- **AND** no peer SHALL emit an announcement in response to the joiner's own

#### Scenario: Join cost does not grow with session size

- **WHEN** a peer joins a session
- **THEN** the number of messages emitted in response SHALL NOT grow with the
  number of peers already present

#### Scenario: A joiner that receives no session state still discovers peers

- **WHEN** a peer joins a session in which no session state is sent to it
- **THEN** it SHALL still learn every present peer from their periodic
  announcements
- **AND** SHALL do so within the announcement interval

#### Scenario: Every peer converges on the same peer set

- **WHEN** a joiner adopts the peer set from session state
- **AND** subsequently receives announcements from those peers
- **THEN** its peer table SHALL agree with the sender's
- **AND** host election evaluated against it SHALL reach the same host

### Requirement: Peer departure does not alter master election

Removal of a peer from the peer table SHALL NOT change how the session master is
elected or how master failover is detected. Master remains a state-sync role
with its own failover mechanism, distinct from any role elected from the peer
table.

#### Scenario: A departing non-master peer leaves mastership untouched

- **WHEN** a peer that is not the master departs
- **THEN** the session master SHALL be unchanged
- **AND** no master failover SHALL be triggered by the departure
