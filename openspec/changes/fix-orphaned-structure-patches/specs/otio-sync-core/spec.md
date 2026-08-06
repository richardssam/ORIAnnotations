## ADDED Requirements

### Requirement: A sync GUID is stable for the life of the object
An object's sync GUID SHALL identify the same logical object for as long as that
object exists in the session. A peer SHALL NOT assign a new GUID to an object
that peers already hold, whether through re-initialisation, rebuilding, or any
other local operation.

A sync GUID is the session's shared name for an object. If it can change under a
live session, every peer's reference to that object is invalidated at once, and
patches addressing the new name cannot be resolved by anyone.

#### Scenario: Rebuilding a container preserves its identity
- **WHEN** a peer rebuilds or re-initialises a container that peers already hold
- **THEN** the rebuilt container SHALL keep its existing sync GUID
- **AND** patches addressing that GUID SHALL continue to resolve on every peer

### Requirement: A patch never addresses an object peers were not given
A peer SHALL NOT broadcast a structural patch that addresses an object it has
not published to the session. Where such a patch would be sent, the peer SHALL
report it rather than emit it.

This is the condition that allowed eight consecutive structural messages to be
sent, none applied, and no error raised at either end.

#### Scenario: An unpublished parent is caught at the sender
- **WHEN** a peer would broadcast a patch whose parent it has never published
- **THEN** the peer SHALL report the condition
- **AND** SHALL NOT leave the session believing the patch was applied

### Requirement: An unresolvable structural patch is reported
When a structural patch cannot be applied because its target cannot be resolved,
the receiving peer SHALL record it and make the record observable, rather than
discard it silently.

The record is **diagnostic, not a health verdict**. A receiving peer cannot
distinguish "the sender broadcast against an object it never published" from "I
have not caught up yet" — both present as a parent GUID it does not hold — and
sessions routinely produce a few of these while establishing. A peer that
self-elects as master reaches its synchronised state holding no timelines at
all, so even "has this peer joined?" does not separate the two cases. The
enforceable check therefore belongs at the sender, which always knows whether it
published a parent; it is specified separately above.

#### Scenario: A patch naming an unknown parent is surfaced
- **WHEN** a peer receives a structural patch whose parent object it does not hold
- **THEN** the peer SHALL record that the patch could not be applied
- **AND** the record SHALL be observable without reading application logs

#### Scenario: The record does not grow without bound
- **WHEN** many patches cannot be applied
- **THEN** the retained detail SHALL be bounded
- **AND** a count of all occurrences SHALL still be available
- **AND** the record SHALL NOT be used to replay the patches later

### Requirement: Buffered deltas are replayed against the timestamp they carry
Messages buffered while joining SHALL be compared against the snapshot using the
timestamp the message actually carries.

#### Scenario: A delta newer than the snapshot is replayed
- **WHEN** a peer buffers a message while joining
- **AND** that message is newer than the snapshot it subsequently applies
- **THEN** the message SHALL be replayed after the snapshot

#### Scenario: A delta older than the snapshot is discarded
- **WHEN** a buffered message predates the snapshot
- **THEN** it SHALL be discarded, the snapshot already containing its effect
