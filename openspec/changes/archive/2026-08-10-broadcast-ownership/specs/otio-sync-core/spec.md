## ADDED Requirements

### Requirement: Claim and Release Ownership Messages

The system SHALL define `CLAIM_OWNERSHIP` and `RELEASE_OWNERSHIP` as typed protocol messages, registered for dispatch through the same message registry used by other protocol messages. `CLAIM_OWNERSHIP` SHALL carry `category`, `peer_guid`, and `claim_ts`. `RELEASE_OWNERSHIP` SHALL carry `category` and `peer_guid`. Neither message SHALL carry an OTIO payload.

The manager SHALL expose a claim operation, invoked only from input-driven plugin paths, that broadcasts `CLAIM_OWNERSHIP` for a category and applies the same local claim-resolution rule to its own claim that it applies to a received one, so the claiming peer's own view stays consistent with everyone else's.

#### Scenario: A claim is dispatched through the standard registry
- **WHEN** a `CLAIM_OWNERSHIP` message is received
- **THEN** it SHALL be dispatched to the ownership-claim handler via the message registry, the same mechanism used for other typed messages

#### Scenario: A claim resolves identically on the claiming peer and every receiver
- **WHEN** a peer broadcasts a `CLAIM_OWNERSHIP` for a category
- **THEN** it SHALL apply the same claim-resolution rule to that message as any other peer would on receiving it

#### Scenario: An explicit release frees the category immediately
- **WHEN** a peer that holds a category's lease broadcasts `RELEASE_OWNERSHIP`
- **THEN** every peer SHALL treat that category as free (or transfer it to a pending claimant, if one exists) without waiting for the lease's expiry timer

### Requirement: STATE_SNAPSHOT carries ownership state for late joiners

`StateSnapshot` SHALL gain a `broadcast_ownership` section carrying, per leased category, the current owner's peer GUID and the lease's remaining time. The section SHALL follow the same backwards-compatibility convention already established for `host_guid` on this message: it SHALL be omitted from the payload when no category has an owner worth reporting, and a `None` or absent value on receipt SHALL be ignored rather than interpreted as "no owner" — so a peer running code that predates ownership cannot clear another peer's held lease by sending or relaying a snapshot.

Adoption of the received ownership state SHALL go through a single named operation, mirroring `adopt_host()`, rather than direct assignment to the local lease table.

#### Scenario: A snapshot reports the current owner and remaining lease time
- **WHEN** a peer builds a `StateSnapshot` while it or another peer holds a leased category
- **THEN** the `broadcast_ownership` section SHALL include that category's owner GUID and remaining lease time

#### Scenario: An unset category is omitted, not sent as free
- **WHEN** a peer builds a `StateSnapshot` and a leased category currently has no owner
- **THEN** that category SHALL be omitted from `broadcast_ownership` rather than included with a null owner

#### Scenario: A snapshot from an old peer cannot clear a held lease
- **WHEN** a `StateSnapshot` arrives with no `broadcast_ownership` section, or with a category absent from it
- **THEN** the receiving peer SHALL leave its locally-tracked ownership for that category unchanged

### Requirement: Position field stripping mirrors visibility field stripping

`authority.py` SHALL provide `strip_position_fields`, matching the shape of the existing `strip_visibility_fields`, and `SyncManager` SHALL provide `_enforce_position`, matching the shape of the existing `_enforce_visibility`. `broadcast_playback_state` SHALL call `_enforce_position` beside `_enforce_visibility`, so a single playback message can have its visibility fields stripped, its position fields stripped, both, or neither, independently.

#### Scenario: Position fields are stripped when the sender lacks the lease
- **WHEN** `broadcast_playback_state` is called by a peer that does not hold the position lease
- **THEN** the position field group SHALL be stripped from the outgoing message
- **AND** the call SHALL report `SUPPRESSED`

#### Scenario: A message can lose one field group and keep another
- **WHEN** a peer holds the position lease but is not the visibility host
- **THEN** the outgoing message SHALL retain its position fields and have its visibility fields stripped
- **AND** the call SHALL report `SUPPRESSED`, consistent with `SUPPRESSED` meaning "sent, with some fields stripped" rather than "not sent"

### Requirement: Ownership enforcement is read per call from a runtime switch

Ownership enforcement SHALL be gated by an environment-variable switch read at each enforcement check, not cached at import, following the same discipline as `ORI_VISIBILITY_AUTHORITY`. Disabling the switch SHALL cause `_enforce_position` and the structure-lease check to behave as if every peer always held every lease.

#### Scenario: The switch is re-read on every call
- **WHEN** the ownership switch is changed while the process is running
- **THEN** the next `broadcast_*` call SHALL observe the new value, with no caching from a prior call or from import time
