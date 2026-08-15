# post-join-state-confirmation Specification

## Purpose

Tells a peer, and its user, whether joining a session actually worked. A joiner
adopts a snapshot and then displays whatever its own session build produced;
nothing compares the two, so a joiner that ends up on the wrong clip or the
wrong frame believes it is synchronised and says so. This capability makes the
adoption checkable at the one moment its expected value is known.

## Requirements

### Requirement: A peer confirms the state it joined with

After adopting a session snapshot, a peer SHALL compare what it is displaying
against what that snapshot described, and SHALL record the outcome.

The comparison SHALL cover what the session agrees "in sync" means — the
timelines and their clip order, which timeline is active, the current frame, and
the display target — rather than a subset chosen at the call site. A confirmation
that checks less than the session's own definition of agreement reports success
for states the session would call divergent.

The peer's own side of the comparison SHALL be derived from what it is actually
displaying, not from the snapshot it was sent or from its record of having
applied it. Comparing a stored intention against itself confirms nothing; the
whole failure being detected is an intention that did not take effect.

#### Scenario: A joiner that adopted the session's view confirms

- **WHEN** a peer finishes adopting a snapshot and is displaying what it described
- **THEN** the confirmation SHALL record that the state was confirmed

#### Scenario: A joiner on the wrong clip reports it

- **WHEN** a peer finishes adopting a snapshot but is displaying a different clip
- **THEN** the confirmation SHALL record a mismatch
- **AND** SHALL name what differs

#### Scenario: A joiner on the wrong frame reports it

- **WHEN** a peer finishes adopting a snapshot but is at a different frame
- **THEN** the confirmation SHALL record a mismatch

#### Scenario: Confirmation reads what is displayed, not what was intended

- **WHEN** a peer applied a snapshot's view but the application did not take
  effect
- **THEN** the confirmation SHALL report a mismatch

### Requirement: Confirmation reports and does not repair

A confirmation SHALL NOT alter this peer's state, request state, or broadcast
anything.

Detection is separated from response deliberately. A repair driven by a detector
that has not yet earned confidence is worse than the condition it repairs: this
project has twice concluded that acting on an uncertain signal costs more than
the signal is worth — a read that timed out must not be treated as a deletion,
and a position that could not be read must not be treated as frame 0. A
confirmation that only reports cannot make a session worse than not having it.

#### Scenario: A mismatch changes nothing

- **WHEN** the confirmation finds a mismatch
- **THEN** this peer's displayed state SHALL be unchanged by the confirmation
- **AND** no state request or broadcast SHALL be made as a result

#### Scenario: A confirmation is not a broadcast

- **WHEN** a confirmation runs
- **THEN** no peer other than this one SHALL be affected by it

### Requirement: Confirmation runs when the join has settled

The confirmation SHALL run only once this peer's session build and view
adoption have settled.

A peer mid-build has not finished becoming what it is being checked against, so
a comparison then reports a difference that is real and meaningless. The check
SHALL therefore be sequenced after the same point the view adoption waits for,
and SHALL NOT run on a fixed delay chosen independently of it.

Where the peer never settles, the confirmation SHALL record that it could not be
performed rather than reporting either success or a mismatch. "Not checked" and
"checked and matching" are different facts, and collapsing them re-creates the
silence this capability exists to remove.

#### Scenario: A confirmation waits for the build

- **WHEN** a peer's session build has not completed
- **THEN** no confirmation outcome SHALL be recorded yet

#### Scenario: A peer that never settles says so

- **WHEN** a joining peer cannot reach a state in which it can be checked
- **THEN** the outcome SHALL be recorded as not confirmed
- **AND** SHALL NOT be recorded as either a match or a mismatch

### Requirement: A legitimately advancing frame is not a mismatch

Where the session is playing, the frame SHALL NOT be compared as an exact value.

A snapshot's frame is a point in time. A joiner adopting a playing host is
behind it by the time it has finished building, and both peers may be in perfect
lockstep. Reporting that as divergence would make the indicator wrong in the
ordinary case and train users to disregard it.

A tolerance SHALL be applied when comparing frames, and comparison SHALL be
skippable where the session is playing.

#### Scenario: A paused session compares frames

- **WHEN** the snapshot describes a paused session
- **THEN** the frame SHALL be compared

#### Scenario: A playing session does not report an advanced frame as divergence

- **WHEN** the snapshot describes a playing session
- **AND** this peer is at a later frame having adopted it
- **THEN** that SHALL NOT be reported as a mismatch

#### Scenario: A small frame difference is within tolerance

- **WHEN** this peer's frame differs from the snapshot's by less than the
  tolerance
- **THEN** that SHALL NOT be reported as a mismatch

### Requirement: The outcome names what differs

A reported mismatch SHALL identify what differs, not merely that something does.

"This peer is not in sync" is not actionable and cannot be triaged from a log
pair after the fact. Each of the failures that motivated this capability was
distinguished from the others only by *which* field disagreed — the clip, the
frame, or the timeline — and a bare boolean would have made all three look the
same.

#### Scenario: A mismatch is itemised

- **WHEN** the confirmation reports a mismatch
- **THEN** the differing fields SHALL be identified individually

#### Scenario: The outcome is durable enough to diagnose after the fact

- **WHEN** a mismatch is reported
- **THEN** it SHALL be recorded where it can be read after the session, not only
  shown transiently
