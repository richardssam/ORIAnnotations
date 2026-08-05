## MODIFIED Requirements

### Requirement: Structural Checkpoint Validation
At each state checkpoint the runner SHALL fetch every live client's full state, project it, and diff it against the checkpoint's expected projection using the GUID-keyed structural diff. A non-empty diff SHALL fail the checkpoint with a human-readable report identifying the client and the difference, subject to the bounded retry defined under Bounded Retry For Convergence-Timing Failures. The runner SHALL also support client-vs-client consensus comparison of projections.

#### Scenario: Structural desync fails the test
- **WHEN** a client's projected state differs structurally from the expected projection at a checkpoint, and the desync persists through the bounded retry
- **THEN** the runner SHALL fail the checkpoint and report the offending client and difference

#### Scenario: Matching clients pass
- **WHEN** every client's projection matches the expected projection within tolerance
- **THEN** the runner SHALL pass the checkpoint

#### Scenario: Structural failure is classified
- **WHEN** a structural checkpoint diff fails
- **THEN** it SHALL be reported with failure kind `structural_consensus`, distinguishing it from frame-checkpoint, missing-media, and other failure kinds

## ADDED Requirements

### Requirement: Failure Kind Classification
Every checkpoint or state-comparison failure SHALL be classified into exactly one failure kind: `state_mismatch` (live playhead/clip mismatch), `checkpoint_timeout` (frame checkpoint mismatch), `missing_media`, `log_error_signature`, `annotation_missing`, `structural_consensus`, or `otio_export`. The failure kind SHALL be reported alongside the human-readable failure message rather than replacing it.

#### Scenario: Every failure carries a kind
- **WHEN** a test fails for any reason validated by this capability
- **THEN** the failure result SHALL include one of the defined failure kinds, not only a free-text message

#### Scenario: Convergence-timing kinds are identified
- **WHEN** a failure's kind is `state_mismatch`, `checkpoint_timeout`, or `structural_consensus`
- **THEN** it SHALL be treated as convergence-timing-eligible for the purpose of the bounded retry

#### Scenario: Structural kinds are never timing-eligible
- **WHEN** a failure's kind is `missing_media`, `log_error_signature`, `annotation_missing`, or `otio_export`
- **THEN** it SHALL NOT be retried, since no amount of additional wait time can change the outcome

### Requirement: Bounded Retry For Convergence-Timing Failures
When a convergence-timing-eligible failure occurs on a check with no moving target, the runner SHALL retry the same check exactly once with the original wait/deadline doubled (2x), before treating it as a final result. Non-timing-eligible failures SHALL fail immediately with no retry.

A check has "no moving target" when the state it asserts is not still being changed by an advancing recording — specifically the live peer-vs-peer mismatch watch, the terminal structural-consensus check that runs after playback stops, and (as of the freeze-recording-during-validation change) frame checkpoints and recorded-snapshot state checkpoints validated *while the recording is still playing*, since the runner now freezes recording playback for the full duration of that validation, including any retry.

Freezing is what makes these point-in-time checkpoints retry-eligible: with the target unable to advance mid-check, a retry can only help a check that is still converging (e.g. a structural mutation still applying), not chase a moving target. A checkpoint that legitimately mismatches the frozen state will still fail identically on retry, since nothing about the sampled state can change between attempts.

#### Scenario: Retry converges
- **WHEN** a convergence-timing-eligible check fails, and the retry at 2x the original deadline passes
- **THEN** the test SHALL count as passed for suite exit-code purposes
- **AND** the result SHALL be recorded as "converged late" rather than as an ordinary immediate pass

#### Scenario: Entering a retry is logged, not just its outcome
- **WHEN** a convergence-timing-eligible check fails on its first attempt and the runner begins the bounded retry
- **THEN** it SHALL log that a retry is starting and how long it will wait before giving up, not only the eventual pass/fail outcome — so a human reading the log can distinguish "retried and still failed" from "never retried"

#### Scenario: Retry still fails
- **WHEN** a convergence-timing-eligible check fails, and the retry at 2x the original deadline also fails
- **THEN** the test SHALL fail, with its failure kind preserved from the original check

#### Scenario: Non-timing-eligible failure skips retry
- **WHEN** a failure's kind is not convergence-timing-eligible
- **THEN** the runner SHALL report the failure immediately without attempting a retry

#### Scenario: A frozen mid-playback point-in-time checkpoint is retry-eligible
- **WHEN** a frame checkpoint or recorded-snapshot state checkpoint fails while the recording would otherwise still be advancing
- **THEN** the runner SHALL have frozen recording playback before sampling app state for that checkpoint, so the checkpoint's expected value cannot go stale between the first attempt and the retry
- **AND** the runner MAY retry the check once at 2x the original deadline before failing, per Bounded Retry For Convergence-Timing Failures

### Requirement: Convergence Margin Reporting
Checkpoint and consensus validation SHALL report how much of the allotted wait time was actually used to reach a result, on passing checks as well as failing ones.

#### Scenario: A pass close to its deadline is reported with its margin
- **WHEN** a checkpoint or consensus check passes only shortly before its wait/deadline would have elapsed
- **THEN** the time taken to converge SHALL be included in the result, so a test trending toward its deadline is visible before it starts failing outright

#### Scenario: A fast pass is reported with its margin
- **WHEN** a checkpoint or consensus check passes quickly, well within its allotted wait time
- **THEN** the time taken to converge SHALL still be included in the result, for consistency with the slow-pass case

#### Scenario: Live log output shows convergence timing comparable to the configured delay
- **WHEN** a recording-driven checkpoint (frame checkpoint or structural state checkpoint) is validated, pass or fail
- **THEN** the runner SHALL log the real wall-clock time from the recording's event to the moment the checkpoint was confirmed valid (or given up on), expressed so it is directly comparable to the test's configured `checkpoint_validation_delay` — not only the retry-phase duration — so a developer tuning that setting can read the required delay directly instead of bisecting it by trial and error
