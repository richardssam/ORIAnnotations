## ADDED Requirements

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
At each state checkpoint the runner SHALL fetch every live client's full state, project it, and diff it against the checkpoint's expected projection using the GUID-keyed structural diff. A non-empty diff SHALL fail the checkpoint with a human-readable report identifying the client and the difference. The runner SHALL also support client-vs-client consensus comparison of projections.

#### Scenario: Structural desync fails the test
- **WHEN** a client's projected state differs structurally from the expected projection at a checkpoint
- **THEN** the runner SHALL fail the checkpoint and report the offending client and difference

#### Scenario: Matching clients pass
- **WHEN** every client's projection matches the expected projection within tolerance
- **THEN** the runner SHALL pass the checkpoint
