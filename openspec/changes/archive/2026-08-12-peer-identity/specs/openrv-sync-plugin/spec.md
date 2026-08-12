## ADDED Requirements

### Requirement: The session dialog offers an identity override

OpenRV's session create and join dialogs SHALL offer an optional identity field, pre-filled with the identity resolved from the local machine, which the user may replace before connecting.

Leaving the field as presented SHALL be equivalent to supplying no override. The dialog SHALL NOT block connection on the field being populated.

#### Scenario: Connecting without touching the field

- **WHEN** a user connects without editing the identity field
- **THEN** the machine-resolved identity SHALL be used

#### Scenario: Connecting with an edited identity

- **WHEN** a user edits the identity field before connecting
- **THEN** peers SHALL see the edited identity
- **AND** it SHALL be marked as user-entered
