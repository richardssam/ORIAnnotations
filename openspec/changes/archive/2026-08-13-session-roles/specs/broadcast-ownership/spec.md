## MODIFIED Requirements

### Requirement: Claiming is triggered only by local user input
A peer SHALL claim a category only in response to input that peer's own user produced, never automatically as a side effect of a suppressed broadcast or of applying a remote message. Applying a remote message can trigger local events indistinguishable in shape from user action; treating those as claim triggers would let ownership cycle back to a non-driving peer at the next lease expiry, reproducing the same feedback loop at expiry speed instead of per-message.

A peer SHALL additionally NOT claim a category its session role forbids it from broadcasting, even when the trigger is genuine local user input. Local user input remains a necessary condition for a claim; it is no longer a sufficient one.

This is required because the two mechanisms compose into a defect neither has alone. Host applications invoke the claim operation unconditionally from every input-driven path, as they must, since they never test authority themselves — and a peer whose role forbids a category keeps interacting locally, that being the deliberate local interaction model. Such a peer would therefore claim the category, and could never confirm it, because confirmation follows only from a broadcast in that category actually going out and role has stripped it. The category would sit held-but-unconfirmed for its full duration, and an earlier claim outranks a later one, so a driver's fresh claim would lose to it until it expired.

The check SHALL live in the shared claim operation, beside the existing runtime-switch check, and SHALL be keyed on the same role table the broadcast guard uses. A role-blocked claim SHALL be a refusal to claim and SHALL NOT be treated as a release: releasing would let a non-driver's local activity take a category away from its current owner, which is the same defect with its sign reversed.

Categories that every role may broadcast SHALL remain claimable by every role.

A suppressed broadcast SHALL NOT queue its payload for later replay. When a peer is later granted ownership, it SHALL broadcast its current state if its user is still interacting, or nothing at all.

#### Scenario: A suppressed broadcast does not claim
- **WHEN** a peer's broadcast is suppressed because it does not hold the category's lease
- **THEN** that suppression SHALL NOT itself trigger a claim

#### Scenario: Applying a remote message does not claim
- **WHEN** a peer applies a message it received from the category's current owner
- **AND** that application causes local events that resemble user-driven state changes
- **THEN** no claim SHALL be triggered by those events

#### Scenario: A deferred grant does not replay stale state
- **WHEN** a pending claim is promoted to ownership after its peer's user has stopped interacting
- **THEN** the newly granted owner SHALL NOT broadcast a stale payload captured at claim time

#### Scenario: A role-forbidden category is not claimed on local input
- **WHEN** a peer whose role forbids the position category scrubs its own playhead
- **THEN** no claim SHALL be emitted for that category
- **AND** the current owner SHALL continue to hold it

#### Scenario: A refused claim leaves the current owner untouched
- **WHEN** a claim is refused because the claiming peer's role forbids the category
- **THEN** no release SHALL be emitted
- **AND** no other peer's ownership SHALL change

#### Scenario: An ungated category remains claimable by every role
- **WHEN** a peer holding the `viewer` role changes its own display state
- **THEN** the display category SHALL be claimed as it would be for any other role

#### Scenario: The claim gate is inert under a permissive role policy
- **WHEN** every peer in the session holds the `driver` role
- **THEN** every claim SHALL proceed exactly as it did before role was introduced
