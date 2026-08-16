## ADDED Requirements

### Requirement: The session panel shows when this peer's structure has diverged

Both host applications' session panels SHALL show when this peer's structure has
diverged from the session, and SHALL distinguish a divergence that is being
repaired from one that cannot be.

The panel is the only place a user learns why their deletion came back. Without
it, correct recovery presents as the application undoing their work at random,
and an unrecoverable divergence presents as nothing at all — the panel reports a
healthy session while the user looks at content no one else has.

The two conditions are distinguished because the user's options differ: a
repairing peer needs no action, while a peer that cannot reach an authoritative
state is showing content it must not be trusted to review from.

#### Scenario: A repairing divergence is shown
- **WHEN** this peer is diverged and rebuilding
- **THEN** the panel SHALL show that the session is being resynchronised

#### Scenario: An unrecoverable divergence is shown distinctly
- **WHEN** this peer is diverged and cannot obtain the session's state
- **THEN** the panel SHALL show that this peer's content may not match the session
- **AND** the indication SHALL be distinguishable from a repair in progress

#### Scenario: A synchronised peer shows neither
- **WHEN** this peer is synchronised
- **THEN** the panel SHALL show no divergence indication

#### Scenario: Both hosts present the condition
- **WHEN** the condition is reported in either host application
- **THEN** that application's session panel SHALL surface it
