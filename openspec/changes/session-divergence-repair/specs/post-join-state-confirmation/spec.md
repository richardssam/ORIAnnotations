## MODIFIED Requirements

### Requirement: Confirmation reports and does not repair

Confirmation itself — the act of comparing what this peer is displaying against the snapshot it joined with — SHALL NOT alter this peer's state, request state, or broadcast anything. It only records an outcome.

A `MISMATCHED` outcome MAY subsequently be acted on by `session-divergence-repair`, which issues a bounded, rate-limited recovery request specifically in response to that recorded outcome. This is a separate capability reading a recorded fact, not the confirmation step itself reaching further than it should — the distinction matters because it keeps the same caution this requirement was written to enforce: repair is conditioned on a *confirmed* mismatch, never on `NOT_CONFIRMED` (see `session-divergence-repair`'s "An unconfirmable join does not trigger repair").

Detection stays separated from response for the reason this requirement was written for: a repair driven by a detector that has not yet earned confidence is worse than the condition it repairs. What changes here is that a `MISMATCHED` result — not `NOT_CONFIRMED`, and not the confirmation step acting on its own account — has now earned that confidence.

#### Scenario: The confirmation step itself changes nothing

- **WHEN** the confirmation step runs and compares displayed state against the join snapshot
- **THEN** the comparison itself SHALL NOT alter this peer's state, request state, or broadcast anything
- **AND** it SHALL only record the outcome

#### Scenario: A mismatch may be repaired by a separate capability

- **WHEN** the confirmation step records a `MISMATCHED` outcome
- **THEN** `session-divergence-repair` MAY issue a recovery request in response
- **AND** that request SHALL be attributable to the repair capability reading the recorded outcome, not to the confirmation step itself

#### Scenario: A confirmation is still not a broadcast

- **WHEN** a confirmation runs
- **THEN** no peer other than this one SHALL be affected by the confirmation step itself
