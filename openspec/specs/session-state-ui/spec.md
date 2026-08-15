# session-state-ui Specification

## Purpose
Provides a unified cross-platform graphical user interface to expose OTIO Sync Session state, including connected peers, master/host assignments, and broadcast ownership leases.
## Requirements
### Requirement: Cross-platform native presentation
The session state UI SHALL render natively in the host application, adapting its styling to match either xStudio or OpenRV seamlessly without requiring separate UI codebases.

#### Scenario: Running in xStudio
- **WHEN** the session state UI is launched within xStudio
- **THEN** it renders using xStudio's native color palette, fonts, and widget sizing conventions (via `XsStyleSheet`).

#### Scenario: Running in OpenRV
- **WHEN** the session state UI is launched within OpenRV
- **THEN** it renders using a consistent dark theme matching OpenRV's default appearance, without crashing due to missing xStudio QML modules.

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

### Requirement: Display master and host roles
The UI SHALL clearly indicate which peer is the elected Master and which peer is the elected Host (visibility authority).

The UI SHALL additionally show each peer's session role — driver, reviewer, or viewer. Session role, master, and host SHALL be presented as three separate things rather than collapsed into one label: a session may contain several drivers and exactly one host, and the master may hold any role.

The role shown SHALL come from the shared state projection rather than from per-host formatting, so both host applications label the same peer identically. Where the projection previously reported a placeholder derived from master or host status, it SHALL report the session role instead, with master and host remaining the separate flags they already are.

The presentation SHALL NOT imply that a peer is prevented from acting outside its role. Enforcement is send-side and cooperative, so the role shown is what that peer declares.

#### Scenario: Master re-election
- **WHEN** the current master leaves and a new master is elected
- **THEN** the UI updates its Master indicator to point to the newly elected peer.

#### Scenario: Role, master, and host are distinguishable
- **WHEN** the panel lists a peer that is master, is not host, and holds the `reviewer` role
- **THEN** all three facts SHALL be readable from that peer's row
- **AND** no one of them SHALL be inferred from another

#### Scenario: Several drivers are shown as such
- **WHEN** a session contains more than one peer holding the `driver` role
- **THEN** each SHALL be shown as a driver
- **AND** only one of them SHALL be indicated as host

#### Scenario: Both hosts label a peer identically
- **WHEN** the same session is viewed from both host applications
- **THEN** a given peer's role SHALL be shown with the same value in both

### Requirement: Debug mode for broadcast leases
The UI SHALL provide an optional Debug Mode that exposes low-level session mechanics, specifically broadcast ownership leases and raw GUIDs.

#### Scenario: Toggling Debug Mode
- **WHEN** a user activates Debug Mode
- **THEN** the UI expands to show which peers currently hold the `position`, `display`, and `structure` leases.

### Requirement: Host menu entry points
Each host application SHALL offer a menu item that opens the Session State panel and a "resync" item that re-requests the full session state from the master. In xStudio these SHALL sit directly under the top-level "Session" menu together with the existing Create/Join/Leave items, so session management is reachable without descending into a submenu. In OpenRV they SHALL sit in the "OTIO Sync" menu (see the `openrv-sync-plugin` spec).

#### Scenario: xStudio Session menu contents
- **WHEN** the ORI Sync plugin is loaded in xStudio
- **THEN** the "Session" menu SHALL contain "Create Session...", "Join Session...", "Leave Session", "Session State...", and "Resync Session" as direct children
- **AND** none of them SHALL be nested under a "Connect" submenu

#### Scenario: Resync is a no-op for the master
- **WHEN** the resync item is selected on the peer that is currently master
- **THEN** no state request SHALL be sent, because the master is the authority being resynced from

### Requirement: Hosts share the state projection, not the view
The session state each host displays SHALL be derived from `otio_sync_core.session_state.session_state_snapshot`, a Qt-free projection of `SyncManager`. Host code SHALL be limited to layout and to binding that projection into its own UI toolkit. A host SHALL NOT reimplement the peer, role, or lease derivation locally.

