## MODIFIED Requirements

### Requirement: Synchronized Playback
The plugin SHALL synchronize the playhead (frame) and playback state (play/stop) between all instances.

The broadcast frame is expressed relative to the view the sender is displaying, so the accompanying timeline guid SHALL identify **that** view — the isolated clip's own timeline when a single clip is displayed, the sequence's timeline when the sequence is displayed. A position SHALL NOT be attributed to a timeline the sender is not displaying, because a receiver has no other way to tell which coordinate space a frame belongs to and would apply it to the wrong one.

When the displayed view has no timeline shared with the session, the broadcast SHALL carry no timeline guid rather than substituting the session's active timeline. "A position in a view you do not have" and "a position in your sequence" are different claims, and only the first one is true.

#### Scenario: Scrubbing while paused

- **WHEN** a paused peer moves its playhead to a new frame
- **THEN** it SHALL broadcast the new playback state, carrying the frame, the playing flag, the playback mode, and the timeline guid
- **AND** every other peer SHALL move its playhead to the corresponding frame on that timeline

#### Scenario: Play and stop propagate

- **WHEN** a peer starts or stops playback
- **THEN** every other peer SHALL enter the same playing/stopped state

#### Scenario: Applying a remote state does not echo

- **WHEN** a peer applies a playback state received from another peer
- **THEN** it SHALL NOT re-broadcast that state back to the session

#### Scenario: An isolated clip is labelled with its own timeline

- **WHEN** OpenRV is displaying a single isolated clip and broadcasts a playback state
- **THEN** the timeline guid SHALL be that clip's own timeline guid
- **AND** SHALL NOT be the guid of the sequence the clip belongs to

#### Scenario: A position from an unshared view is not attributed to a shared timeline

- **WHEN** OpenRV is displaying media that has no timeline shared with the session
- **THEN** the broadcast SHALL carry no timeline guid
- **AND** peers SHALL NOT move their playheads in response to it

#### Scenario: Sequence views are unaffected

- **WHEN** OpenRV is displaying a sequence and broadcasts a playback state
- **THEN** the timeline guid SHALL be that sequence's timeline guid
- **AND** peers displaying the same sequence SHALL move their playheads to the corresponding frame
