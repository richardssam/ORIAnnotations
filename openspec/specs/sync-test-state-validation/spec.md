# sync-test-state-validation Specification

## Purpose
TBD - created by archiving change record-periodic-state-snapshots. Update Purpose after archive.
## Requirements
### Requirement: Time-Ordered Snapshot Storage In Recordings
The player's `load_recording` SHALL retain all `STATE_SNAPSHOT` events as a list ordered by `time_offset`, while still exposing the first snapshot to answer joiners' `STATE_REQUEST` during replay. Mid-stream snapshots SHALL NOT be replayed as session events.

#### Scenario: Multiple snapshots are retained
- **WHEN** a recording contains several `STATE_SNAPSHOT` events at different offsets
- **THEN** `load_recording` SHALL retain all of them ordered by `time_offset`
- **AND** SHALL NOT discard earlier snapshots by overwriting

#### Scenario: First snapshot still seeds joiners
- **WHEN** a peer requests state during replay
- **THEN** the player SHALL answer with the first recorded snapshot as it does today

#### Scenario: Mid-stream snapshots are not broadcast
- **WHEN** the player advances through the event timeline
- **THEN** it SHALL NOT send any recorded `STATE_SNAPSHOT` as a playback event

### Requirement: Client Full-State Inspection
The test inspector SHALL expose a `get_full_state` operation that returns the client manager's current state as a `StateSnapshot`-shaped dict, suitable for `project_state`. This is in addition to the existing lightweight `/state` (`clip`, `frame`, `playing`).

#### Scenario: Client reports its full projected state
- **WHEN** the runner requests full state from a client during replay
- **THEN** the inspector SHALL return a `StateSnapshot`-shaped dict reflecting the client manager's current timelines, active timeline, playback, and display

### Requirement: State Checkpoint Derivation
The runner SHALL derive **state checkpoints** from a recording's periodic `STATE_SNAPSHOT` events, each carrying the snapshot's `time_offset` and its canonical projection as the expectation. State checkpoints SHALL coexist with the existing frame checkpoints; recordings without periodic snapshots SHALL still validate via frame checkpoints only.

#### Scenario: Snapshots become checkpoints
- **WHEN** a recording contains periodic `STATE_SNAPSHOT` events
- **THEN** the runner SHALL produce one state checkpoint per snapshot keyed by its `time_offset`

#### Scenario: Recording without periodic snapshots still validates
- **WHEN** a recording contains only the startup snapshot
- **THEN** the runner SHALL fall back to frame-only checkpoint validation without error

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

### Requirement: Frame assertions are made only against a parked playhead
A frame is comparable only when the playhead is stationary. When an app reports that it is playing, its frame is changing continuously and no single value is correct, so the runner SHALL NOT compare it — and SHALL NOT report the resulting absence of a comparison as a pass.

#### Scenario: A moving playhead is not compared
- **WHEN** an app reports that it is playing at the moment a frame checkpoint is evaluated
- **THEN** the runner SHALL NOT compare that app's frame against the expectation

#### Scenario: A playhead that never parks is reported as such
- **WHEN** a frame assertion is due and the playhead is still playing when the deadline expires
- **THEN** the runner SHALL fail the check stating that playback was still active and no frame assertion was possible
- **AND** SHALL NOT report a frame mismatch, which would misdescribe the fault

#### Scenario: Apps report their real playback state
- **WHEN** the runner reads an app's state
- **THEN** that state SHALL carry the app's actual playback status, not a fixed value

### Requirement: Unreadable state is never reported as a pass
A check that could not read the value it was meant to compare SHALL NOT count as satisfied. Missing data and correct data must be distinguishable in the result.

#### Scenario: A failed sub-read does not disable unrelated assertions
- **WHEN** one part of an app's state cannot be read
- **THEN** the remaining parts SHALL still be reported
- **AND** a failure to read one value SHALL NOT silently disable assertions that depend on another

#### Scenario: Observed values are reported on success and failure alike
- **WHEN** the runner evaluates a frame check
- **THEN** it SHALL report what every app actually reported — frame, timeline, and playback state — not only the expected value
- **AND** a failure naming one app SHALL still show the others, so "one app is wrong" is distinguishable from "the expectation is wrong"

### Requirement: Every commanded seek is verified
A script-driven test SHALL verify each `set_frame` it issues, not only the last one. An unverified intermediate seek can fail to propagate and then be masked by a later seek that lands, leaving the peers looking synchronised at the end while having missed one in the middle.

#### Scenario: An intermediate seek that does not propagate fails the test
- **WHEN** a seek is issued and a peer does not follow it
- **THEN** the test SHALL fail at that seek
- **AND** SHALL identify which seek was not followed

#### Scenario: Waiting for convergence is reported
- **WHEN** a check does not pass on its first evaluation and is retried until a deadline
- **THEN** the runner SHALL report that it is waiting and what the outcome was
- **AND** a check that retried SHALL be distinguishable in the log from one that passed immediately

