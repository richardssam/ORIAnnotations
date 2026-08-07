## ADDED Requirements

### Requirement: The departure message is documented as best-effort

The generated protocol documentation SHALL describe the peer departure message:
its schema and event name, the peer it identifies, and that it is emitted when a
peer closes its session.

The documentation SHALL state that delivery is **best-effort** and that
correctness does not depend on it — a departure that is never delivered is
resolved by peer inactivity instead. A reader who assumes the message is
guaranteed would be entitled to skip the inactivity path, which is the one that
covers crashes.

#### Scenario: Departure message appears in the generated protocol reference

- **WHEN** the protocol reference is generated
- **THEN** it SHALL include the departure message with its schema, event name,
  and fields
- **AND** SHALL state that the message is best-effort and backed by an
  inactivity fallback

### Requirement: The announcement message documents its periodic cadence

The generated protocol documentation for the peer announcement message SHALL
describe that it is sent on joining and periodically thereafter, and SHALL NOT
describe an answering behaviour that no longer exists.

The existing documentation explains that answers are suppressed to avoid an
announcement storm, and that answering is what lets a late joiner discover peers
that have gone quiet. Both statements SHALL be replaced rather than left to
contradict the implementation: periodic announcement is now what makes a quiet
peer discoverable, and the cascade the suppression guarded against no longer has
a mechanism to occur through.

#### Scenario: Announcement cadence is documented

- **WHEN** the protocol reference is generated
- **THEN** the peer announcement entry SHALL describe both emission occasions:
  on joining, and periodically
- **AND** SHALL NOT refer to answering another peer's announcement
- **AND** SHALL identify periodic announcement as what makes a quiet peer
  discoverable

### Requirement: Session state documents the peer roster it carries

The generated protocol documentation for the session state message SHALL
describe the peer roster it carries: that it identifies the peers present when
the state was taken, and that it exists so a joining peer learns the peer set
without other peers answering its announcement.

The documentation SHALL state that the roster is **not** the only means of
discovery — a joiner that receives no session state learns peers from their
periodic announcements instead.

#### Scenario: Roster appears in the generated protocol reference

- **WHEN** the protocol reference is generated
- **THEN** the session state entry SHALL document the peer roster field
- **AND** SHALL state that periodic announcement is the fallback when no session
  state is received
