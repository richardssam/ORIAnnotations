# session-visibility-authority

## Purpose
Define who may change what a synchronised review session is looking at. Visibility — which clip or sequence is on screen and in which view mode — is owned by an elected host, while position and annotation remain open to every peer. Covers the split itself, how the host is elected, and how a follower mirrors rather than derives the view.

## Requirements

### Requirement: Broadcast authority is split by category
Sync traffic SHALL be divided into categories with distinct authority, so that controlling what the session looks at is a separate permission from moving within it.

- **visibility** — which clip or sequence is on screen, and in which view mode — SHALL be broadcast only by the session host.
- **position** — playhead position, play/stop, playback mode — SHALL remain broadcastable by any peer.
- **annotation** SHALL remain broadcastable by any peer.

Visibility and position currently travel as field groups within one message, so enforcement SHALL apply to the fields rather than to the message type: a non-host peer MAY broadcast a message carrying position, and SHALL NOT broadcast one asserting visibility.

#### Scenario: A follower may scrub but not change what is shown
- **WHEN** a peer that is not the host moves its playhead
- **THEN** the position SHALL be broadcast and followed by other peers
- **WHEN** that same peer changes which clip it is viewing locally
- **THEN** no visibility change SHALL be broadcast

#### Scenario: The host changes what everyone sees
- **WHEN** the host changes the clip or view mode
- **THEN** that visibility change SHALL be broadcast
- **AND** every other peer SHALL adopt it

#### Scenario: Authority is enforced in one place
- **WHEN** any peer attempts a broadcast
- **THEN** authority SHALL be evaluated at a single shared enforcement point rather than separately in each host application
- **AND** the caller SHALL be told whether the broadcast was sent or suppressed

### Requirement: The host is elected by capability
The session host SHALL be elected rather than fixed to a particular application, so that a session containing no preferred host still has one. Election SHALL prefer a peer whose application is designated as the preferred visibility authority, and SHALL fall back to any capable peer otherwise. Ties SHALL be broken deterministically so that every peer reaches the same result.

The host role SHALL be distinct from the existing master (snapshot authority) role: a change of master SHALL NOT by itself change who controls visibility.

#### Scenario: The preferred application hosts when present
- **WHEN** a session contains both a preferred-host peer and other peers
- **THEN** the preferred peer SHALL be elected host

#### Scenario: A session without a preferred peer still has a host
- **WHEN** a session contains only non-preferred peers
- **THEN** one of them SHALL be elected host
- **AND** it SHALL hold visibility authority exactly as a preferred host would

#### Scenario: Every peer agrees on the host
- **WHEN** peers evaluate host election from the same set of peers
- **THEN** they SHALL all reach the same host
- **AND** a late-joining peer SHALL learn the current host from the session state it is given on joining

### Requirement: A non-owner never infers local user intent
A peer that does not own a category SHALL NOT infer, from a local state transition within that category, that a local user caused it, and SHALL NOT act on such an inference.

This exists because a transition caused by applying a remote message is indistinguishable from one caused by a user, and acting on the guess produces effects that no user requested — starting playback on every peer, or resetting a playhead over a seek that had just been applied.

#### Scenario: A peer-driven transition triggers no local-intent side effects
- **WHEN** a peer's view changes as a result of applying a message from another peer
- **THEN** it SHALL NOT broadcast anything that describes that change as a local action
- **AND** it SHALL NOT start, stop, or reposition playback on the assumption that a user initiated the change

#### Scenario: The owner's transitions need no inference
- **WHEN** the host's own view changes
- **THEN** it MAY broadcast the change, being the only peer permitted to
- **AND** no time-based guard SHALL be required to decide whether that change was locally caused

### Requirement: Followers mirror visibility rather than deriving it
A follower SHALL adopt the host's reported view directly, rather than independently computing a view it considers equivalent. Independent derivation lets two peers reach different results from the same inputs and present them as agreement.

A follower that cannot adopt the host's view SHALL report the failure rather than substituting its closest local approximation.

#### Scenario: Follower shows what the host shows
- **WHEN** the host reports the clip and view mode it is displaying
- **THEN** the follower SHALL display that clip in that view mode

#### Scenario: An unmirrorable view is reported, not approximated
- **WHEN** a follower cannot display the clip the host reports
- **THEN** it SHALL report that it could not
- **AND** SHALL NOT silently display a different clip
