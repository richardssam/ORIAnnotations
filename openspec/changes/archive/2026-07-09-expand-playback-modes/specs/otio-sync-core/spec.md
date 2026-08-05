## MODIFIED Requirements

### Requirement: Settings Messages Declare Fields but Tolerate Extras
The system SHALL provide message classes for `PLAYBACK_SETTINGS_1.0/SET` and `DISPLAY_SETTINGS_1.0/SET` that document their known fields, while accepting messages that contain additional, unrecognized fields without failure. This preserves interoperability with independent producers that may emit extra keys.

#### Scenario: Known settings fields are documented
- **WHEN** the playback and display settings message classes are defined
- **THEN** they SHALL enumerate the established fields (playback: `playing`, `current_time`, `playback_mode`, `timeline_guid`, `sync_timestamp`; display: `pan`, `zoom`, `exposure`, `channel`, `sync_timestamp`).

#### Scenario: Extra fields do not break parsing
- **WHEN** a settings message arrives containing fields beyond the declared set
- **THEN** the message SHALL be parsed and applied without error, and unrecognized fields SHALL be ignored rather than rejected.

## ADDED Requirements

### Requirement: Playback Mode Is a Three-Way Wire Value
`PLAYBACK_SETTINGS_1.0/SET`'s `playback_mode` field SHALL be one of the string values `"play-once"`, `"loop"`, or `"ping-pong"`, replacing the prior `looping: bool` field. Each host SHALL translate between this wire value and its own native play-mode representation; no host-neutral mode enum SHALL be shared beyond these three wire strings.

#### Scenario: OpenRV translates its native play mode to the wire value
- **WHEN** OpenRV broadcasts a playback state update
- **THEN** `playback_mode` SHALL be derived directly from `rv.commands.playMode()` (`PlayLoop` → `"loop"`, `PlayOnce` → `"play-once"`, `PlayPingPong` → `"ping-pong"`), with no intermediate boolean collapse

#### Scenario: xStudio translates its native play mode to the wire value
- **WHEN** xStudio broadcasts a playback state update
- **THEN** `playback_mode` SHALL be derived directly from the active playhead's native `"Loop Mode"` attribute (`"Loop"` → `"loop"`, `"Play Once"` → `"play-once"`, `"Ping Pong"` → `"ping-pong"`), with no intermediate boolean collapse

#### Scenario: A received playback_mode is applied to the local native engine
- **WHEN** a peer's `PLAYBACK_SETTINGS_1.0/SET` message carrying a `playback_mode` value is received
- **THEN** the receiving host SHALL set its own native play-mode attribute to the corresponding value, so both peers' native engines (not just their synced state) agree on the mode

#### Scenario: Ping-pong playback reverses direction without additional sync messages
- **WHEN** a host's native play mode is set to ping-pong and the playhead reaches either end of its playback range
- **THEN** the host's own native engine SHALL reverse playback direction on its own, and the existing per-frame position broadcast SHALL continue to report the current frame regardless of direction, requiring no new wire message beyond the existing `current_time` field
