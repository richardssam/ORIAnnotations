## Purpose

Defines the session lifecycle UI — environment-variable-driven auto-connect, and interactive Create/Join/Leave session menu items — shared across the RV and xStudio sync plugins.

## Requirements

### Requirement: ORI_SESSION environment variable triggers auto-join on startup

When `ORI_SESSION` is set, both plugins SHALL parse it as `[host:]session_name` and connect to that session automatically during plugin initialisation, with join semantics (no "already exists" warning).

#### Scenario: Auto-join with host and session name

- **WHEN** `ORI_SESSION=rmq.studio.com:daily-review` is set before launch
- **THEN** the plugin SHALL connect to session `daily-review` on host `rmq.studio.com` without prompting the user

#### Scenario: Auto-join with session name only

- **WHEN** `ORI_SESSION=daily-review` is set (no colon)
- **THEN** the plugin SHALL connect to session `daily-review` on `localhost` (or `ORI_RMQ_HOST` if set)

#### Scenario: No auto-connect when ORI_SESSION is absent

- **WHEN** `ORI_SESSION` is not set
- **THEN** the plugin SHALL start in disconnected state and show session management menu items

### Requirement: ORI_RMQ_HOST environment variable sets the default RabbitMQ host

When `ORI_RMQ_HOST` is set, both plugins SHALL use its value as the default host pre-filled in connection dialogs and as the fallback host when `ORI_SESSION` contains no host component.

#### Scenario: Dialog pre-filled from env var

- **WHEN** `ORI_RMQ_HOST=rmq.studio.com` is set and the user opens Create or Join dialog
- **THEN** the MQ Host field SHALL be pre-filled with `rmq.studio.com`

### Requirement: ORI_SESSION host component overrides ORI_RMQ_HOST

When `ORI_SESSION` contains an explicit host, it SHALL take precedence over `ORI_RMQ_HOST`.

#### Scenario: Explicit host in ORI_SESSION wins

- **WHEN** `ORI_RMQ_HOST=fallback.host` and `ORI_SESSION=primary.host:my-session` are both set
- **THEN** the plugin SHALL connect to `primary.host`, not `fallback.host`

### Requirement: Create Session menu item

Both plugins SHALL provide a "Create Session…" menu item that prompts for a RabbitMQ host and session name, connects, and warns the user if the session was already occupied by another master.

#### Scenario: Clean session created

- **WHEN** the user fills in host and session name, confirms, and no master exists on that exchange
- **THEN** the plugin SHALL connect and become master with no warning shown

#### Scenario: Session already exists warning

- **WHEN** the user chooses Create Session and another master responds to WHO_IS_MASTER within the discovery timeout
- **THEN** after reaching STATE_SYNCED the plugin SHALL display a warning: "Session '{name}' already exists. You have joined as a peer rather than creating a new session."

#### Scenario: Create Session while already connected

- **WHEN** the user activates Create Session while already in a session
- **THEN** the plugin SHALL display an informational message: "Already connected to '{name}'. Leave the current session first." and SHALL NOT attempt to connect again

### Requirement: Join Session menu item

Both plugins SHALL provide a "Join Session…" menu item that prompts for a RabbitMQ host and session name and connects with peer semantics. No "already exists" warning is shown.

#### Scenario: Successful join

- **WHEN** the user fills in host and session name and confirms
- **THEN** the plugin SHALL connect to that session and synchronise state from the master

#### Scenario: Join Session while already connected

- **WHEN** the user activates Join Session while already in a session
- **THEN** the plugin SHALL display an informational message and SHALL NOT connect again

### Requirement: Leave Session menu item

Both plugins SHALL provide a "Leave Session" menu item (labelled with the current session name when connected) that disconnects from the active session.

#### Scenario: Successful leave

- **WHEN** the user activates Leave Session while in a session
- **THEN** the plugin SHALL disconnect, stop all background threads, and return to the disconnected state

#### Scenario: Leave Session while not connected

- **WHEN** the user activates Leave Session while not in a session
- **THEN** the plugin SHALL do nothing

### Requirement: Two-field connection dialog

Both plugins SHALL present a dialog with two fields — MQ Host and Session Name — when the user activates Create Session or Join Session.

#### Scenario: MQ Host defaults to ORI_RMQ_HOST or localhost

- **WHEN** the dialog opens
- **THEN** the MQ Host field SHALL be pre-filled with `ORI_RMQ_HOST` if set, otherwise `localhost`

#### Scenario: Empty session name prevents connect

- **WHEN** the user leaves Session Name blank and confirms
- **THEN** the plugin SHALL NOT attempt to connect
