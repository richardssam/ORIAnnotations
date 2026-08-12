## MODIFIED Requirements

### Requirement: The host is elected by capability
The session host SHALL be elected rather than fixed to a particular application, so that a session containing no preferred host still has one. Election SHALL prefer a peer whose application is designated as the preferred visibility authority, and SHALL fall back to any capable peer otherwise. Ties SHALL be broken deterministically so that every peer reaches the same result.

Election SHALL additionally be restricted to peers holding the `driver` role. A host that is not a driver would hold visibility authority while its own role forbade it from emitting visibility, so the session's view would freeze with no peer able to change it and nothing reporting why. Role SHALL be applied as a filter on the candidate set, with the existing application preference and deterministic tie-break applied among the survivors, so that every peer still reaches the same host from the same peer table.

A peer whose role is not known — because it is running code that predates roles, or because of the order in which it was learned — SHALL be treated as holding the session's default role for the purpose of this filter. Treating an unknown role as ineligible would let a single peer, or a single adoption ordering, produce a candidate set that appears empty, which is a false report of a driverless session rather than a late election.

The host role SHALL be distinct from the existing master (snapshot authority) role: a change of master SHALL NOT by itself change who controls visibility. It SHALL likewise be distinct from the session role: a session MAY contain several drivers and SHALL have at most one host.

A host that leaves the session SHALL NOT retain visibility authority. Election
SHALL be re-run when a peer departs, so authority moves to a peer that is still
present.

This closes a failure that is otherwise unreachable by any other route: because
only the host may broadcast visibility, a host that has gone while still being
counted as elected leaves the session's view frozen, with no peer permitted to
change it and nothing reporting the cause.

The role filter makes an equivalent state reachable by a second route — a session in which no driver is present at all — so that state SHALL be reported and SHALL have an exit, on the same grounds. Reporting it SHALL NOT be left implicit in a disabled menu item.

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

#### Scenario: A non-driver is not elected host
- **WHEN** a session contains a preferred-application peer holding the `viewer` or `reviewer` role and a non-preferred peer holding the `driver` role
- **THEN** the driver SHALL be elected host
- **AND** the preferred peer SHALL NOT be elected despite its application preference

#### Scenario: Preference still decides among drivers
- **WHEN** a session contains more than one peer holding the `driver` role
- **THEN** the existing application preference and deterministic tie-break SHALL decide between them

#### Scenario: An unknown role does not remove a candidate
- **WHEN** the peer table contains a capable peer whose role is not known
- **AND** the session's default role is `driver`
- **THEN** that peer SHALL remain eligible for election

#### Scenario: A session with no driver reports the condition
- **WHEN** no peer in the session holds the `driver` role
- **THEN** no host SHALL be elected
- **AND** the condition SHALL be reported to the user rather than presenting as an unexplained frozen view
- **AND** an action SHALL be available that resolves it
