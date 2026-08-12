## MODIFIED Requirements

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

## ADDED Requirements

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
