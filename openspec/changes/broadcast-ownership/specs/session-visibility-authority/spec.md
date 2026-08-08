## MODIFIED Requirements

### Requirement: Broadcast authority is split by category
Sync traffic SHALL be divided into categories with distinct authority, so that controlling what the session looks at is a separate permission from moving within it.

- **visibility** — which clip or sequence is on screen, and in which view mode — SHALL be broadcast only by the session host. This is a static, single-writer rule and needs no additional contention resolution.
- **position** — playhead position, play/stop, playback mode, and display state (channel, exposure, pan/zoom) — SHALL remain broadcastable by any peer, but only by whichever peer currently holds that category's ownership lease (`broadcast-ownership`).
- **structure** — timeline add/remove/replace/rename and structural child mutations — SHALL remain broadcastable by any peer, but only by whichever peer currently holds the structure ownership lease (`broadcast-ownership`).
- **annotation** SHALL remain broadcastable by any peer, with no ownership lease.

Visibility, position, and structure currently travel as field groups within one or more messages, so enforcement SHALL apply to the fields rather than to the message type: a non-host peer MAY broadcast a message carrying position, and SHALL NOT broadcast one asserting visibility; a peer that does not hold the position or structure lease SHALL NOT broadcast fields in that category regardless of host status.

#### Scenario: A follower may scrub but not change what is shown
- **WHEN** a peer that is not the host moves its playhead while holding the position lease
- **THEN** the position SHALL be broadcast and followed by other peers
- **WHEN** that same peer changes which clip it is viewing locally
- **THEN** no visibility change SHALL be broadcast

#### Scenario: The host changes what everyone sees
- **WHEN** the host changes the clip or view mode
- **THEN** that visibility change SHALL be broadcast
- **AND** every other peer SHALL adopt it

#### Scenario: A peer without the position lease does not broadcast position
- **WHEN** a peer moves its playhead while another peer holds the position lease
- **THEN** its position fields SHALL NOT be broadcast

#### Scenario: Authority is enforced in one place
- **WHEN** any peer attempts a broadcast
- **THEN** authority SHALL be evaluated at a single shared enforcement point rather than separately in each host application
- **AND** that evaluation SHALL include both the static visibility rule and the position/structure lease check
- **AND** the caller SHALL be told whether the broadcast was sent or suppressed, where a message that is sent with some field groups stripped is reported as suppressed
