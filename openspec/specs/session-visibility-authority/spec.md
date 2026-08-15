# session-visibility-authority

## Purpose
Define who may change what a synchronised review session is looking at. Visibility — which clip or sequence is on screen and in which view mode — is owned by an elected host, while position and annotation remain open to every peer. Covers the split itself, how the host is elected, and how a follower mirrors rather than derives the view.
## Requirements
### Requirement: Broadcast authority is split by category
Sync traffic SHALL be divided into categories with distinct authority, so that controlling what the session looks at is a separate permission from moving within it.

- **visibility** — which clip or sequence is on screen, and in which view mode — SHALL be broadcast only by the session host. This is a static, single-writer rule and needs no additional contention resolution.
- **position** — playhead position, play/stop, playback mode, and display state (channel, exposure, pan/zoom) — SHALL remain broadcastable by any peer, but only by whichever peer currently holds that category's ownership lease (`broadcast-ownership`).
- **structure** — timeline add/remove/replace/rename and structural child mutations — SHALL remain broadcastable by any peer, but only by whichever peer currently holds the structure ownership lease (`broadcast-ownership`).
- **annotation** SHALL remain broadcastable by any peer, with no ownership lease.

Visibility, position, and structure currently travel as field groups within one or more messages, so enforcement SHALL apply to the fields rather than to the message type: a non-host peer MAY broadcast a message carrying position, and SHALL NOT broadcast one asserting visibility; a peer that does not hold the position or structure lease SHALL NOT broadcast fields in that category regardless of host status.

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

#### Scenario: A follower's structural message does not move the host's view
- **WHEN** a non-host peer changes its own view, and that produces a structural message
- **AND** the host receives and registers that structure
- **THEN** what the host displays SHALL be unchanged
- **AND** the host SHALL NOT broadcast a visibility change as a result

### Requirement: The host is elected by capability
The session host SHALL be elected rather than fixed to a particular application, so that a session containing no preferred host still has one. Election SHALL prefer a peer whose application is designated as the preferred visibility authority, and SHALL fall back to any capable peer otherwise. Ties SHALL be broken deterministically so that every peer reaches the same result.

Election SHALL additionally be restricted to peers holding the `driver` role. A host that is not a driver would hold visibility authority while its own role forbade it from emitting visibility, so the session's view would freeze with no peer able to change it and nothing reporting why. Role SHALL be applied as a filter on the candidate set, with the existing application preference and deterministic tie-break applied among the survivors, so that every peer still reaches the same host from the same peer table.

A peer whose role is not known — because it is running code that predates roles, or because of the order in which it was learned — SHALL be treated as holding the session's default role for the purpose of this filter. Treating an unknown role as ineligible would let a single peer, or a single adoption ordering, produce a candidate set that appears empty, which is a false report of a driverless session rather than a late election.

Where peers are otherwise equally ranked, the session master SHALL be preferred.
A GUID tie-break alone assigns the seat at random between two peers of the same
application, which moved authority to a joining peer one second after it
connected and left the peer the user was driving unable to change the view. The
master is used rather than the incumbent host because peers already agree on the
master, so election remains a pure function that converges without a claim
protocol; two peers that each elected themselves while alone hold different
incumbents and would not converge. The master preference SHALL break ties only,
and SHALL NOT outrank a better-qualified application.

Election decides who holds visibility **when no peer has claimed it** — at
session start, and after a holder departs. It is no longer the standing answer
to which peer may change the view; that is the visibility lease. Election
continues to supply the role-eligibility filter above and the indicator for a
session with no peer able to hold the category at all.

The host role SHALL be distinct from the existing master (snapshot authority) role: a change of master SHALL NOT by itself change who controls visibility. It SHALL likewise be distinct from the session role: a session MAY contain several drivers and SHALL have at most one host.

A host that leaves the session SHALL NOT retain visibility authority. Election
SHALL be re-run when a peer departs, so authority moves to a peer that is still
present.

