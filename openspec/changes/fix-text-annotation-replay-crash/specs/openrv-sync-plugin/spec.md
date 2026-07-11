## MODIFIED Requirements

### Requirement: Session State Management

The plugin SHALL support runtime-configurable session identity. The session name and RabbitMQ host SHALL be determined at connect time from either the `ORI_SESSION` environment variable or interactive user input, replacing the previously hardcoded `SYNC_SESSION_ID` constant.

#### Scenario: Late joiner synchronization

- **WHEN** a new instance joins an active session
- **THEN** it SHALL request a full state snapshot from the Master peer
- **AND** it SHALL rebuild its local RV session (media sources, timeline) based on the received snapshot.

#### Scenario: A failed annotation replay does not block the rest of the join

- **WHEN** the joining instance replays the snapshot's annotation clips and one clip's event raises an exception (e.g. an unexpected event shape, or a lookup that fails for that specific clip)
- **THEN** that one event's replay SHALL be skipped and logged
- **AND** every other event, clip, and kind SHALL still be replayed
- **AND** the join SHALL still apply playback state, display state, and color sync, none of which SHALL be silently skipped as a side effect of the annotation-replay failure

#### Scenario: Session name from ORI_SESSION

- **WHEN** `ORI_SESSION` is set at launch
- **THEN** the plugin SHALL parse `[host:]session_name` and call `connect_to_session(host, name)` automatically, with no hardcoded fallback name

#### Scenario: Session name from interactive menu

- **WHEN** `ORI_SESSION` is not set and the user selects Create or Join Session from the OTIO Sync menu
- **THEN** the plugin SHALL present a two-field dialog and call `connect_to_session(host, name)` on confirm
