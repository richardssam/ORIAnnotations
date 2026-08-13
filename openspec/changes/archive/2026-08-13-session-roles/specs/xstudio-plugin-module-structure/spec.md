## ADDED Requirements

### Requirement: The Session menu offers driverless-session recovery

The xStudio plugin SHALL offer a "Become Controller" item, which sets this peer's session role to `driver`. It SHALL sit in the same top-level "Session" menu as the existing session management items, consistent with those items being direct children rather than nested under a submenu.

The item SHALL be enabled only while the session contains no peer eligible to be host on account of role, and SHALL be disabled otherwise.

The item SHALL set the role and nothing else. Host follows from the next election rather than being assigned by the action.

The eligibility question SHALL be answered by the shared sync core rather than computed in the plugin, consistent with the module structure's rule that protocol decisions are not hand-replicated per application.

#### Scenario: The item sits in the Session menu

- **WHEN** the ORI Sync plugin is loaded in xStudio
- **THEN** "Become Controller" SHALL appear as a direct child of the "Session" menu

#### Scenario: The item is enabled only in the driverless condition

- **WHEN** the session contains at least one eligible driver
- **THEN** "Become Controller" SHALL be disabled

#### Scenario: Self-elevation grants the role

- **WHEN** the user selects "Become Controller" while the session has no eligible driver
- **THEN** this peer's role SHALL become `driver`
- **AND** the plugin SHALL NOT assign host locally
- **AND** the peer SHALL re-announce so other peers observe the new role

### Requirement: xStudio controllers broadcast unconditionally and let the core apply role

The xStudio controllers SHALL continue to invoke broadcast and category-claim operations without consulting this peer's role, leaving field stripping and claim refusal to the shared core. No controller SHALL acquire a role branch on a broadcast path.

Where role is needed for presentation, it SHALL be read from the shared core through a single predicate, in keeping with the module structure's state-ownership rule that a controller owns its domain's state and does not duplicate session-level decisions.

Local interaction SHALL NOT be blocked on account of role. A peer whose broadcasts are stripped SHALL respond to its own user normally and re-converge when a driver next broadcasts.

#### Scenario: No controller gates a broadcast on role

- **WHEN** any controller emits a broadcast
- **THEN** it SHALL do so without testing this peer's role

#### Scenario: A viewer still interacts locally in xStudio

- **WHEN** a user whose peer holds the `viewer` role scrubs, plays, or annotates in xStudio
- **THEN** xStudio SHALL respond locally as it normally would
- **AND** nothing session-visible SHALL be emitted

#### Scenario: Role reaches the panel through the shared projection

- **WHEN** the xStudio session state panel displays peer roles
- **THEN** it SHALL read them from the shared projection
- **AND** SHALL NOT derive them from controller state