This closes a failure that is otherwise unreachable by any other route: because
only the host may broadcast visibility, a host that has gone while still being
counted as elected leaves the session's view frozen, with no peer permitted to
change it and nothing reporting the cause.

The role filter makes an equivalent state reachable by a second route — a session in which no driver is present at all — so that state SHALL be reported and SHALL have an exit, on the same grounds. Reporting it SHALL NOT be left implicit in a disabled menu item.

#### Scenario: The preferred application hosts when present
- **WHEN** a session contains both a preferred-host peer and other peers
- **THEN** the preferred peer SHALL be elected host

#### Scenario: A session without a preferred peer still has a host
- **WHEN** a session contains only non-preferred peers
- **THEN** one of them SHALL be elected host
- **AND** it SHALL hold visibility authority exactly as a preferred host would

#### Scenario: The master holds the seat against an equally-ranked joiner
- **WHEN** a peer joins a session whose host runs the same application
- **AND** the existing host is the session master
- **THEN** the existing host SHALL remain elected

#### Scenario: A preferred application still outranks the master
- **WHEN** a peer of the preferred application joins a session whose master runs a less-preferred application
- **THEN** the joining peer SHALL be elected host

#### Scenario: Learning the master re-runs the election
- **WHEN** a peer learns which peer is master
- **AND** that changes the elected host
- **THEN** the peer SHALL re-elect rather than keep the host it chose without that knowledge

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

#### Scenario: A non-driver is not elected host
- **WHEN** a session contains a preferred-application peer holding the `viewer` or `reviewer` role and a non-preferred peer holding the `driver` role
- **THEN** the driver SHALL be elected host
- **AND** the preferred peer SHALL NOT be elected despite its application preference

#### Scenario: Preference still decides among drivers
- **WHEN** a session contains more than one peer holding the `driver` role
- **THEN** the existing application preference and deterministic tie-break SHALL decide between them

#### Scenario: An unknown role does not remove a candidate
- **WHEN** the peer table contains a capable peer whose role is not known
- **AND** the session's default role is `driver`
- **THEN** that peer SHALL remain eligible for election

#### Scenario: A session with no driver reports the condition
- **WHEN** no peer in the session holds the `driver` role
- **THEN** no host SHALL be elected
- **AND** the condition SHALL be reported to the user rather than presenting as an unexplained frozen view
- **AND** an action SHALL be available that resolves it

### Requirement: A non-owner never infers local user intent
A peer that does not own a category SHALL NOT infer, from a local state transition within that category, that a local user caused it, and SHALL NOT act on such an inference.

Ownership of visibility means **holding its lease**, not being the elected host.
A peer that has just taken the category by acting owns it for as long as it
holds it, and a peer whose lease has passed to someone else does not, whatever
the election says.

This exists because a transition caused by applying a remote message is indistinguishable from one caused by a user, and acting on the guess produces effects that no user requested — starting playback on every peer, or resetting a playhead over a seek that had just been applied.

#### Scenario: A peer-driven transition triggers no local-intent side effects
- **WHEN** a peer's view changes as a result of applying a message from another peer
- **THEN** it SHALL NOT broadcast anything that describes that change as a local action
- **AND** it SHALL NOT start, stop, or reposition playback on the assumption that a user initiated the change

#### Scenario: The owner's transitions need no inference
- **WHEN** the peer holding visibility changes its own view
- **THEN** it MAY broadcast the change, being the only peer permitted to
- **AND** no time-based guard SHALL be required to decide whether that change was locally caused

#### Scenario: Losing the lease ends the inference
- **WHEN** a peer's visibility lease passes to another peer
- **THEN** it SHALL stop treating its own view transitions as broadcastable user intent

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


### Requirement: Visibility is held by the peer that most recently asserted a view

Authority over visibility SHALL be a lease held by the peer that most recently
asserted what the session is looking at, not a standing property of an elected
seat.

