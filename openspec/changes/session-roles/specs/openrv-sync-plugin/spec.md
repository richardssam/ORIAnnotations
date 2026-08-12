## ADDED Requirements

### Requirement: The OTIO Sync menu offers driverless-session recovery

The OpenRV plugin SHALL offer a "Become Controller" item in the OTIO Sync menu, which sets this peer's session role to `driver`.

The item SHALL be enabled only while the session contains no peer eligible to be host on account of role, and SHALL be in `DisabledMenuState` otherwise. An always-available control would make a restrictive session policy advisory.

The item SHALL set the role and nothing else. It SHALL NOT assign host directly: host follows from the next election, which is a pure function of the peer table.

The eligibility question SHALL be answered by the shared sync core, not computed in the plugin, consistent with the existing rule that the plugin does not gate behaviour on authority it derives itself.

#### Scenario: The item is enabled only in the driverless condition

- **WHEN** the plugin is in a session containing at least one eligible driver
- **THEN** "Become Controller" SHALL be in `DisabledMenuState`

#### Scenario: Self-elevation grants the role

- **WHEN** the user selects "Become Controller" while the session has no eligible driver
- **THEN** this peer's role SHALL become `driver`
- **AND** the plugin SHALL NOT assign host locally
- **AND** the peer SHALL re-announce so other peers observe the new role

#### Scenario: The menu rebuilds when the condition clears

- **WHEN** another peer becomes a driver
- **THEN** the OTIO Sync menu SHALL rebuild with "Become Controller" disabled

### Requirement: OpenRV broadcasts unconditionally and lets the core apply role

The OpenRV plugin SHALL continue to invoke broadcast and category-claim operations without consulting its own role, leaving both the field stripping and the claim refusal to the shared core.

Where the plugin needs its role for presentation — labelling its own row in the session panel, or enabling the recovery item — it SHALL read it from the shared core through a single predicate rather than deriving it from local state.

A peer whose broadcasts are stripped by role SHALL continue to respond normally to its own user's input locally, and SHALL re-converge when the driver next broadcasts. No local interaction SHALL be blocked on account of role.

#### Scenario: No broadcast path tests role

- **WHEN** the plugin emits any broadcast
- **THEN** it SHALL do so without testing this peer's role
- **AND** any stripping SHALL be applied inside the shared core

#### Scenario: A viewer still interacts locally

- **WHEN** a user whose peer holds the `viewer` role scrubs, plays, or annotates in OpenRV
- **THEN** OpenRV SHALL respond locally as it normally would
- **AND** nothing session-visible SHALL be emitted

#### Scenario: A viewer re-converges on the next driver broadcast

- **WHEN** a peer holding the `viewer` role has diverged locally
- **AND** a driver subsequently broadcasts the session's state
- **THEN** that peer SHALL adopt the broadcast state
