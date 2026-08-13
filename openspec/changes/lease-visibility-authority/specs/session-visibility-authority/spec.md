## ADDED Requirements

### Requirement: Visibility is held by the peer that most recently asserted a view

Authority over visibility SHALL be a lease held by the peer that most recently
asserted what the session is looking at, not a standing property of an elected
seat.

The category SHALL remain single-writer at any instant. Leasing changes *who*
the writer is, not how many there are: at most one peer may assert a view at a
time, and every other peer mirrors it.

A peer SHALL claim visibility when a local user action changes what it is
displaying, and SHALL hold the category for a bounded time, releasing it on
expiry as every other leased category does.

#### Scenario: Selecting a clip takes the category
- **WHEN** a user changes what their peer is displaying
- **AND** that peer's role permits visibility
- **THEN** that peer SHALL hold visibility authority
- **AND** its view SHALL be broadcast to the session

#### Scenario: An idle peer does not hold the view
- **WHEN** a peer holds visibility and stops asserting a view for the lease duration
- **THEN** it SHALL no longer hold the category
- **AND** another eligible peer SHALL be able to take it without contest

#### Scenario: Only one peer asserts a view at a time
- **WHEN** two peers assert a view simultaneously
- **THEN** exactly one SHALL hold the category
- **AND** the other's visibility fields SHALL be stripped from its outgoing messages

### Requirement: The peer that just acted takes visibility from an idle holder

Where two peers claim visibility, the claim from the peer that acted **most
recently** SHALL win.

This is deliberately the opposite of the position category, and the difference
is the substance of this capability. Position prefers the earlier claim, which
is right where two peers scrubbing at once is a conflict to settle in favour of
whoever started. For visibility the later claim *is* the user who just selected
something, and refusing it is precisely the failure this capability exists to
remove: a user watching their own selections do nothing, with no error at either
end.

A claim SHALL NOT be honoured while another peer is actively asserting a view,
so that "most recent" cannot be read as "whoever spoke last in a burst".

#### Scenario: A new selection takes the view from an idle holder
- **WHEN** a peer holds visibility but has not asserted a view recently
- **AND** another eligible peer's user selects something
- **THEN** the selecting peer SHALL hold visibility
- **AND** the session SHALL display what that peer selected

#### Scenario: An active holder is not interrupted mid-action
- **WHEN** a peer is actively asserting a view
- **AND** another peer claims visibility within that activity
- **THEN** the active holder SHALL retain the category

#### Scenario: Hand-off does not oscillate
- **WHEN** visibility passes from one peer to another
- **THEN** the session SHALL settle on one view
- **AND** SHALL NOT alternate between the two peers' views while both remain idle

### Requirement: A role that forbids visibility forbids claiming it

A peer whose session role does not permit emitting visibility SHALL NOT acquire
the visibility lease, and its claim SHALL be refused rather than recorded.

Role is checked before the lease, on the same composition rule the other
categories already follow: a claim recorded for a peer that may never broadcast
the category leaves an un-preemptable holder that cannot use what it holds.

#### Scenario: A reviewer cannot take the view
- **WHEN** a peer whose role forbids visibility changes what it displays locally
- **THEN** it SHALL NOT acquire visibility authority
- **AND** its visibility fields SHALL be stripped from its outgoing messages

#### Scenario: A refused claim leaves the current holder untouched
- **WHEN** a role-forbidden peer's visibility claim is refused
- **THEN** the peer currently holding visibility SHALL continue to hold it

### Requirement: A peer that never claims still defers to the elected host

A peer that does not claim visibility SHALL continue to defer to the elected
host, so that a session containing peers which predate the visibility lease
behaves as it did before it.

Absence of a claim SHALL be read as "this peer is not asserting a view", never
as "this peer relinquishes" or "no peer holds the category" — the same
convention the session already applies to an omitted host, roster or ownership
section.

#### Scenario: A session where nobody claims behaves as before
- **WHEN** no peer in a session claims visibility
- **THEN** the elected host SHALL hold visibility authority
- **AND** the session SHALL behave exactly as it did before visibility was leased

#### Scenario: A non-claiming peer is not treated as releasing the category
- **WHEN** a peer sends messages carrying no visibility claim
- **THEN** the current holder's authority SHALL be unchanged

## MODIFIED Requirements

