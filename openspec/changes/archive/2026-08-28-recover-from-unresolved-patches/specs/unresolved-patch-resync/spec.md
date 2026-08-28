## ADDED Requirements

### Requirement: A structural patch that stays unapplied triggers a state request

When a peer receives a structural patch whose target is not in its object map, and that target is still absent after a settle window, the peer SHALL request state from the master.

A peer in this position holds a session that is missing structure the rest of the session has. It cannot repair itself from what it holds, because the very message that would have told it what it is missing is the one it could not apply. Asking the master is the only move available to it.

The request SHALL reuse the session's existing recovery path — the same `request_state(reason="recovery")` and the same cooldown that governs structural recovery — so that a peer cannot issue recovery requests from two independent triggers faster than the session's rate limit allows.

#### Scenario: A patch against an unknown timeline resyncs the peer

- **WHEN** a peer receives `INSERT_CHILD` naming a target it does not hold
- **AND** the target is still not in its object map when the settle window expires
- **THEN** the peer SHALL request state from the master
- **AND** SHALL hold the inserted media once the request is answered

#### Scenario: A peer that was merely behind does not request state

- **WHEN** a peer receives a patch naming a target it does not yet hold
- **AND** the target arrives before the settle window expires
- **THEN** the peer SHALL NOT request state

This is the case `structure-divergence-recovery` warns about: an unresolved patch on its own cannot distinguish a diverged peer from one that has not caught up. Waiting is what separates them, because only one of the two clears on its own.

### Requirement: An unapplied patch does not mark the peer diverged

A peer SHALL NOT record structural divergence on the basis of a patch it could not apply.

`structure-divergence-recovery` declares divergence from the attempt to change structure, never from observed mismatch, so that "diverged" means one thing and is trustworthy where it is surfaced to the user. A receiving peer has made no such attempt: it has been told about a change, not made one. Requesting state is idempotent and costs a message; declaring divergence changes what the session reports about this peer and is visible in the UI.

#### Scenario: Resync leaves the divergence state alone

- **WHEN** a peer requests state because a patch stayed unapplied
- **THEN** it SHALL NOT be reported as structurally diverged
- **AND** any divergence state it already held SHALL be unaffected

### Requirement: Repeated unapplied patches produce one request

A burst of patches against the same missing target SHALL produce a single state request, not one per patch.

A peer that is missing a timeline will typically fail to apply every patch that names it — a media add can arrive as many children in sequence. One request repairs all of them, and the answer is a full snapshot regardless of how many patches prompted it.

#### Scenario: A burst of inserts against one missing target requests once

- **WHEN** a peer receives several structural patches naming the same absent target within the settle window
- **THEN** it SHALL issue one state request

### Requirement: A peer with no master to ask stays unrepaired and says so

When a peer has no known master, or is itself the master, it SHALL NOT issue a request, and SHALL record that the resync was unreachable.

This mirrors how structural recovery already handles the same dead end. A master that cannot apply a patch has no higher authority to consult, and a peer with no master has nowhere to send the request. Both must be visible rather than retried in silence.

#### Scenario: A master cannot resync itself

- **WHEN** the peer holding an unapplied patch is the session master
- **THEN** it SHALL NOT issue a state request
- **AND** SHALL record that the resync was unreachable
