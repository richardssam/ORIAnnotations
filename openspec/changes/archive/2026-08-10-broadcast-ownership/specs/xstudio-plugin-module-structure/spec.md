## ADDED Requirements

### Requirement: Remote structural applies are filtered by an apply-scope guard, not a wall-clock window
`StructureSyncController` SHALL suppress the echo of a remote structural apply using an apply-scope guard tied to the extent of that apply, rather than a fixed-duration wall-clock suppression window. The guard SHALL remain active for exactly as long as the remote structural apply it scopes is in progress, including applies that take an extended, variable time (such as a full timeline rebuild), rather than for a fixed duration chosen to outlast the typical case.

#### Scenario: A long remote rebuild stays suppressed for its full duration
- **WHEN** a remote structural message triggers a rebuild that takes longer than the previous fixed suppression window
- **THEN** the controller's own structural poll SHALL NOT treat that rebuild's in-progress changes as a local edit for any part of its duration

#### Scenario: Suppression ends when the apply ends, not on a timer
- **WHEN** a remote structural apply completes
- **THEN** the apply-scope guard SHALL clear at that point
- **AND** SHALL NOT remain active for a fixed duration afterward

### Requirement: A bounded horizon filters late-arriving asynchronous echo callbacks
Because xStudio delivers some structural and playback change callbacks asynchronously, arriving after the apply scope that caused them has exited, `claim_category()` SHALL be a no-op for a bounded horizon after the controller stamps a remote apply, so a late-arriving echo callback cannot trigger a claim. This horizon SHALL be the only remaining time-window mechanism in the claim path; its failure mode SHALL be limited to an unnecessary pending claim, never a re-broadcast.

#### Scenario: A late async callback within the horizon does not claim
- **WHEN** an asynchronous callback attributable to a remote apply arrives within the horizon after that apply was stamped
- **THEN** any `claim_category()` call it triggers SHALL be a no-op

#### Scenario: A callback outside the horizon may claim normally
- **WHEN** an asynchronous callback arrives after the horizon has elapsed
- **THEN** `claim_category()` SHALL evaluate normally, as it would for any other input-driven call
