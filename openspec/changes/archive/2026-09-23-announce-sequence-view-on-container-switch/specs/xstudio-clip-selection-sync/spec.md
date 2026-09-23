## ADDED Requirements

### Requirement: Opening a sequence in xStudio is announced as a sequence view

When the user opens a published sequence in xStudio (for example by double-clicking it in the bin), the viewed container changes from a Playlist to that sequence's Timeline. Pinned Source Mode does not change. xStudio SHALL announce this as `view_mode: "sequence"`, with that sequence's sync `timeline_guid`. It SHALL NOT announce it as an isolation (`view_mode: "source"`) of the clip under the playhead. The announcement SHALL NOT depend on the order in which xStudio's viewport, media-change and selection events arrive. It SHALL still be made when the media-change event for the switch is suppressed as scan-through.

The announcement SHALL be made only for a Timeline that is a published sync sequence. A flat playlist's internal Timeline, which xStudio's viewport reports once a playlist starts playing, is not a sequence. Neither is a Timeline the session hasn't been told about. The announcement SHALL NOT be made when this peer already announced that same sequence view, and SHALL NOT be made when the switch was caused by applying a peer's message.

#### Scenario: double-clicking a sequence from the bin switches the peer to the sequence
- **WHEN** the xStudio host is viewing the bin (a Playlist) and double-clicks a published sequence
- **THEN** xStudio broadcasts `view_mode: "sequence"` with that sequence's `timeline_guid`
- **AND** it does not broadcast `view_mode: "source"` for the clip under the playhead
- **AND** the receiving peer switches to the sequence view and follows subsequent scrubs of it

#### Scenario: the media-change for the switch arrives before the selection poll
- **WHEN** xStudio reports the new on-screen media for the opened sequence before the plugin's selection poll has observed the container change
- **THEN** the media-change is classified against the sequence's Timeline container, not the previously viewed Playlist

#### Scenario: the switch is still announced when the media-change is suppressed
- **WHEN** the media-change event for the opened sequence is suppressed as scan-through (the playhead was moving)
- **THEN** xStudio still broadcasts `view_mode: "sequence"` for that sequence once it observes the container change

#### Scenario: playing a flat playlist is not announced as a sequence
- **WHEN** the user starts playback in a flat playlist and xStudio's viewport starts reporting that playlist's internal Timeline
- **THEN** xStudio does not broadcast `view_mode: "sequence"` because of that container change

#### Scenario: a peer-driven switch to a sequence is not re-announced
- **WHEN** this peer switches to a sequence because it applied a peer's `view_mode: "sequence"` message
- **THEN** it does not broadcast its own `view_mode: "sequence"` assertion for that switch

#### Scenario: an already-announced sequence is not announced twice
- **WHEN** the switch to a sequence has already been broadcast as `view_mode: "sequence"` for that timeline
- **THEN** observing the same container change again does not produce a second broadcast
