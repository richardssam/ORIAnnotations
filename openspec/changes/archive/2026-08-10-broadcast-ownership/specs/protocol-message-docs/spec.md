## ADDED Requirements

### Requirement: Ownership claim and release messages are documented

The generated protocol documentation SHALL include `CLAIM_OWNERSHIP` and `RELEASE_OWNERSHIP`: their schema, event name, and fields (`category`, `peer_guid`, and — for `CLAIM_OWNERSHIP` — `claim_ts`), generated the same way as every other typed message, with no manually-maintained addition beyond registering the classes.

#### Scenario: Ownership messages appear in the generated reference
- **WHEN** the protocol reference is generated after the ownership messages are registered
- **THEN** it SHALL include `CLAIM_OWNERSHIP` and `RELEASE_OWNERSHIP` with their fields, schema, and event name
- **AND** no manual edit to the generator SHALL have been required beyond registering the message classes

### Requirement: Session state documents the ownership section it carries

The generated protocol documentation for the session state message SHALL describe the `broadcast_ownership` section: that it reports, per leased category, the current owner and remaining lease time, and that the section is omitted when a category has no owner — mirroring the existing documentation of how the message's peer roster and host fields behave when unset.

#### Scenario: The ownership section appears in the generated protocol reference
- **WHEN** the protocol reference is generated
- **THEN** the session state entry SHALL document the `broadcast_ownership` section, its per-category fields, and that an omitted category means no reported owner rather than an explicitly free one
