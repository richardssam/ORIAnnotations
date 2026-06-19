## ADDED Requirements

### Requirement: Canonical State Projection
The system SHALL provide a single `project_state` function that reduces a `StateSnapshot`-shaped dict to a canonical, comparable structure. The function SHALL be the one source of truth for what "in sync" means and SHALL be importable by both the record/test side and the OpenRV/xStudio client integrations. The projection SHALL preserve: the set of timelines keyed by timeline GUID; the active timeline GUID; per timeline, each track's ordered list of `(clip_guid, normalized_name)`; the current frame from `playback_state.current_time.value`; and the display view/display target plus annotation enablement.

#### Scenario: Two states with the same logical content project equal
- **WHEN** two snapshots describe the same timelines, clip order, active timeline, frame, and display target but differ only in media URLs or color metadata
- **THEN** `project_state` SHALL produce equal canonical structures for both

#### Scenario: Reordered clips project unequal
- **WHEN** two snapshots hold the same clips in different track order
- **THEN** their canonical projections SHALL differ

### Requirement: Representation Fields Dropped
The projection SHALL drop fields that legitimately differ across applications or machines and do not indicate desync: media `target_url`, color/OCIO metadata, `available_range`, any `*timestamp*` field, and device-centric viewport values (pan, zoom, exposure). The `playing` flag SHALL NOT be part of equality.

#### Scenario: Per-machine media paths do not cause a mismatch
- **WHEN** two clients resolve the same media to different absolute `target_url`s
- **THEN** the canonical projections SHALL NOT differ on that account

#### Scenario: Color metadata differences do not cause a mismatch
- **WHEN** one client carries OCIO/color metadata the other resolves differently
- **THEN** that metadata SHALL be absent from the canonical projection

### Requirement: GUID-Keyed Structural Diff
The system SHALL provide a diff over canonical projections that is keyed on stable GUIDs (timeline GUID, clip `metadata.sync.guid`) rather than name or position, and reports human-readable differences (missing/extra timeline, missing/extra clip, reordered clips, active-timeline mismatch, frame difference beyond tolerance). The frame comparison SHALL apply a configurable tolerance; a missing frame on either side SHALL be treated as "not asserted" rather than a mismatch.

#### Scenario: Missed insert is reported
- **WHEN** the expected projection contains a clip GUID absent from a client's projection
- **THEN** the diff SHALL report that clip as missing on that client

#### Scenario: Frame within tolerance passes
- **WHEN** the client frame differs from the expected frame by no more than the tolerance
- **THEN** the diff SHALL NOT report a frame mismatch

#### Scenario: Unasserted frame is skipped
- **WHEN** either side has no `playback_state.current_time.value`
- **THEN** the diff SHALL NOT report a frame mismatch
