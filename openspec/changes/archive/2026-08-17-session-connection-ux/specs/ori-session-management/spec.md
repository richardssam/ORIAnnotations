## ADDED Requirements

### Requirement: The join and create dialogs display the resolved account identity, read-only

The create and join dialogs SHALL display the resolved account identity — the underlying account/login identity, distinct from the editable display-name override — as read-only text, so a user can confirm which account they are about to connect as before completing the action.

This is a separate field from the existing editable display-name override (`ori-session-management`: "A user may declare their identity when joining"), which remains editable for shared-workstation and loaner-seat use. The account identity field does not gain an editing path through the dialog; an account-level override, where one exists, continues to be set the way it is today.

#### Scenario: The dialog shows the resolved account identity

- **WHEN** a user opens the create or join dialog
- **THEN** the dialog SHALL display the resolved account identity as read-only text, separate from the editable display-name field

#### Scenario: The account identity field cannot be edited

- **WHEN** a user attempts to edit the account identity field
- **THEN** the dialog SHALL NOT accept the edit

#### Scenario: An account-level override is reflected, not the raw OS account

- **WHEN** an account-level override is already in effect for this process
- **THEN** the dialog SHALL display the overridden account identity, not the unoverridden OS account
