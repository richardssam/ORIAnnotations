# peer-identity

## Purpose
Carries who is on the other end of a sync session — a human-readable identity per peer, resolved from the local machine today and from an authenticated login service later — so that peers can be recognised by name rather than by GUID, in panels, in logs, and by any mechanism that needs to remember a person across a reconnect.

## Requirements
### Requirement: A peer carries a human identity

Every peer SHALL carry an identity alongside the application name and capabilities it already advertises. The identity SHALL consist of an account name, an optional personal name, the name of the machine the peer is running on, and a marker recording where the identity came from.

The account name is the field any mechanism that needs to recognise a person across sessions SHALL key on; the machine name distinguishes one person occupying two seats. Personal names SHALL be optional, because the sources available on a local machine do not reliably provide them.

Identity SHALL NOT be required. A peer that carries none remains a full participant in every other respect.

#### Scenario: A peer is identifiable by name

- **WHEN** a peer joins a session
- **THEN** every other peer SHALL be able to name the account, machine, and (when available) person behind it

#### Scenario: One person on two machines

- **WHEN** the same account is connected from two machines
- **THEN** both peers SHALL carry the same account name
- **AND** SHALL be distinguishable by machine name

### Requirement: Identity reaches every peer by every path that carries a peer

Identity SHALL travel by each path through which a peer becomes known to another: the peer's own announcements, and the peer set carried in session state.

Carrying identity on announcements alone is insufficient. A peer that has gone quiet becomes known to a joiner only through the peer set in session state, so identity omitted from that path is missing for precisely the peers a joiner cannot yet name.

#### Scenario: A joiner can name a quiet peer

- **WHEN** a peer joins a session containing a peer that has not announced since the joiner arrived
- **THEN** the joiner SHALL know that peer's identity from the session state it received
- **AND** SHALL NOT have to wait for that peer's next announcement to name it

#### Scenario: Both paths agree

- **WHEN** a peer learns another peer's identity from session state
- **AND** subsequently receives that peer's own announcement
- **THEN** the identity SHALL be unchanged by the second path

### Requirement: The identity source is replaceable without changing the protocol

Resolution of a peer's own identity SHALL happen in one place, and every consumer — the wire format, the peer set, the shared state projection, and each host's presentation — SHALL be independent of where the identity came from.

Each identity SHALL record its provenance, so that a consumer can distinguish an identity resolved from the local machine, one entered by the user, and one issued by an authenticated service, without inferring it from which other fields are populated.

Replacing the local resolver with an authenticated identity service SHALL NOT require a change to the message format, the peer set, or either host's presentation.

#### Scenario: Swapping the source changes nothing downstream

- **WHEN** the mechanism that resolves this peer's identity is replaced
- **THEN** the fields on the wire SHALL be unchanged
- **AND** no consumer of a peer's identity SHALL require modification

#### Scenario: Provenance is explicit

- **WHEN** a peer's identity is read
- **THEN** it SHALL state whether it was resolved locally, entered by the user, or issued by a service

### Requirement: A displayed name is derived, not transmitted

The string shown for a peer SHALL be derived from that peer's identity fields by one shared rule, and SHALL NOT be carried as a field of its own.

The rule SHALL fall back through the available fields — personal name, then account name, then application name — so that a partially resolved identity still yields a usable name.

Deriving it once is what prevents two host applications from presenting the same partially resolved identity differently, and prevents a peer from choosing an arbitrary label independent of the fields it reported.

#### Scenario: A peer with no personal name is still named

- **WHEN** a peer reports an account name but no personal name
- **THEN** it SHALL be displayed by its account name

#### Scenario: Both hosts display the same peer identically

- **WHEN** the same peer is shown in each host application
- **THEN** both SHALL display the same derived name

### Requirement: An identity is self-declared and unverified

A peer's identity SHALL be treated as self-declared, on the same terms as the application name it already advertises. Nothing SHALL present an identity as verified, and no authority decision SHALL be justified solely by an identity being present.

This is a stated boundary rather than a defect: the session's transport is unauthenticated and its participants are cooperating, not adversarial. A capability that needs a trustworthy identity SHALL obtain one by replacing the identity source with an authenticated service, and SHALL NOT attempt to verify a self-declared field.

#### Scenario: An overridden identity is accepted

- **WHEN** a peer declares an identity that differs from the one its machine would resolve
- **THEN** other peers SHALL accept and display it
- **AND** its provenance SHALL record that it was entered rather than resolved

### Requirement: A peer without identity degrades rather than disappears

A peer that carries no identity SHALL be displayed and logged as it was before identity existed — by application name and peer GUID — and SHALL NOT be rendered as an empty entry or as an unknown participant.

An absent identity SHALL be ignored rather than treated as a value, so that a peer running code that predates identity cannot become anonymous in a listing that previously named it, and cannot clear an identity already known for it.

#### Scenario: A peer predating identity remains visible

- **WHEN** a peer that carries no identity is in the session
- **THEN** it SHALL appear in every listing that would have shown it before
- **AND** SHALL be labelled by its application and GUID

#### Scenario: A missing identity does not overwrite a known one

- **WHEN** an update for a peer arrives carrying no identity
- **AND** that peer's identity is already known
- **THEN** the known identity SHALL be retained
