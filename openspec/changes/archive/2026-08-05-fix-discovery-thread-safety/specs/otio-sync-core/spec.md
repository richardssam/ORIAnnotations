## ADDED Requirements

### Requirement: Self-Election Is a Single Manager Operation

The sync manager SHALL expose one operation that performs self-election as
session master and owns every state transition that election entails: marking
this peer as the master, recording this peer's GUID as the session master GUID,
transitioning the session status to `SYNCED`, and announcing mastership with an
`I_AM_MASTER` message. Callers SHALL NOT elect by assigning the master flag, the
master GUID, or the session status directly; every self-election path — discovery
timeout, state-request timeout, and master failover — SHALL go through this one
operation.

The operation SHALL allow the caller to defer the `I_AM_MASTER` announcement, so
a host that must finish building its initial timelines before it can serve a
state request may apply the local election state immediately and announce
afterwards. When the announcement is deferred, the caller SHALL broadcast the
master response itself once that work completes.

The operation SHALL NOT change the messages a self-electing peer puts on the
wire, nor their order, relative to the previous per-caller election sequences.

#### Scenario: Solo peer self-elects

- **WHEN** a peer that has found no master performs the self-election operation
- **THEN** it SHALL report itself as the session master
- **AND** the session master GUID SHALL equal that peer's own GUID
- **AND** the session status SHALL be `SYNCED`
- **AND** exactly one `I_AM_MASTER` message carrying that peer's GUID SHALL be
  sent to the session

#### Scenario: Election announcement is deferred

- **WHEN** a peer performs the self-election operation with the announcement
  deferred
- **THEN** the master flag, master GUID, and `SYNCED` status SHALL be applied
  immediately
- **AND** no `I_AM_MASTER` message SHALL be sent until the caller broadcasts the
  master response

#### Scenario: Re-electing an existing master is inert locally

- **WHEN** the self-election operation runs on a peer that is already master and
  already `SYNCED`
- **THEN** it SHALL remain master with an unchanged master GUID
- **AND** the synced callbacks SHALL NOT be fired a second time

#### Scenario: Wire behaviour unchanged for existing peers

- **WHEN** a peer self-elects after its discovery timeout expires
- **THEN** the message content and ordering observed by other peers SHALL be
  identical to the prior per-caller implementation
- **AND** peers running older code SHALL interoperate without modification

#### Scenario: No caller mutates election state directly

- **WHEN** any host plugin or manager-internal path elects this peer as master
- **THEN** it SHALL invoke the self-election operation
- **AND** it SHALL NOT assign the master flag, master GUID, or session status at
  the call site
