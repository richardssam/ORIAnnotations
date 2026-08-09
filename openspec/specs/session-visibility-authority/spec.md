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

Stripping those fields is necessary and **not sufficient**. Authority is over
the **displayed outcome**, not over one message's fields: a non-host peer SHALL
NOT cause the host to change what it displays, by any route. A peer's action
that reaches the host as *structure* — registering a container, adding a
timeline — SHALL NOT, by its side effects, change what the host shows.

This is stated as an invariant, not as a described defect. It was originally
motivated by a 2026-08-06 session in which a follower isolated two clips and the
host isolated the same two, in the same order, seconds later — read at the time
as the follower's clip-timeline registration firing the host's selection
machinery. **That reading was withdrawn on 2026-08-09**: the two clips are
adjacent on the Video Track, and the host's behaviour was reproduced exactly
with the follower idle. It was sequence scan-through. A clip-timeline
`ADD_TIMELINE` cannot move the host's display at all — it is registered without
notifying the host application.

The requirement stands on its own terms regardless. The route that was believed
to exist did not, but "authority over fields" genuinely does not imply
"authority over the displayed outcome", and the real 2026-08-06 chain — a
follower's *position* messages driving the host's play state, which opened a
timing exemption, which let the host's own scan-through broadcast as deliberate
isolations — is an instance of exactly that gap, reached by a different route.
See `openspec/changes/archive/2026-08-09-fix-visibility-authority-bypass/evidence.md`.

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

#### Scenario: A follower's structural message does not move the host's view
- **WHEN** a non-host peer changes its own view, and that produces a structural message
- **AND** the host receives and registers that structure
- **THEN** what the host displays SHALL be unchanged
- **AND** the host SHALL NOT broadcast a visibility change as a result

### Requirement: The host is elected by capability
The session host SHALL be elected rather than fixed to a particular application, so that a session containing no preferred host still has one. Election SHALL prefer a peer whose application is designated as the preferred visibility authority, and SHALL fall back to any capable peer otherwise. Ties SHALL be broken deterministically so that every peer reaches the same result.

The host role SHALL be distinct from the existing master (snapshot authority) role: a change of master SHALL NOT by itself change who controls visibility.

A host that leaves the session SHALL NOT retain visibility authority. Election
SHALL be re-run when a peer departs, so authority moves to a peer that is still
present.

This closes a failure that is otherwise unreachable by any other route: because
only the host may broadcast visibility, a host that has gone while still being
counted as elected leaves the session's view frozen, with no peer permitted to
change it and nothing reporting the cause.

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

#### Scenario: A departing host hands over visibility authority
- **WHEN** the peer currently elected host leaves the session
- **THEN** a remaining peer SHALL be elected host
- **AND** that peer SHALL be able to broadcast visibility changes
- **AND** the session's view SHALL NOT remain frozen on the departed host's last state

#### Scenario: A departing follower does not change the host
- **WHEN** a peer that is not the host leaves the session
- **THEN** the elected host SHALL be unchanged

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

A follower SHALL decide whether it already matches the host's view by comparing
against **what it is currently displaying**, not against the last view it
adopted from a peer. A locally-initiated view change leaves those two different,
and a peer that compares against the latter treats the host's instruction as
already satisfied and ignores it — leaving a divergence the host cannot correct.

Declining to act on the host's view SHALL be reported on the same terms as
failing to. A follower that silently does nothing is indistinguishable from one
that complied, which is the condition this requirement exists to remove.

#### Scenario: Follower shows what the host shows
- **WHEN** the host reports the clip and view mode it is displaying
- **THEN** the follower SHALL display that clip in that view mode

#### Scenario: An unmirrorable view is reported, not approximated
- **WHEN** a follower cannot display the clip the host reports
- **THEN** it SHALL report that it could not
- **AND** SHALL NOT silently display a different clip

#### Scenario: A locally diverged follower is recoverable
- **WHEN** a follower has changed its own view so that it differs from the host's
- **AND** the host subsequently reports its view
- **THEN** the follower SHALL adopt the host's view
- **AND** SHALL NOT treat the instruction as redundant

#### Scenario: Taking no action is reported
- **WHEN** a follower receives the host's view and neither adopts it nor fails visibly
- **THEN** it SHALL record that the view was not adopted, and why
- **AND** the record SHALL be observable without reading application logs