The category SHALL remain single-writer at any instant. Leasing changes *who*
the writer is, not how many there are: at most one peer may assert a view at a
time, and every other peer mirrors it.

A peer SHALL claim visibility when a local user action changes what it is
displaying, and SHALL hold the category for a bounded time, releasing it on
expiry as every other leased category does.

#### Scenario: Selecting a clip takes the category
- **WHEN** a user changes what their peer is displaying
- **AND** that peer's role permits visibility
- **THEN** that peer SHALL hold visibility authority
- **AND** its view SHALL be broadcast to the session

#### Scenario: An idle peer does not hold the view
- **WHEN** a peer holds visibility and stops asserting a view for the lease duration
- **THEN** it SHALL no longer hold the category
- **AND** another eligible peer SHALL be able to take it without contest

#### Scenario: Only one peer asserts a view at a time
- **WHEN** two peers assert a view simultaneously
- **THEN** exactly one SHALL hold the category
- **AND** the other's visibility fields SHALL be stripped from its outgoing messages

### Requirement: The peer that just acted takes visibility from an idle holder

Where two peers claim visibility, the claim from the peer that acted **most
recently** SHALL win.

This is deliberately the opposite of the position category, and the difference
is the substance of this capability. Position prefers the earlier claim, which
is right where two peers scrubbing at once is a conflict to settle in favour of
whoever started. For visibility the later claim *is* the user who just selected
something, and refusing it is precisely the failure this capability exists to
remove: a user watching their own selections do nothing, with no error at either
end.

A claim SHALL NOT be honoured while another peer is actively asserting a view,
so that "most recent" cannot be read as "whoever spoke last in a burst".

#### Scenario: A new selection takes the view from an idle holder
- **WHEN** a peer holds visibility but has not asserted a view recently
- **AND** another eligible peer's user selects something
- **THEN** the selecting peer SHALL hold visibility
- **AND** the session SHALL display what that peer selected

#### Scenario: An active holder is not interrupted mid-action
- **WHEN** a peer is actively asserting a view
- **AND** another peer claims visibility within that activity
- **THEN** the active holder SHALL retain the category

#### Scenario: Hand-off does not oscillate
- **WHEN** visibility passes from one peer to another
- **THEN** the session SHALL settle on one view
- **AND** SHALL NOT alternate between the two peers' views while both remain idle

### Requirement: A role that forbids visibility forbids claiming it

A peer whose session role does not permit emitting visibility SHALL NOT acquire
the visibility lease, and its claim SHALL be refused rather than recorded.

Role is checked before the lease, on the same composition rule the other
categories already follow: a claim recorded for a peer that may never broadcast
the category leaves an un-preemptable holder that cannot use what it holds.

#### Scenario: A reviewer cannot take the view
- **WHEN** a peer whose role forbids visibility changes what it displays locally
- **THEN** it SHALL NOT acquire visibility authority
- **AND** its visibility fields SHALL be stripped from its outgoing messages

#### Scenario: A refused claim leaves the current holder untouched
- **WHEN** a role-forbidden peer's visibility claim is refused
- **THEN** the peer currently holding visibility SHALL continue to hold it

### Requirement: A peer that never claims still defers to the elected host

A peer that does not claim visibility SHALL continue to defer to the elected
host, so that a session containing peers which predate the visibility lease
behaves as it did before it.

Absence of a claim SHALL be read as "this peer is not asserting a view", never
as "this peer relinquishes" or "no peer holds the category" — the same
convention the session already applies to an omitted host, roster or ownership
section.

#### Scenario: A session where nobody claims behaves as before
- **WHEN** no peer in a session claims visibility
- **THEN** the elected host SHALL hold visibility authority
- **AND** the session SHALL behave exactly as it did before visibility was leased

#### Scenario: A non-claiming peer is not treated as releasing the category
- **WHEN** a peer sends messages carrying no visibility claim
- **THEN** the current holder's authority SHALL be unchanged
