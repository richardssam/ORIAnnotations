# structure-divergence-recovery

## Purpose
Keeps a peer's session matching the rest of the session after it makes a
structural change it is not permitted to broadcast. Role enforcement governs
what a peer may emit, never what its host application lets the user do locally,
so a non-driver can delete, add or reorder material and silently hold a session
that no longer matches anyone else's. This capability turns that moment into a
detected divergence and a rebuild from the authoritative state.

## Requirements

### Requirement: A structural change that cannot be broadcast marks this peer diverged

When a peer makes a structural change to its local session and that change
cannot be broadcast, the peer SHALL record that its structure has diverged from
the session.

Divergence is declared from the *attempt*, not from later evidence of
mismatch. By the time a peer can observe a mismatch — a patch naming an object
it does not hold, a view it cannot mirror — it cannot distinguish "I edited
something I may not edit" from "I have not caught up yet", and the two demand
opposite responses. The attempt is unambiguous: this peer changed structure and
the session was not told.

Structural changes that are broadcast normally SHALL NOT mark the peer diverged,
and neither SHALL a suppressed broadcast in any other category. Annotation
traffic in particular is expressed as structural patches but is permitted to
roles that may not emit structure, and must not trip this.

#### Scenario: A refused removal marks divergence
- **WHEN** a peer removes a child locally
- **AND** the removal cannot be broadcast because this peer's role forbids structure
- **THEN** the peer SHALL record that its structure has diverged

#### Scenario: A refused insertion marks divergence
- **WHEN** a peer adds material to its local session
- **AND** the insertion cannot be broadcast because this peer's role forbids structure
- **THEN** the peer SHALL record that its structure has diverged

#### Scenario: A refused reorder marks divergence
- **WHEN** a peer reorders children locally
- **AND** the reorder cannot be broadcast because this peer's role forbids structure
- **THEN** the peer SHALL record that its structure has diverged
- **AND** the divergence SHALL be recorded even though no error was surfaced to the user

#### Scenario: A permitted structural broadcast does not mark divergence
- **WHEN** a peer makes a structural change that is broadcast to the session
- **THEN** the peer SHALL NOT be marked diverged

#### Scenario: A permitted annotation is not a structural divergence
- **WHEN** a peer whose role forbids structure emits an annotation carried as a structural patch
- **THEN** the broadcast SHALL proceed
- **AND** the peer SHALL NOT be marked diverged

#### Scenario: A suppressed playback message is not a structural divergence
- **WHEN** a peer's playback message is emitted with fields stripped, or withheld entirely, because of its role
- **THEN** the peer SHALL NOT be marked diverged

### Requirement: A diverged peer rebuilds from the authoritative state

A diverged peer SHALL recover by requesting the session's current state and
applying it, replacing its local structure, rather than by reversing the local
change or reconciling a difference.

The local change carries no information the session needs: the peer was not
permitted to make it, so there is nothing to merge and no inverse worth
computing. Requesting and applying state is the same operation a joining peer
performs, which is what makes it trustworthy here.

Recovery SHALL be reachable from the synchronised state, not only while joining.

#### Scenario: A diverged peer requests and applies current state
- **WHEN** a peer has been marked diverged
- **AND** the session has a peer able to serve its state
- **THEN** the diverged peer SHALL request that state and apply it
- **AND** the material it removed locally SHALL be present again afterwards

#### Scenario: Recovery is available while synchronised
- **WHEN** a peer diverges while in the synchronised state
- **THEN** it SHALL be able to request state without first leaving the session

### Requirement: One user action produces one rebuild

Recovery SHALL be coalesced, so that a single user action which refuses many
structural broadcasts triggers one rebuild.

Deleting a multi-clip selection refuses one broadcast per child. Rebuilding per
refusal would request the full session state several times for one keystroke,
and each rebuild would race the refusals still arriving behind it.

#### Scenario: A multi-child deletion rebuilds once
- **WHEN** a peer's single action refuses several structural broadcasts in quick succession
- **THEN** the peer SHALL perform one rebuild

#### Scenario: Divergence during a rebuild is not lost
- **WHEN** a peer diverges again while a rebuild is already in progress
- **THEN** the peer SHALL remain marked diverged after that rebuild completes
- **AND** SHALL rebuild again rather than settle in a stale state

### Requirement: A peer that cannot recover stays diverged and says so

When a diverged peer cannot obtain the session's state — no peer is able to
serve it, or the request is not answered — the peer SHALL remain marked diverged
and SHALL remain eligible to recover later.

It SHALL NOT clear the divergence, and SHALL NOT leave the session or discard
its local content, on a failed recovery. A peer that reported itself
synchronised while holding structure nobody else has is worse than one that
reports the truth, and discarding local content on an unanswered request would
destroy the user's material to fix a mismatch that may not exist.

#### Scenario: No peer can serve state
- **WHEN** a diverged peer finds no peer able to serve the session's state
- **THEN** it SHALL remain marked diverged
- **AND** SHALL NOT discard its local content
- **AND** SHALL attempt recovery again when one becomes available

#### Scenario: A state request is not answered
- **WHEN** a diverged peer's request for state is not answered within the session's timeout
- **THEN** it SHALL remain marked diverged
- **AND** SHALL remain in the session

#### Scenario: Recovery clears the divergence only on success
- **WHEN** a rebuild completes successfully
- **THEN** the peer SHALL no longer be marked diverged

### Requirement: Divergence is visible to the user

A peer's diverged and recovering conditions SHALL be reported in the session
state it publishes to its user interface.

Recovery restores material the user has just deleted and reorders material they
have just reordered. Unexplained, that reads as the application fighting them.
The user must be able to see both that their session had diverged and that it
was rebuilt, or the correct behaviour is indistinguishable from a fault.

#### Scenario: A diverged peer reports the condition
- **WHEN** a peer is marked diverged
- **THEN** its published session state SHALL report that condition

#### Scenario: A recovered peer stops reporting it
- **WHEN** a peer completes a rebuild successfully
- **THEN** its published session state SHALL no longer report the condition

#### Scenario: An unrecoverable divergence is distinguishable from a healthy session
- **WHEN** a peer is diverged and cannot obtain the session's state
- **THEN** its published session state SHALL report a condition distinct from a synchronised peer's
