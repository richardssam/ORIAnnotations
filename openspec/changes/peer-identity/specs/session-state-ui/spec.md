## MODIFIED Requirements

### Requirement: Display connected peers
The UI SHALL display a list of all currently connected peers in the sync session, including the local peer.

Each peer SHALL be identified by the name derived from its identity rather than by its GUID. A peer that carries no identity SHALL be shown by application name and GUID, as before, so that a peer running older code remains visible and labelled.

The derived name SHALL come from the shared state projection, not from per-host formatting, so that both host applications name the same peer identically.

#### Scenario: Peer list updates
- **WHEN** a remote peer joins or leaves the sync session
- **THEN** the UI updates to reflect the current active peers based on the SyncManager's state.

#### Scenario: A peer is listed by name
- **WHEN** a peer in the session carries an identity
- **THEN** the list SHALL show its derived name
- **AND** SHALL NOT require the GUID to distinguish it from another peer running the same application

#### Scenario: A peer without identity is still listed
- **WHEN** a peer in the session carries no identity
- **THEN** it SHALL still appear in the list, labelled by application name and GUID

## ADDED Requirements

### Requirement: Account and machine names are Debug Mode detail

The account name and machine name behind a peer's identity SHALL be shown only when Debug Mode is active. The default view SHALL show the derived name and application.

A session may contain participants from multiple organisations, and the default listing should not put every participant's machine name on every participant's screen. This is a presentation decision, not an access control: the fields are carried by the protocol and are readable by every peer regardless of what the panel shows.

#### Scenario: Default view omits machine detail
- **WHEN** the panel is open with Debug Mode inactive
- **THEN** each peer row SHALL show its derived name and application
- **AND** SHALL NOT show its account name or machine name

#### Scenario: Debug Mode reveals identity detail
- **WHEN** Debug Mode is activated
- **THEN** each peer row SHALL additionally expose the account name and machine name