### Requirement: The host is elected by capability
The session host SHALL be elected rather than fixed to a particular application, so that a session containing no preferred host still has one. Election SHALL prefer a peer whose application is designated as the preferred visibility authority, and SHALL fall back to any capable peer otherwise. Ties SHALL be broken deterministically so that every peer reaches the same result.

Election decides who holds visibility **when no peer has claimed it** — at
session start, and after a holder departs. It is no longer the standing answer
to which peer may change the view; that is the visibility lease. Election
continues to supply the eligibility filter and the indicator for a session with
no peer able to hold the category at all.

Where peers are otherwise equally ranked, the session master SHALL be preferred.
A GUID tie-break alone assigns the seat at random between two peers of the same
application, which moved authority to a joining peer one second after it
connected and left the peer the user was driving unable to change the view. The
master is used rather than the incumbent host because peers already agree on the
master, so election remains a pure function that converges without a claim
protocol; two peers that each elected themselves while alone hold different
incumbents and would not converge. The master preference SHALL break ties only,
and SHALL NOT outrank a better-qualified application.

The host role SHALL be distinct from the existing master (snapshot authority) role: a change of master SHALL NOT by itself change who controls visibility.

A host that leaves the session SHALL NOT retain visibility authority. Election
SHALL be re-run when a peer departs, so authority moves to a peer that is still
present.

This closes a failure that is otherwise unreachable by any other route: because
only the host may broadcast visibility, a host that has gone while still being
counted as elected leaves the session's view frozen, with no peer permitted to
change it and nothing reporting the cause.

#### Scenario: The preferred application hosts when present
- **WHEN** a session contains both a preferred-host peer and other peers
- **THEN** the preferred peer SHALL be elected host

#### Scenario: A session without a preferred peer still has a host
- **WHEN** a session contains only non-preferred peers
- **THEN** one of them SHALL be elected host
- **AND** it SHALL hold visibility authority exactly as a preferred host would

#### Scenario: The master holds the seat against an equally-ranked joiner
- **WHEN** a peer joins a session whose host runs the same application
- **AND** the existing host is the session master
- **THEN** the existing host SHALL remain elected

#### Scenario: A preferred application still outranks the master
- **WHEN** a peer of the preferred application joins a session whose master runs a less-preferred application
- **THEN** the joining peer SHALL be elected host

#### Scenario: Learning the master re-runs the election
- **WHEN** a peer learns which peer is master
- **AND** that changes the elected host
- **THEN** the peer SHALL re-elect rather than keep the host it chose without that knowledge

#### Scenario: Every peer agrees on the host
- **WHEN** peers evaluate host election from the same set of peers
- **THEN** they SHALL all reach the same host
- **AND** a late-joining peer SHALL learn the current host from the session state it is given on joining

#### Scenario: A departing host hands over visibility authority
- **WHEN** the peer currently elected host leaves the session
- **THEN** a remaining peer SHALL be elected host
- **AND** that peer SHALL be able to broadcast visibility changes
- **AND** the session's view SHALL NOT remain frozen on the departed host's last state

#### Scenario: A departing follower does not change the host
- **WHEN** a peer that is not the host leaves the session
- **THEN** the elected host SHALL be unchanged

### Requirement: A non-owner never infers local user intent
A peer that does not own a category SHALL NOT infer, from a local state transition within that category, that a local user caused it, and SHALL NOT act on such an inference.

Ownership of visibility means **holding its lease**, not being the elected host.
A peer that has just taken the category by acting owns it for as long as it
holds it, and a peer whose lease has passed to someone else does not, whatever
the election says.

This exists because a transition caused by applying a remote message is indistinguishable from one caused by a user, and acting on the guess produces effects that no user requested — starting playback on every peer, or resetting a playhead over a seek that had just been applied.

#### Scenario: A peer-driven transition triggers no local-intent side effects
- **WHEN** a peer's view changes as a result of applying a message from another peer
- **THEN** it SHALL NOT broadcast anything that describes that change as a local action
- **AND** it SHALL NOT start, stop, or reposition playback on the assumption that a user initiated the change

#### Scenario: The owner's transitions need no inference
- **WHEN** the peer holding visibility changes its own view
- **THEN** it MAY broadcast the change, being the only peer permitted to
- **AND** no time-based guard SHALL be required to decide whether that change was locally caused

#### Scenario: Losing the lease ends the inference
- **WHEN** a peer's visibility lease passes to another peer
- **THEN** it SHALL stop treating its own view transitions as broadcastable user intent
