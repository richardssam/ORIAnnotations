## ADDED Requirements

### Requirement: The echo guard covers the whole period a peer is driving playback
A peer that receives a `PLAYBACK_SETTINGS_1.0` message SHALL treat itself as being driven by another peer, and SHALL NOT broadcast its own playhead position, for a short rolling period after that message — regardless of whether it applied the message.

This SHALL hold for messages the receiver deliberately declines to apply (a mismatched `timeline_guid`, a play-state-only update), because the receiver's own playhead can still move during that period for unrelated local reasons — most commonly a selection change resetting it to a clip start — and broadcasting that position would override the seek the driver just issued.

#### Scenario: A dropped message still suppresses the receiver's position broadcasts
- **WHEN** a peer receives a playback message it does not apply
- **THEN** it SHALL NOT broadcast its own playhead position for the guard period
- **AND** the driving peer's position SHALL remain in effect

#### Scenario: A position sampled before the guard armed is not released after
- **WHEN** a peer has a throttled position update pending, and a remote playback message arrives before that update is flushed
- **THEN** the pending update SHALL be discarded rather than broadcast
- **AND** the discard SHALL be reported, so a dropped local scrub is visible rather than silent

#### Scenario: Every broadcast path honours the guard
- **WHEN** any code path would broadcast this peer's playhead position while the guard is armed
- **THEN** that position SHALL be withheld
- **AND** a broadcast carrying information beyond position — a view or mode change — SHALL still be delivered, with only the position withheld

#### Scenario: The guard expires once the peer stops driving
- **WHEN** no playback message has been received for longer than the guard period
- **THEN** the peer SHALL resume broadcasting its own playhead position normally

### Requirement: A peer-driven view transition is not reported as a local play action
xStudio starts playing when a user double-clicks a clip, and broadcasts that so peers follow. A peer-driven view switch produces the identical local transition, so the transition alone SHALL NOT be treated as evidence of a local user action.

A peer SHALL assert `playing=true` on such a transition only when it is not currently being driven by another peer. When it is, the broadcast SHALL NOT assert play, leaving the play state to whichever peer is actually driving.

#### Scenario: A remotely-caused view switch does not start playback everywhere
- **WHEN** a peer's view transitions to an isolated clip as a result of applying a remote message
- **THEN** it SHALL NOT broadcast that playback has started
- **AND** no peer SHALL begin playing as a result

#### Scenario: A genuine local double-click still starts playback on all peers
- **WHEN** a user double-clicks a clip locally, with no peer driving playback
- **THEN** the peer SHALL broadcast that playback has started, as before
