## MODIFIED Requirements

### Requirement: Applying an incoming clip change does not echo back to the sender
Applying an incoming clip isolation/highlight SHALL NOT itself trigger a new outbound `PLAYBACK_SETTINGS_1.0` broadcast from the receiving peer for that same selection.

This SHALL hold for any selection change attributable to a remote apply, not only for a directly applied clip change. Applying a remote *position* moves the local playhead, which can raise a selection event of its own; that event is a consequence of the remote message, and reporting it as a local selection sends a peer's own action back to it as an instruction. Where the plugin already attributes an event to a recent remote apply, that attribution SHALL gate the broadcast rather than only annotate the log.

#### Scenario: applying a peer's selection does not bounce back
- **WHEN** a peer applies an incoming clip isolation/highlight
- **THEN** that peer does not broadcast a `PLAYBACK_SETTINGS_1.0/SET` message carrying the same `clip_guid` as an echo of the just-applied change

#### Scenario: a selection raised by applying a remote position is not reported as local
- **WHEN** applying a remote position moves the local playhead and that raises a selection event
- **AND** the plugin attributes that event to the remote apply it has just performed
- **THEN** it SHALL NOT broadcast the event as a local selection
- **AND** the peer that sent the position SHALL NOT be instructed to change its view

#### Scenario: a genuine local selection is still broadcast
- **WHEN** the user changes the selection locally, with no recent remote apply to attribute it to
- **THEN** the selection SHALL be broadcast as before
