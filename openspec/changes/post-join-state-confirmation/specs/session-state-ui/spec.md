## ADDED Requirements

### Requirement: The session panel reports whether this peer confirmed the state it joined with

Both host applications' session panels SHALL show whether this peer's joined
state was confirmed against the snapshot it was sent.

Three outcomes SHALL be distinguishable: confirmed, mismatched, and not
confirmed. Collapsing "not checked" into either of the others reproduces the
silence this exists to remove — a peer that never finished joining would
otherwise present exactly as one that joined correctly.

A reported mismatch SHALL be presented as information about this peer's own
state, not as an error attributed to the session or to another participant. The
peer that is wrong is this one, and a message implying otherwise sends the user
looking in the wrong place.

#### Scenario: A confirmed join is shown as confirmed

- **WHEN** this peer's joined state matched the snapshot it was sent
- **THEN** the panel SHALL show the state as confirmed

#### Scenario: A mismatch is shown, with what differed

- **WHEN** this peer's joined state did not match the snapshot
- **THEN** the panel SHALL show that it did not match
- **AND** SHALL make the differing fields available to the user

#### Scenario: An unchecked join is distinguishable from a confirmed one

- **WHEN** the confirmation could not be performed
- **THEN** the panel SHALL show the state as not confirmed
- **AND** SHALL NOT present it as either confirmed or mismatched

#### Scenario: A peer that never joined shows no outcome

- **WHEN** this peer has not joined a session
- **THEN** no confirmation outcome SHALL be shown

#### Scenario: The outcome comes from the shared projection

- **WHEN** either panel displays the confirmation outcome
- **THEN** it SHALL read that outcome from the shared session-state projection
  rather than deriving it locally
