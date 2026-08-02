# xstudio-clip-selection-sync (delta)

## ADDED Requirements

### Requirement: Playback addressed to an isolated clip is followed, not discarded

While a peer views an isolated clip, its `PLAYBACK_SETTINGS_1.0` messages carry a `timeline_guid` identifying that clip's **clip timeline** rather than the sequence. A receiver SHALL resolve such a `timeline_guid` and apply the playback state, so that play, stop, and scrub inside an isolated clip keep peers in step.

Resolution SHALL use the existing deterministic derivation — every peer computes a clip timeline's guid from the clip's own guid without coordination — so no additional field, message, or handshake is introduced. A receiver therefore SHALL be able to resolve the guid for any clip it knows, whether or not it has itself created that clip timeline.

A `timeline_guid` matching neither a known timeline nor a clip timeline of a known clip SHALL continue to be ignored: this requirement widens what is resolvable, it does not remove the guard.

The frame carried in such a message is clip-local. Applying it SHALL leave the receiver showing the same image as the sender, on both hosts.

#### Scenario: Stopping inside an isolated clip stops the peer

- **WHEN** a peer isolates a clip, plays, and then stops at a clip-local frame
- **THEN** the receiving peer SHALL stop
- **AND** SHALL land on the same image, rather than continuing to play

#### Scenario: Scrubbing inside an isolated clip moves the peer

- **WHEN** a peer scrubs within an isolated clip
- **THEN** the receiving peer SHALL follow to the same image

#### Scenario: A receiver that never created the clip timeline still follows

- **WHEN** playback arrives addressed to the clip timeline of a clip the receiver knows, but whose clip timeline the receiver has not itself created
- **THEN** the receiver SHALL resolve the guid from the clip and follow the playback
- **AND** SHALL NOT require any prior announcement of that clip timeline

#### Scenario: An unresolvable timeline is still ignored

- **WHEN** playback arrives with a `timeline_guid` that matches no known timeline and no clip timeline of any known clip
- **THEN** the receiver SHALL ignore it, as before

#### Scenario: Returning to the sequence is unaffected

- **WHEN** a peer leaves the isolated clip and returns to sequence view
- **THEN** playback addressed to the sequence timeline SHALL be applied as it is today
