## ADDED Requirements

### Requirement: The session dialog offers an identity override

xStudio's session dialog SHALL offer an optional identity field, pre-filled with the identity resolved from the local machine, which the user may replace before connecting.

Leaving the field as presented SHALL be equivalent to supplying no override. The dialog SHALL NOT block connection on the field being populated.

The resolved and overridden identities SHALL come from the shared sync core rather than from an xStudio-specific implementation, on the same terms as every other protocol behaviour the two host applications share.

#### Scenario: Connecting without touching the field

- **WHEN** a user connects without editing the identity field
- **THEN** the machine-resolved identity SHALL be used

#### Scenario: Connecting with an edited identity

- **WHEN** a user edits the identity field before connecting
- **THEN** peers SHALL see the edited identity
- **AND** it SHALL be marked as user-entered

#### Scenario: Identity resolution is not reimplemented per host

- **WHEN** the plugin resolves the local identity
- **THEN** it SHALL obtain it from the shared sync core
- **AND** SHALL NOT derive identity fields itself
