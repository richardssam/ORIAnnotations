## ADDED Requirements

### Requirement: A refused structural broadcast reports that it was refused

The structural broadcast operations SHALL report to their caller whether the
patch was emitted or refused, using the same status vocabulary as the playback
broadcast path.

Today a refusal is a log line and an early return, indistinguishable from
success to the caller. Divergence recovery is triggered by the refusal, so the
refusal has to be a value the caller can act on. Reusing the existing status
vocabulary keeps one meaning of `SUPPRESSED` across the codebase; where a
structural patch is refused outright rather than emitted with fields removed,
the distinction between "withheld" and "sent, reduced" SHALL be reportable.

#### Scenario: A role-refused structural patch reports refusal
- **WHEN** a structural broadcast is refused because the peer's role forbids the category
- **THEN** the operation SHALL report the refusal to its caller

#### Scenario: An emitted structural patch reports that it was sent
- **WHEN** a structural broadcast is emitted
- **THEN** the operation SHALL report that it was sent

#### Scenario: Existing callers are unaffected by the report
- **WHEN** a caller ignores the reported status
- **THEN** its behaviour SHALL be unchanged from before this requirement

### Requirement: State may be re-requested from the synchronised state

A peer SHALL be able to request the session's state while synchronised, and
apply the resulting snapshot, without first leaving the session or re-entering
discovery.

The existing request path is reachable only from joining, which reflects an
assumption that a peer's state can only be wrong before it has one. Divergence
recovery breaks that assumption. A peer that has to leave the session to repair
itself loses its host election standing and any leases it holds, and reappears
to every other peer as a departure and a rejoin.

A re-request SHALL buffer and replay concurrent deltas on the same terms as a
joining peer's request, so material broadcast while the snapshot is in flight is
not lost.

#### Scenario: A synchronised peer requests state
- **WHEN** a synchronised peer requests the session's state
- **THEN** the request SHALL be sent
- **AND** the peer SHALL NOT be reported to other peers as having left

#### Scenario: Deltas arriving during a re-request are replayed
- **WHEN** a structural or annotation message arrives while a re-requested snapshot is in flight
- **AND** that message is newer than the snapshot
- **THEN** it SHALL be applied after the snapshot

#### Scenario: An unanswered re-request returns the peer to synchronised
- **WHEN** a re-requested snapshot does not arrive within the session's timeout
- **THEN** the peer SHALL return to the synchronised state
- **AND** SHALL NOT enter discovery

#### Scenario: Serving a mid-session request is indistinguishable from serving a join
- **WHEN** a peer receives a state request from a peer that is already in the session
- **THEN** it SHALL answer it exactly as it answers a joining peer's request
