## MODIFIED Requirements

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

This is not hypothetical. A follower isolated two clips, correctly broadcast no
visibility at all, and the host isolated the same two clips in the same order
seconds later: the follower's clip-timeline registration fired the host's own
selection machinery, and the host then broadcast that as its own visibility.
Enforcement defined over fields cannot see this, because no visibility field
ever crossed the wire.

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
