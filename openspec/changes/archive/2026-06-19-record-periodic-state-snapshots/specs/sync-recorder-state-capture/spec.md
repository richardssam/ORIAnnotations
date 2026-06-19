## ADDED Requirements

### Requirement: Passive Snapshot Capture
The recorder SHALL record every `STATE_SNAPSHOT` message observed on the session exchange, regardless of its `target_guid`, as an event in the recording with its `time_offset`. This is always active once recording starts and SHALL NOT depend on the opt-in active-capture mode. The initial join-handshake snapshot (targeted at the recorder) SHALL continue to satisfy `_snapshot_captured` as today.

#### Scenario: A peer joins mid-session
- **WHEN** another peer joins and the master broadcasts a `STATE_SNAPSHOT` targeted at that peer
- **THEN** the recorder SHALL record that snapshot event with its `time_offset`
- **AND** SHALL NOT require the snapshot to be targeted at the recorder

#### Scenario: Passive capture costs no extra traffic
- **WHEN** passive capture records a snapshot
- **THEN** the recorder SHALL NOT emit any `WHO_IS_MASTER` or `STATE_REQUEST` message as a result

### Requirement: Opt-In Active Periodic Capture
The recorder SHALL accept a `capture_periodic_state` option (default off) that, when enabled, requests a fresh `STATE_SNAPSHOT` from the master at settle points during recording. When disabled, the recorder's emitted-traffic behaviour SHALL be identical to today (single startup handshake only).

#### Scenario: Default behaviour is unchanged
- **WHEN** the recorder runs without `capture_periodic_state`
- **THEN** it SHALL request a snapshot only during the initial startup handshake
- **AND** SHALL otherwise behave as a passive tap

#### Scenario: Active capture requests a snapshot at a settle point
- **WHEN** `capture_periodic_state` is enabled and the stream has been silent for at least the configured `min_silence`
- **AND** at least `min_interval` has elapsed since the last active request
- **AND** no `STATE_SNAPSHOT` arrived passively within that window
- **THEN** the recorder SHALL send a `STATE_REQUEST` to the cached master GUID

### Requirement: Bounded Perturbation
Active periodic capture SHALL be bounded by a minimum silence threshold (`min_silence`) before requesting and a minimum interval (`min_interval`) between requests, and SHALL suppress an active request when a snapshot has been captured passively within the current window. These bounds exist so that recording a live review session does not flood it with state requests or large snapshot payloads.

#### Scenario: No request during active playback
- **WHEN** the stream is continuously busy (no silence gap reaching `min_silence`)
- **THEN** the recorder SHALL NOT issue an active `STATE_REQUEST`

#### Scenario: Requests are rate-limited
- **WHEN** two settle points occur closer together than `min_interval`
- **THEN** the recorder SHALL issue at most one active `STATE_REQUEST` across them

### Requirement: Cached Master Discovery
After the initial handshake the recorder SHALL cache the master GUID and, for active periodic requests, send `STATE_REQUEST` directly without re-broadcasting `WHO_IS_MASTER`. It SHALL fall back to `WHO_IS_MASTER` re-discovery only if an active `STATE_REQUEST` times out without a snapshot.

#### Scenario: Subsequent request skips discovery
- **WHEN** the recorder issues an active `STATE_REQUEST` after a successful initial handshake
- **THEN** it SHALL target the cached master GUID
- **AND** SHALL NOT emit a `WHO_IS_MASTER` message

#### Scenario: Re-discovery on timeout
- **WHEN** an active `STATE_REQUEST` receives no matching `STATE_SNAPSHOT` within the request timeout
- **THEN** the recorder SHALL re-issue `WHO_IS_MASTER` to rediscover the master before retrying
