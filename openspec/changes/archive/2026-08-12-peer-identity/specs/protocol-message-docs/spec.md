## ADDED Requirements

### Requirement: Peer identity fields are documented on both paths that carry them

The generated protocol documentation SHALL describe the identity fields carried
on the peer announcement message and in the peer roster carried by session
state, and SHALL state that the two carry the same fields.

The documentation SHALL state that identity is self-declared and unverified, so
that a reader does not mistake a named peer for an authenticated one, and SHALL
state that the fields are optional — a peer that omits them is a valid peer.

The documentation SHALL NOT describe a displayed name as a wire field, since it
is derived from the transmitted fields rather than transmitted.

#### Scenario: Identity fields appear in the generated protocol reference

- **WHEN** the protocol reference is generated
- **THEN** the peer announcement entry SHALL document each identity field
- **AND** the session state entry SHALL document the same fields on its peer
  roster
- **AND** both SHALL state that the fields are optional

#### Scenario: The trust boundary is documented

- **WHEN** the protocol reference is generated
- **THEN** the identity fields SHALL be described as self-declared and
  unverified
