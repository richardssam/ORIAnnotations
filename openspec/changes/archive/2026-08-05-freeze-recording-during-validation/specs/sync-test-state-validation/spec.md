## ADDED Requirements

### Requirement: Frozen-Playback Checkpoint Validation
The runner SHALL freeze recording playback for the duration of every checkpoint validation — both frame checkpoints and structural state checkpoints — and resume playback once that check has reached a verdict. A checkpoint's expected value SHALL NOT be able to go stale while the checkpoint is being evaluated.

#### Scenario: Playback is frozen while a checkpoint is evaluated
- **WHEN** the runner begins validating a checkpoint
- **THEN** recording playback SHALL be frozen before any app state is sampled
- **AND** SHALL be resumed once the checkpoint has passed or failed

#### Scenario: A slow check does not consume the recording's timeline
- **WHEN** a checkpoint validation takes significantly longer than expected (slow inspector responses, a long convergence poll)
- **THEN** no recorded event SHALL be dispatched during that time
- **AND** subsequent checkpoints SHALL still be evaluated against the app state their own recorded position implies, not a state the recording advanced to while the earlier check was running

#### Scenario: Playback is resumed even when a checkpoint fails
- **WHEN** a checkpoint validation fails, errors, or times out
- **THEN** playback SHALL still be resumed, so a failing check cannot leave the recording permanently frozen

#### Scenario: Freeze duration is reported
- **WHEN** playback is frozen for a checkpoint validation
- **THEN** the runner SHALL log how long playback was frozen, so time removed from real-time replay pacing is visible rather than silent

### Requirement: Point-In-Time Checkpoints Are Retry-Eligible Under Freeze
With playback frozen, a checkpoint's expectation no longer moves while it is being evaluated. Point-in-time checkpoints (frame checkpoints and recorded-snapshot state checkpoints) SHALL therefore be permitted to poll until a bounded deadline before failing, rather than being restricted to a single evaluation.

#### Scenario: A frozen checkpoint may poll for convergence
- **WHEN** a checkpoint fails its first evaluation while playback is frozen
- **THEN** the runner MAY re-evaluate it until a bounded deadline
- **AND** the expected value SHALL remain valid throughout, because playback did not advance

#### Scenario: A genuinely wrong checkpoint still fails
- **WHEN** a checkpoint's expectation is never satisfied within its bounded deadline while frozen
- **THEN** the runner SHALL fail it, with the failure kind and convergence timing reported as for any other timing-eligible check
