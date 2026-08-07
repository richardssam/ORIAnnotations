## MODIFIED Requirements

### Requirement: The host is elected by capability
The session host SHALL be elected rather than fixed to a particular application, so that a session containing no preferred host still has one. Election SHALL prefer a peer whose application is designated as the preferred visibility authority, and SHALL fall back to any capable peer otherwise. Ties SHALL be broken deterministically so that every peer reaches the same result.

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
