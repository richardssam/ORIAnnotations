# session-divergence-repair Specification

## Purpose

Turns a confidently-detected view/playback divergence — a peer displaying a different clip, frame, or timeline than the session — into an actual repair, at the two moments it currently goes uncorrected: right after joining, and continuously afterward as playback continues.

## Requirements

### Requirement: A confirmed join-time mismatch triggers a recovery request

When post-join confirmation records a `MISMATCHED` outcome, the peer SHALL issue one recovery request to re-adopt the session's actual view, in place of silently keeping the mismatched state.

#### Scenario: A joiner on the wrong clip repairs itself

- **WHEN** a peer finishes adopting a join snapshot but post-join confirmation finds it displaying a different clip or frame than the snapshot described
- **THEN** the peer SHALL issue a recovery request
- **AND** SHALL end up displaying the session's actual current view once the request is answered

### Requirement: An unconfirmable join does not trigger repair

A `NOT_CONFIRMED` outcome — the join never reached a checkable state — SHALL NOT trigger a recovery request. Acting on an uncertain signal is the specific failure mode this project has already paid for twice; `NOT_CONFIRMED` exists precisely to be distinguishable from a real, confirmed mismatch, and this capability SHALL preserve that distinction rather than treating "couldn't check" as "wrong."

#### Scenario: An unsettled join does not repair

- **WHEN** post-join confirmation records `NOT_CONFIRMED`
- **THEN** no recovery request SHALL be issued as a result

### Requirement: A sustained live divergence triggers a recovery request; a single one does not

While a session is live (not just joining), a peer that repeatedly receives playback addressed to a timeline it does not have active SHALL issue a recovery request. A single such message SHALL NOT trigger one, since an isolated mismatch can be an artifact of a view switch still in flight rather than a real divergence.

#### Scenario: A peer stuck on the wrong timeline resyncs as the host keeps playing

- **WHEN** a peer receives multiple consecutive playback messages addressed to a timeline it does not have active
- **THEN** the peer SHALL issue a recovery request
- **AND** SHALL end up following the session's actual timeline once the request is answered

#### Scenario: A single in-flight mismatch does not trigger repair

- **WHEN** a peer receives one playback message addressed to a timeline it does not have active, followed by a message that resolves cleanly
- **THEN** no recovery request SHALL be issued for the isolated mismatch

### Requirement: Repair is rate-limited

A peer SHALL NOT issue recovery requests for divergence without bound. Where a repair does not resolve the underlying mismatch, the peer SHALL NOT re-request in a tight loop; it SHALL back off rather than repeatedly hammer the master.

#### Scenario: A repair that doesn't stick does not loop

- **WHEN** a recovery request is answered but the peer's confirmed or detected state still diverges shortly after
- **THEN** the peer SHALL NOT immediately issue another recovery request
- **AND** SHALL wait at least a bounded interval before it may issue another one

### Requirement: Repair reuses the session's existing recovery path

The recovery request issued by this capability SHALL be the same mechanism already used for structural divergence recovery, so it composes with delta buffering and replay unmodified rather than introducing a second, parallel recovery path.

#### Scenario: A view-divergence repair behaves like a structural recovery request

- **WHEN** this capability issues a recovery request
- **THEN** it SHALL be indistinguishable on the wire from a structural-divergence recovery request
- **AND** SHALL be answered and applied through the same handling