The projection SHALL be read-only: it reads `SyncManager` and never requires fields to be added to it. A panel that needs state the manager does not hold SHALL obtain it from the host (for example, the host's own current view) rather than by extending the sync core.

#### Scenario: A new peer field reaches every host
- **WHEN** a field is added to the peer listing
- **THEN** it SHALL be added once in `session_state_snapshot`
- **AND** SHALL NOT require a parallel edit to a host-specific reimplementation of the same listing

#### Scenario: The panel never mutates sync state
- **WHEN** the panel is open and polling
- **THEN** no field SHALL be written to `SyncManager`
- **AND** the projection SHALL return plain data that the caller can mutate without affecting manager state

#### Scenario: A host without PySide6 is still served
- **WHEN** a host application cannot host the PySide6 models (as xStudio cannot, its QML running in xStudio's own engine)
- **THEN** it SHALL still consume `session_state_snapshot` directly
- **AND** SHALL render it with a view written in that host's native idiom

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

### Requirement: The panel reports a session with no eligible driver

The panel SHALL report the condition in which no peer holds a role that makes it eligible to be host, because in that condition nothing can change what the session is looking at.

The report SHALL be an explicit indication of the condition. Availability of the recovery action MAY coincide with it, but a menu item that becomes enabled is not by itself the report: a user who has not opened that menu is left with a session that has stopped responding and no explanation.

#### Scenario: The driverless condition is visible in the panel
- **WHEN** the panel is open and no peer in the session holds an eligible driver role
- **THEN** the panel SHALL indicate that the session currently has no driver

#### Scenario: The indication clears when a driver appears
- **WHEN** a peer becomes a driver, by joining or by self-elevation
- **THEN** the panel SHALL stop reporting the condition
- **AND** SHALL show the newly elected host

#### Scenario: The projection remains read-only
- **WHEN** the panel derives and displays role and the driverless condition
- **THEN** no field SHALL be written to the sync manager
- **AND** the derivation SHALL happen in the shared projection rather than in either host's view code


### Requirement: The session panel names who currently holds the view

Both host applications' session panels SHALL show which participant currently
holds visibility authority, identified by person rather than by peer GUID.

A category that moves between users silently is worse than one that never moves.
The failure this capability addresses was invisible for exactly this reason: a
user's selections did nothing, no error appeared at either end, and the only
record that the authority sat elsewhere was a log line on the machine that had
lost it.

The panel SHALL distinguish holding the view from merely being able to take it,
so a user can tell "I am driving" from "I could drive".

#### Scenario: The holder is named
- **WHEN** a peer holds visibility authority
- **THEN** every peer's panel SHALL show that participant as holding the view

#### Scenario: This peer holding it is distinguishable
- **WHEN** this peer holds visibility authority
- **THEN** its panel SHALL show that this peer is the one driving the view

#### Scenario: An eligible peer that is not holding it is shown as such
- **WHEN** this peer may claim visibility but does not currently hold it
- **THEN** the panel SHALL distinguish that from holding it

#### Scenario: A peer that may never hold it is shown as such
- **WHEN** this peer's role forbids visibility
- **THEN** the panel SHALL show that changing the view is not available to it
- **AND** SHALL NOT present it as merely not currently holding the category

### Requirement: The session panel reports whether this peer confirmed the state it joined with

Both host applications' session panels SHALL show whether this peer's joined
state was confirmed against the snapshot it was sent.

Three outcomes SHALL be distinguishable: confirmed, mismatched, and not
confirmed. Collapsing "not checked" into either of the others reproduces the
silence this exists to remove — a peer that never finished joining would
otherwise present exactly as one that joined correctly.

A reported mismatch SHALL be presented as information about this peer's own
state, not as an error attributed to the session or to another participant. The
peer that is wrong is this one, and a message implying otherwise sends the user
looking in the wrong place.

#### Scenario: A confirmed join is shown as confirmed

- **WHEN** this peer's joined state matched the snapshot it was sent
- **THEN** the panel SHALL show the state as confirmed

#### Scenario: A mismatch is shown, with what differed

- **WHEN** this peer's joined state did not match the snapshot
- **THEN** the panel SHALL show that it did not match
- **AND** SHALL make the differing fields available to the user

#### Scenario: An unchecked join is distinguishable from a confirmed one

- **WHEN** the confirmation could not be performed
- **THEN** the panel SHALL show the state as not confirmed
- **AND** SHALL NOT present it as either confirmed or mismatched

#### Scenario: A peer that never joined shows no outcome

- **WHEN** this peer has not joined a session
- **THEN** no confirmation outcome SHALL be shown

#### Scenario: The outcome comes from the shared projection

- **WHEN** either panel displays the confirmation outcome
- **THEN** it SHALL read that outcome from the shared session-state projection
  rather than deriving it locally
