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

#### Scenario: Peer list updates
- **WHEN** a remote peer joins or leaves the sync session
- **THEN** the UI updates to reflect the current active peers based on the SyncManager's state.

### Requirement: Display master and host roles
The UI SHALL clearly indicate which peer is the elected Master and which peer is the elected Host (visibility authority).

#### Scenario: Master re-election
- **WHEN** the current master leaves and a new master is elected
- **THEN** the UI updates its Master indicator to point to the newly elected peer.

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

