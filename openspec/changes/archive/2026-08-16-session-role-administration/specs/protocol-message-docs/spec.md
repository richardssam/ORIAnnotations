## ADDED Requirements

### Requirement: The role grant message is documented with who applies it

The generated protocol documentation SHALL describe the role grant message on
the same terms as the ownership and identity messages already are: its schema
and event name, the participant and role it carries, and the issuer it names.

The documentation SHALL state the two properties a reader cannot infer from the
field list, because getting either wrong produces a working-looking
implementation that is wrong:

- The message is **broadcast**, not addressed to its target alone, and every
  peer merges the participant-to-role pair into its own copy of the session's
  role memory. A reader who treated it as a unicast to the target would build a
  grant that never reaches the master and so never reaches a later joiner.
- The message is **applied by its target**, which then re-announces. Peer
  announcement remains the sole write path into the peer table. A reader who
  wrote the target's role into the peer table on receipt would add a second,
  racing writer for a value the target is also announcing.

#### Scenario: The grant message appears in the generated protocol reference

- **WHEN** the protocol reference is generated
- **THEN** it SHALL include the role grant message with its schema, event name,
  and fields

#### Scenario: The documentation states how the grant is applied

- **WHEN** the grant message's documentation is rendered
- **THEN** it SHALL state that the message is broadcast and merged by every peer
- **AND** SHALL state that the target applies it and re-announces
