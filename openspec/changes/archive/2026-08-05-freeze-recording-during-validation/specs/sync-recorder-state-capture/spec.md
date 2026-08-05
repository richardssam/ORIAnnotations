## ADDED Requirements

### Requirement: Pausable Procedural Playback
The player SHALL support pausing and resuming non-blocking procedural playback. While paused it SHALL NOT dispatch recorded events, SHALL continue to service incoming peer requests (so a joining peer is still answered with the recorded state snapshot), and SHALL preserve its logical event timeline — no event may be skipped, duplicated, or re-timed relative to its neighbours as a result of the pause.

#### Scenario: Paused playback dispatches no events
- **WHEN** playback is paused and the player is ticked repeatedly
- **THEN** it SHALL NOT dispatch any recorded event, regardless of how much wall-clock time elapses

#### Scenario: Peer requests are still served while paused
- **WHEN** a peer requests state while playback is paused
- **THEN** the player SHALL answer that request as it would during normal playback

#### Scenario: Resuming preserves the logical timeline
- **WHEN** playback is paused for some duration and then resumed
- **THEN** the next event dispatched SHALL be the one that was next before the pause
- **AND** the interval between subsequent events SHALL match their recorded spacing, unaffected by the pause duration

#### Scenario: The logical clock does not advance while paused
- **WHEN** playback is paused for some duration
- **THEN** the player's reported playback offset SHALL be the same immediately after resuming as it was at the moment of the pause

#### Scenario: Pause and resume are idempotent
- **WHEN** pause is requested on already-paused playback, or resume on playback that is not paused
- **THEN** the call SHALL be a no-op and SHALL NOT corrupt the logical timeline
