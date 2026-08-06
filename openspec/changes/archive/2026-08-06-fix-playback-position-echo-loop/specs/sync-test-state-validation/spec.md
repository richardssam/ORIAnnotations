## ADDED Requirements

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
