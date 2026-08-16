## ADDED Requirements

### Requirement: The session panel offers a role control for each peer

Both host applications' session panels SHALL offer, on each peer's row, a control that grants that peer's participant one of the session roles.

The control SHALL be enabled only while the local peer holds a role permitted to issue a grant, and SHALL be absent or disabled otherwise rather than being offered and then failing. Whether to offer it SHALL be read from the shared state projection, not derived in either host's view code, so that both hosts agree about who may administer a session.

The control SHALL NOT be offered for a peer carrying no identity, because such a peer cannot be the target of a grant.

The panel SHALL show a peer's role as the role that peer declares. It SHALL NOT show a requested role, a pending role, or any indication that a grant is awaiting acknowledgement: a grant is applied by its target, and the row updates when the target announces. Nor SHALL the presentation assert that a peer is *prevented* from acting outside its role — enforcement is send-side and cooperative, and adding a control does not change that.

Where issuing a grant would leave the session with no eligible driver, the panel SHOULD confirm with the user before issuing it. This is a courtesy in the view: the core SHALL apply the grant if it is issued, and the driverless condition the panel already reports is the recovery.

#### Scenario: A driver is offered the control

- **WHEN** the panel is open on a peer holding the `driver` role
- **THEN** each peer row SHALL offer a role control

#### Scenario: A non-driver is not offered the control

- **WHEN** the panel is open on a peer holding `reviewer` or `viewer`
- **THEN** no peer row SHALL offer an enabled role control

#### Scenario: The row updates when the target announces

- **WHEN** a driver grants another peer a role
- **THEN** that peer's row SHALL show the new role once the target has announced it
- **AND** no intermediate requested or pending role SHALL be displayed

#### Scenario: A peer without identity offers no control

- **WHEN** a peer row represents a peer carrying no identity
- **THEN** that row SHALL NOT offer a role control

#### Scenario: Demoting the last driver is confirmed

- **WHEN** a user selects a role that would leave the session with no eligible driver
- **THEN** the panel SHOULD ask the user to confirm before issuing the grant

#### Scenario: Both hosts offer the same thing

- **WHEN** the same session is viewed from both host applications by the same participant
- **THEN** the availability of the role control SHALL be the same in both

## MODIFIED Requirements

### Requirement: Hosts share the state projection, not the view
The session state each host displays SHALL be derived from `otio_sync_core.session_state.session_state_snapshot`, a Qt-free projection of `SyncManager`. Host code SHALL be limited to layout and to binding that projection into its own UI toolkit. A host SHALL NOT reimplement the peer, role, or lease derivation locally.

The projection SHALL be read-only: it reads `SyncManager`, never writes to it, and never requires fields to be added to it. A panel that needs state the manager does not hold SHALL obtain it from the host (for example, the host's own current view) rather than by extending the sync core.

The panel is not read-only, and SHALL NOT be required to be. Where it offers an action — self-elevation, or granting a peer a role — it SHALL invoke a **named command** on the sync core and SHALL NOT write into the projection's output or into manager state directly. The projection returns plain data that the caller may mutate freely without affecting the manager, so a panel that changed state by writing into it would produce a UI that appeared to work and a session that never heard about it.

The decision the command makes SHALL live in the core, not in the panel. A panel MAY read the projection to decide whether to *offer* an action; it SHALL NOT be the thing that decides whether the action is permitted.

#### Scenario: A new peer field reaches every host
- **WHEN** a field is added to the peer listing
- **THEN** it SHALL be added once in `session_state_snapshot`
- **AND** SHALL NOT require a parallel edit to a host-specific reimplementation of the same listing

#### Scenario: The projection never mutates sync state
- **WHEN** the panel is open and polling
- **THEN** no field SHALL be written to `SyncManager` by the projection
- **AND** the projection SHALL return plain data that the caller can mutate without affecting manager state

#### Scenario: A panel action goes through a named command
- **WHEN** the panel offers an action that changes session state
- **THEN** it SHALL invoke a named operation on the sync core
- **AND** SHALL NOT write the intended change into the projection's output

#### Scenario: The panel does not decide whether an action is permitted
- **WHEN** a panel action is invoked
- **THEN** the core SHALL apply or refuse it on its own evaluation
- **AND** the panel's decision to offer the control SHALL NOT be what permits it

#### Scenario: A host without PySide6 is still served
- **WHEN** a host application cannot host the PySide6 models (as xStudio cannot, its QML running in xStudio's own engine)
- **THEN** it SHALL still consume `session_state_snapshot` directly
- **AND** SHALL render it with a view written in that host's native idiom
