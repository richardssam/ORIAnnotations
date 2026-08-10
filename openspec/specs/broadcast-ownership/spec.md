# broadcast-ownership

## Purpose
Give the categories of sync traffic that remain multi-writer — position and structure — a distributed write lease, so that only one peer at a time broadcasts a given category and the feedback loops today's ad hoc time-window guards suppress cannot occur in the steady state.

## Requirements

### Requirement: A lease gates broadcast of the position and structure categories
Before a peer broadcasts fields belonging to the **position** or **structure** category, it SHALL hold an ownership lease for that category. A peer that does not hold the lease SHALL NOT broadcast those fields. The **visibility** category SHALL NOT gain a lease, because it is already a static single writer and cannot contend with itself. The **annotation** category SHALL NOT gain a lease, remaining multi-writer by design.

Display state (channel, exposure, pan/zoom) SHALL be gated by its own lease, independent of the position lease, even though it remains part of the position category for the purpose of who is permitted to emit it at all.

#### Scenario: A peer without the lease does not broadcast the gated category
- **WHEN** a peer attempts to broadcast position or structure fields while it does not hold that category's lease
- **THEN** those fields SHALL NOT be broadcast

#### Scenario: The lease holder broadcasts freely
- **WHEN** a peer holds the lease for a category
- **THEN** it SHALL broadcast that category's fields without restriction

#### Scenario: The display lease is independent of the position lease
- **WHEN** a peer holds the display lease but not the position lease, or vice versa
- **THEN** it SHALL be able to broadcast the category it holds and SHALL be gated on the category it does not

### Requirement: A category has exactly one owner at a time, converged deterministically
At any instant, every peer's view of who owns a leased category SHALL agree, even though ownership is tracked independently by each peer with no central authority. A peer wanting to broadcast a leased category it does not own SHALL claim it by broadcasting a claim carrying a claim timestamp and its own peer identity. Every peer SHALL resolve a claim by the same rule: the earliest claim timestamp wins; exact ties break to the lower peer identity.

#### Scenario: Two peers claim the same free category at once
- **WHEN** two peers each broadcast a claim for the same category while it is free, close enough in time that each sees the other's claim after its own
- **THEN** every peer, including the two claimants, SHALL resolve the same one of the two as owner

#### Scenario: A claim against a live lease is queued, not granted
- **WHEN** a peer claims a category currently owned by a different peer whose lease has not expired
- **THEN** ownership SHALL NOT transfer immediately
- **AND** the claim SHALL be recorded as pending

### Requirement: An idle lease expires and transfers to a pending claimant
Each broadcast within a category SHALL refresh that category's lease. A lease SHALL expire after a bounded period of silence from its owner. On expiry, if a pending claim exists, ownership SHALL transfer to the pending claimant; otherwise the category SHALL become free. An owner that disconnects SHALL lose the lease through this same expiry path, with no separate disconnect-handling rule.

#### Scenario: An active owner keeps the lease
- **WHEN** a peer holding a lease continues to broadcast within that category
- **THEN** the lease SHALL NOT expire and SHALL NOT transfer to a pending claimant

#### Scenario: A silent owner's lease frees or transfers
- **WHEN** a peer holding a lease broadcasts nothing in that category for longer than the lease's expiry period
- **THEN** the lease SHALL expire
- **AND** if a pending claim exists, ownership SHALL transfer to that claimant; otherwise the category SHALL become free

#### Scenario: A disconnected owner's lease is reclaimable
- **WHEN** the peer holding a lease leaves the session
- **THEN** the lease SHALL expire through the same silence-based path
- **AND** another peer SHALL be able to claim the category once it does

### Requirement: An active owner is not interrupted mid-operation
A pending claim SHALL NOT preempt a lease that is still being actively refreshed. The current owner SHALL keep the lease until it falls silent for the expiry period, even while another peer's claim is pending.

#### Scenario: A queued claim waits for the owner to go idle
- **WHEN** peer A owns a category and continues broadcasting within it, and peer B's claim is pending
- **THEN** peer A SHALL retain ownership until it stops broadcasting for the expiry period
- **AND** peer B SHALL NOT receive ownership while A remains active

### Requirement: Claiming is triggered only by local user input
A peer SHALL claim a category only in response to input that peer's own user produced, never automatically as a side effect of a suppressed broadcast or of applying a remote message. Applying a remote message can trigger local events indistinguishable in shape from user action; treating those as claim triggers would let ownership cycle back to a non-driving peer at the next lease expiry, reproducing the same feedback loop at expiry speed instead of per-message.

A suppressed broadcast SHALL NOT queue its payload for later replay. When a peer is later granted ownership, it SHALL broadcast its current state if its user is still interacting, or nothing at all.

#### Scenario: A suppressed broadcast does not claim
- **WHEN** a peer's broadcast is suppressed because it does not hold the category's lease
- **THEN** that suppression SHALL NOT itself trigger a claim

#### Scenario: Applying a remote message does not claim
- **WHEN** a peer applies a message it received from the category's current owner
- **AND** that application causes local events that resemble user-driven state changes
- **THEN** no claim SHALL be triggered by those events

#### Scenario: A deferred grant does not replay stale state
- **WHEN** a pending claim is promoted to ownership after its peer's user has stopped interacting
- **THEN** the newly granted owner SHALL NOT broadcast a stale payload captured at claim time

### Requirement: A late joiner learns the current ownership view instead of assuming free categories
The session state a joining peer receives SHALL carry, for each leased category, the current owner's identity and the lease's remaining time, using the same convention as other authority state carried in that channel: the section SHALL be omitted when no session state carries it, and a received absence SHALL be ignored rather than treated as "no owner" — so a peer that predates ownership cannot clear another peer's held lease by joining.

#### Scenario: A joiner adopts the current owner instead of claiming immediately
- **WHEN** a peer joins a session in which another peer already owns a leased category
- **THEN** the joiner SHALL learn that ownership from the session state it receives
- **AND** SHALL NOT claim that category itself while the reported lease remains live

#### Scenario: An old peer's session state cannot clear a held lease
- **WHEN** a session state arrives with no ownership section, sent by a peer that predates this mechanism
- **THEN** the receiving peer SHALL NOT interpret that absence as every category becoming free

### Requirement: Enforcement is reversible by a single runtime switch
A runtime switch SHALL be able to fully disable lease enforcement, restoring the prior behaviour of unconditional broadcast for the position and structure categories. The switch SHALL be read at each enforcement check rather than cached, so it can be changed in a running process, and disabling it SHALL revert enforcement completely rather than leaving part of the mechanism active against a policy no longer in force.

#### Scenario: The switch reverts enforcement completely
- **WHEN** the runtime switch is set to disable ownership enforcement
- **THEN** peers SHALL broadcast position and structure fields regardless of lease state
- **AND** no inference or gating derived from lease ownership SHALL remain active

#### Scenario: The switch takes effect without a restart
- **WHEN** the runtime switch is changed while peers are connected
- **THEN** the new enforcement state SHALL take effect on each peer's next broadcast check, without requiring a reconnect

### Requirement: Ownership messages are backwards compatible
A peer that does not understand claim or release messages SHALL be able to ignore them without error and continue participating in the session using its existing broadcast behaviour.

#### Scenario: An old peer ignores ownership messages
- **WHEN** a peer running code that predates this mechanism receives a claim or release message
- **THEN** it SHALL ignore the message without error
- **AND** SHALL continue to broadcast as it did before
