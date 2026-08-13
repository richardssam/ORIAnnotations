## ADDED Requirements

### Requirement: The peer role field is documented on both paths that carry it

The generated protocol documentation SHALL document the role field on the peer announcement message and on the peer roster carried in session state, and SHALL state that both paths carry it for the same reason identity is carried on both: a peer that has gone quiet becomes known to a joiner only through the roster.

The documentation SHALL state that an absent role means the session's default role, not the most restrictive one, so that a reader implementing against the protocol does not treat a peer running older code as ineligible.

The documentation SHALL NOT describe a separate role message. Role is a field on an existing message, and documenting a message that does not exist would invite an implementation of it.

#### Scenario: The role field appears on both paths in the generated reference

- **WHEN** the protocol reference is generated
- **THEN** the peer announcement entry SHALL document the role field
- **AND** the session state entry SHALL document the role carried on each peer roster entry
- **AND** both SHALL state that an absent role means the session default

### Requirement: Session state documents the role policy section it carries

The generated protocol documentation for the session state message SHALL describe the role policy section: that it carries the session's default role and the identity-keyed role memory, and that the section is omitted when the session declares no policy — mirroring the existing documentation of how the peer roster, host, and ownership sections behave when unset.

The documentation SHALL state that a received absence is ignored rather than treated as an empty policy, so that a peer predating roles cannot clear a session's policy by relaying session state.

#### Scenario: The role policy section appears in the generated reference

- **WHEN** the protocol reference is generated
- **THEN** the session state entry SHALL document the role policy section and its fields
- **AND** SHALL state that an omitted section means no declared policy rather than an empty one

### Requirement: The limit of send-side role enforcement is documented

The generated protocol documentation SHALL state that role is enforced by the sending peer and is not validated on receipt, so that an implementer reading the protocol does not assume messages are filtered by the sender's declared role.

#### Scenario: Enforcement model is stated in the reference

- **WHEN** the protocol reference is generated
- **THEN** it SHALL state that role is enforced send-side
- **AND** SHALL state that a receiving peer applies a message without checking the sender's role
