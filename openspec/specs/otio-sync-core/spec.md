# otio-sync-core

## Purpose
Coordinate OTIO timeline synchronisation across a networked review session: a command-based message envelope, typed protocol message definitions as the single source of truth for the wire format, registry-based dispatch of incoming messages, and the patching engine that builds and applies OTIO mutations.
## Requirements
### Requirement: Command-Based Messaging (ASWF PRWG)
The system SHALL use a nested message envelope for all payloads to strictly align with the ASWF Synchronized Review Messaging standard, replacing the legacy flat structure. The payload MUST include a top-level `payload` key containing a `command_schema` and `command`. The `command_schema`, the `command.event` name, and the shape of `command.payload` SHALL be derived from a typed message class rather than from inline string literals and ad-hoc dictionaries.

#### Scenario: Dispatching a sync payload
- **WHEN** a client broadcasts a timeline patch or playback state
- **THEN** the message SHALL be wrapped in a nested envelope structured as `payload.command_schema` and `payload.command.event`.

#### Scenario: Envelope fields derived from a typed message
- **WHEN** a client broadcasts any protocol message
- **THEN** the `command_schema` and `command.event` written to the envelope SHALL equal the schema and event declared on the corresponding typed message class
- **AND** the byte-level envelope structure and field names SHALL be unchanged from the prior string-literal implementation, so peers running older code interoperate without modification.

### Requirement: Typed Protocol Message Definitions
The system SHALL define each transport-layer protocol message as a typed class that is the single source of truth for that message's `command_schema`, `event` name, and payload field set. Sending a message SHALL construct the corresponding class; no protocol message SHALL be assembled from free-standing schema/event string literals at the call site.

#### Scenario: Every broadcast corresponds to a defined message class
- **WHEN** any `broadcast_*` operation sends a message
- **THEN** that message SHALL be represented by a registered message class whose declared schema and event match the envelope produced.

#### Scenario: Payload built without reflective serialization
- **WHEN** a message instance is serialized to its wire payload
- **THEN** the payload SHALL be produced by an explicit per-message conversion (not by reflective whole-object serialization), so hot-path messages incur no additional traversal cost.

### Requirement: Registry-Based Message Dispatch
The system SHALL dispatch incoming messages through a registry keyed by `(command_schema, event)` that maps to the handling logic, replacing the sequential string-comparison conditional chain. The registry SHALL be derived from the message class definitions so it cannot diverge from them.

#### Scenario: Known message is dispatched to its handler
- **WHEN** an incoming envelope carries a `(command_schema, event)` pair that matches a registered message class
- **THEN** the payload SHALL be reconstructed into that message type and routed to the registered handler.

#### Scenario: Unknown message is ignored safely
- **WHEN** an incoming envelope carries a `(command_schema, event)` pair with no registered message class
- **THEN** the system SHALL ignore the message without raising an error and SHALL continue processing subsequent messages.

### Requirement: Single Definition for OTIO Session Payloads
The system SHALL ensure the OTIO session mutation messages (`INSERT_CHILD`, `MOVE_CHILD`, `REMOVE_CHILD`, `SET_PROPERTY`, `REPLACE_ANNOTATION_COMMANDS`) are built and consumed through the same message class, so the payload shape for these messages is defined in exactly one place. The patching engine SHALL produce these messages when generating mutations and SHALL reconstruct the same message type when applying them.

#### Scenario: Mutation produces a typed message
- **WHEN** the patching engine performs a local insert, move, remove, property change, or annotation-command replacement
- **THEN** it SHALL return the corresponding typed message whose payload is the value transmitted on the wire.

#### Scenario: Mutation applied from the same definition
- **WHEN** an OTIO session mutation message is received
- **THEN** it SHALL be reconstructed into the same message type used to build it and applied, with no separately-maintained payload-shape declaration.

### Requirement: Settings Messages Declare Fields but Tolerate Extras
The system SHALL provide message classes for `PLAYBACK_SETTINGS_1.0/SET` and `DISPLAY_SETTINGS_1.0/SET` that document their known fields, while accepting messages that contain additional, unrecognized fields without failure. This preserves interoperability with independent producers that may emit extra keys.

#### Scenario: Known settings fields are documented
- **WHEN** the playback and display settings message classes are defined
- **THEN** they SHALL enumerate the established fields (playback: `playing`, `current_time`, `playback_mode`, `timeline_guid`, `sync_timestamp`; display: `pan`, `zoom`, `exposure`, `channel`, `sync_timestamp`).

#### Scenario: Extra fields do not break parsing
- **WHEN** a settings message arrives containing fields beyond the declared set
- **THEN** the message SHALL be parsed and applied without error, and unrecognized fields SHALL be ignored rather than rejected.

### Requirement: Playback Mode Is a Three-Way Wire Value
`PLAYBACK_SETTINGS_1.0/SET`'s `playback_mode` field SHALL be one of the string values `"play-once"`, `"loop"`, or `"ping-pong"`, replacing the prior `looping: bool` field. Each host SHALL translate between this wire value and its own native play-mode representation; no host-neutral mode enum SHALL be shared beyond these three wire strings.

#### Scenario: OpenRV translates its native play mode to the wire value
- **WHEN** OpenRV broadcasts a playback state update
- **THEN** `playback_mode` SHALL be derived directly from `rv.commands.playMode()` (`PlayLoop` → `"loop"`, `PlayOnce` → `"play-once"`, `PlayPingPong` → `"ping-pong"`), with no intermediate boolean collapse

#### Scenario: xStudio translates its native play mode to the wire value
- **WHEN** xStudio broadcasts a playback state update
- **THEN** `playback_mode` SHALL be derived directly from the active playhead's native `"Loop Mode"` attribute (`"Loop"` → `"loop"`, `"Play Once"` → `"play-once"`, `"Ping Pong"` → `"ping-pong"`), with no intermediate boolean collapse

#### Scenario: A received playback_mode is applied to the local native engine
- **WHEN** a peer's `PLAYBACK_SETTINGS_1.0/SET` message carrying a `playback_mode` value is received
- **THEN** the receiving host SHALL set its own native play-mode attribute to the corresponding value, so both peers' native engines (not just their synced state) agree on the mode

#### Scenario: Ping-pong playback reverses direction without additional sync messages
- **WHEN** a host's native play mode is set to ping-pong and the playhead reaches either end of its playback range
- **THEN** the host's own native engine SHALL reverse playback direction on its own, and the existing per-frame position broadcast SHALL continue to report the current frame regardless of direction, requiring no new wire message beyond the existing `current_time` field

### Requirement: Performance Parity on Hot Paths
The system SHALL NOT degrade the throughput of high-frequency message paths, specifically partial-annotation streaming and playback-state broadcasts. Construction and serialization of these messages SHALL avoid added per-message validation or reflective traversal.

#### Scenario: Hot-path messages avoid added overhead
- **WHEN** a partial-annotation or playback-state message is constructed and serialized
- **THEN** it SHALL not perform isinstance-style validation or reflective field walking, so per-message cost remains at parity with the prior implementation.

### Requirement: Messages Own OTIO Serialization

The protocol messages that carry OTIO content — `AddTimeline` (`timeline`), `StateSnapshot` (`timelines`), `InsertChild` (`child_data`), and `ReplaceAnnotationCommands` (`commands`) — SHALL accept the OTIO object(s) directly rather than pre-serialized dictionaries, and SHALL own the conversion to and from wire form. The producer SHALL pass the OTIO object to the message constructor; the message SHALL serialize it when building its wire payload. Callers SHALL NOT serialize OTIO before constructing these messages. The hot-path `PartialAnnotation.events` field is explicitly excluded and SHALL continue to carry serialized dictionaries.

#### Scenario: Producer passes an OTIO object, not a dict

- **WHEN** a producer constructs one of these messages to broadcast OTIO content
- **THEN** it SHALL supply the live OTIO object(s) to the constructor
- **AND** the message's `to_payload()` SHALL emit the serialized wire form, with no `_otio_to_dict` call at the construction site.

#### Scenario: Wire payload is byte-identical to the pre-serialized form

- **WHEN** one of these messages serializes an OTIO object to its wire payload
- **THEN** the resulting payload SHALL be byte-for-byte identical to the prior implementation that pre-serialized with `otio_json`
- **AND** peers running older code SHALL interoperate without modification.

#### Scenario: Hot-path streaming is unchanged

- **WHEN** a `PartialAnnotation` is constructed and serialized
- **THEN** its `events` field SHALL still carry serialized dictionaries
- **AND** no OTIO deserialize-then-reserialize SHALL be introduced on this path.

### Requirement: Lazy OTIO Deserialization on Receive

When an OTIO-bearing message is reconstructed from a received payload, the system SHALL store the raw wire form and SHALL defer deserialization until a handler requests the OTIO object(s) through a dedicated accessor. Reconstructing the message from a payload SHALL NOT eagerly deserialize the OTIO content, so a handler can make admission decisions before paying deserialization cost.

#### Scenario: Reconstruction does not deserialize

- **WHEN** an OTIO-bearing message is reconstructed from a received wire payload
- **THEN** the OTIO content SHALL be retained in its raw wire form
- **AND** no OTIO deserialization SHALL occur until the accessor is called.

#### Scenario: Handler skips deserialization before its guard

- **WHEN** an `AddTimeline` is received for a timeline GUID the receiver already holds
- **THEN** the handler SHALL be able to reject it on the GUID check without deserializing the timeline payload.

#### Scenario: Accessor returns the OTIO object form

- **WHEN** a handler calls the message's OTIO accessor
- **THEN** it SHALL receive the OTIO object(s) for that message, deserializing any element still in wire form and passing through any element already an OTIO object.

### Requirement: Protocol Module Importable Without OTIO

The protocol message module SHALL remain importable without `opentimelineio` installed, so the documentation generator can enumerate message classes and field metadata. Any dependency on `opentimelineio` SHALL be loaded lazily inside the serialization and accessor methods, never at module import time.

#### Scenario: Doc generator imports without OTIO

- **WHEN** the protocol message module is imported in an environment without `opentimelineio`
- **THEN** the import SHALL succeed and all message classes and their field metadata SHALL be available
- **AND** `opentimelineio` SHALL only be required when a serialization or OTIO-accessor method is actually invoked.

### Requirement: Timeline Removal Message and Teardown

The `TIMELINE_1.0` family SHALL include a `RemoveTimeline` message
(`EVENT = "REMOVE_TIMELINE"`) carrying `timeline_guid` and `sync_timestamp`,
registered for dispatch alongside `AddTimeline` and `RenameTimeline`. The message
SHALL NOT carry an OTIO payload — the GUID alone identifies a timeline peers
already hold.

`SyncManager` SHALL provide `broadcast_remove_timeline(guid)`, symmetric to
`broadcast_add_timeline`, which removes the timeline locally and sends a
`RemoveTimeline` to all peers. The inbound handler SHALL perform a single-timeline,
reference-aware teardown rather than clearing all timeline state.

#### Scenario: Removal message is registered and dispatched

- **WHEN** a `REMOVE_TIMELINE` message under `TIMELINE_1.0` is received
- **THEN** it SHALL be dispatched to the timeline-removal handler via the message
  registry, the same mechanism used for `ADD_TIMELINE` and `RENAME_TIMELINE`

#### Scenario: Removing a sequence timeline tears down only its own state

- **WHEN** a `RemoveTimeline` is received for a sequence timeline GUID the receiver
  holds
- **THEN** the manager SHALL delete that GUID from `_timelines`
- **AND** SHALL remove from the shared `_object_map` only the GUIDs belonging to
  that timeline's subtree, leaving every other timeline's object-map entries intact

#### Scenario: Clip-annotation timelines cascade with their sequence

- **WHEN** the removed sequence has one or more clips that own clip-annotation
  timelines
- **THEN** the manager SHALL delete those clip-annotation timelines from both
  `_clip_timelines` and `_timelines`
- **AND** no `_clip_timelines` entry referencing the removed subtree SHALL remain

#### Scenario: Removing the active timeline clears the active pointer

- **WHEN** the removed timeline's GUID equals `active_timeline_guid`
- **THEN** the manager SHALL set `active_timeline_guid` to `None`
- **AND** SHALL NOT select a replacement timeline or carry a successor GUID in the
  message, because the active timeline is re-asserted by the next
  `PlaybackSettingsSet`

#### Scenario: Removal is idempotent for unknown timelines

- **WHEN** a `RemoveTimeline` is received for a GUID not present in `_timelines`
- **THEN** the handler SHALL make no state changes and return no host event
  (silent no-op)

#### Scenario: Real removal notifies the host to tear down its container

- **WHEN** a `RemoveTimeline` removes a sequence timeline the receiver held
- **THEN** the handler SHALL return a `("remove_timeline", tl)` action carrying the
  removed timeline object, symmetric to the `("add_timeline", tl)` action emitted
  on registration

### Requirement: Self-Election Is a Single Manager Operation

The sync manager SHALL expose one operation that performs self-election as
session master and owns every state transition that election entails: marking
this peer as the master, recording this peer's GUID as the session master GUID,
transitioning the session status to `SYNCED`, and announcing mastership with an
`I_AM_MASTER` message. Callers SHALL NOT elect by assigning the master flag, the
master GUID, or the session status directly; every self-election path — discovery
timeout, state-request timeout, and master failover — SHALL go through this one
operation.

The operation SHALL allow the caller to defer the `I_AM_MASTER` announcement, so
a host that must finish building its initial timelines before it can serve a
state request may apply the local election state immediately and announce
afterwards. When the announcement is deferred, the caller SHALL broadcast the
master response itself once that work completes.

The operation SHALL NOT change the messages a self-electing peer puts on the
wire, nor their order, relative to the previous per-caller election sequences.

#### Scenario: Solo peer self-elects

- **WHEN** a peer that has found no master performs the self-election operation
- **THEN** it SHALL report itself as the session master
- **AND** the session master GUID SHALL equal that peer's own GUID
- **AND** the session status SHALL be `SYNCED`
- **AND** exactly one `I_AM_MASTER` message carrying that peer's GUID SHALL be
  sent to the session

#### Scenario: Election announcement is deferred

- **WHEN** a peer performs the self-election operation with the announcement
  deferred
- **THEN** the master flag, master GUID, and `SYNCED` status SHALL be applied
  immediately
- **AND** no `I_AM_MASTER` message SHALL be sent until the caller broadcasts the
  master response

#### Scenario: Re-electing an existing master is inert locally

- **WHEN** the self-election operation runs on a peer that is already master and
  already `SYNCED`
- **THEN** it SHALL remain master with an unchanged master GUID
- **AND** the synced callbacks SHALL NOT be fired a second time

#### Scenario: Wire behaviour unchanged for existing peers

- **WHEN** a peer self-elects after its discovery timeout expires
- **THEN** the message content and ordering observed by other peers SHALL be
  identical to the prior per-caller implementation
- **AND** peers running older code SHALL interoperate without modification

#### Scenario: No caller mutates election state directly

- **WHEN** any host plugin or manager-internal path elects this peer as master
- **THEN** it SHALL invoke the self-election operation
- **AND** it SHALL NOT assign the master flag, master GUID, or session status at
  the call site

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

### Requirement: A patch addressing an unannounced parent is reported at the sender
When a peer broadcasts a structural patch whose parent it has never announced,
it SHALL record the condition and make the record observable.

This is the condition that allowed eight consecutive structural messages to be
sent, none applied, and no error raised at either end. It is checked at the
sender because the sender knows what it announced, where a receiver cannot
distinguish an orphaned patch from one that merely arrived early.

The sender's record is **sharper than the receiver's but still not a verdict**,
and SHALL NOT be treated as one. A peer may legitimately address a parent it
never announced in at least two ways, both observed in a healthy suite:

- **Deterministically derived objects.** Clip-timeline GUIDs are computed by
  each peer from shared inputs, so a receiver holds the parent without anyone
  having sent it. Observed: xStudio broadcast an annotation into a track it
  never announced, and OpenRV resolved it with zero unresolved patches.
- **Insert-then-announce ordering.** A peer may insert into a timeline it is
  still building and announce the whole timeline immediately afterwards, the
  announcement carrying both parent and child.

A peer therefore SHALL NOT refuse a patch on this basis alone. Refusal requires
a test that excludes both patterns; "the sender did not announce it" is not
that test.

#### Scenario: An unannounced parent is recorded at the sender
- **WHEN** a peer broadcasts a patch whose parent it has never announced
- **THEN** the peer SHALL record the condition
- **AND** the record SHALL be observable without reading application logs

#### Scenario: A derived parent is not treated as a fault
- **WHEN** the parent's GUID is one every peer derives independently
- **THEN** the patch SHALL still be emitted
- **AND** receiving peers SHALL resolve it normally

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

### Requirement: A departing peer is removed from the peer table

The sync manager SHALL remove a peer from the peer table that feeds host
election when that peer leaves the session, whether it leaves cleanly or not.

A peer that has left SHALL NOT continue to be treated as a candidate for any
role elected from that table. Without removal the table is append-only, and an
authority elected onto a departed peer can never move.

Removal SHALL go through the single existing removal operation, which owns the
transition and re-runs host election, rather than through direct mutation of the
table by either detection path.

#### Scenario: A peer that disconnects cleanly is removed promptly

- **WHEN** a peer disconnects from the session in the normal way
- **THEN** every other peer SHALL remove it from the peer table
- **AND** SHALL do so without waiting for a liveness timeout

#### Scenario: A peer that vanishes without notice is removed eventually

- **WHEN** a peer stops participating without signalling departure — a crash,
  a killed process, or a lost network
- **THEN** every other peer SHALL remove it from the peer table within a bounded
  time
- **AND** the outcome SHALL be the same as for a clean disconnect

#### Scenario: Removal is a single operation

- **WHEN** a peer is removed by either detection path
- **THEN** removal SHALL go through one shared operation
- **AND** that operation SHALL re-run host election as part of the removal

### Requirement: Peer liveness is established by periodic announcement

Each peer SHALL re-announce its presence periodically, so that the absence of
announcements is meaningful evidence that a peer has gone.

Liveness SHALL NOT be inferred from a peer's other traffic. A peer that is
present but idle — observing a session without scrubbing, annotating, or
editing — emits nothing, and MUST NOT be removed on that basis.

A periodic announcement SHALL NOT request answers from other peers, so that
re-announcement cannot produce an answer cascade.

A peer removed for inactivity SHALL be restored by its next announcement, so a
peer wrongly removed after a temporary stall recovers without rejoining.

#### Scenario: An idle peer is not removed

- **WHEN** a peer is present but sends no playback, annotation, or structural
  messages for longer than the liveness timeout
- **THEN** it SHALL remain in every other peer's peer table
- **AND** SHALL remain eligible for election

#### Scenario: Re-announcement does not cascade

- **WHEN** a peer re-announces its presence periodically
- **THEN** receiving peers SHALL NOT answer that announcement
- **AND** the message volume SHALL grow no faster than the number of peers

#### Scenario: A wrongly removed peer restores itself

- **WHEN** a peer is removed for inactivity but is in fact still present
- **THEN** its next announcement SHALL restore it to the peer table
- **AND** host election SHALL be re-run against the restored table

### Requirement: Departure is signalled from the shared session layer

The departure signal SHALL be emitted by the shared session manager when a
session is closed, not by each host application's own disconnect path.

Both host applications already route their disconnect through that single close
operation. Emitting from each application instead would duplicate protocol
behaviour across two separately-written paths, which is where these two hosts
have already drifted, and the failure would be silent — one application
announcing its exit and the other not.

Loss of the departure signal SHALL degrade to the inactivity path rather than
leaving the peer present indefinitely.

#### Scenario: Both applications signal departure without application-specific code

- **WHEN** either host application disconnects from a session
- **THEN** a departure signal SHALL be emitted
- **AND** neither application SHALL implement that emission itself

#### Scenario: A lost departure signal still resolves

- **WHEN** a departure signal is not delivered
- **THEN** the departing peer SHALL still be removed by the inactivity path

### Requirement: A joining peer learns the peer set from session state

The session state a joining peer is given SHALL carry the current peer set,
alongside the elected host it already carries, so the joiner learns who is
present from the message it already requests.

The peer set SHALL identify each peer by the same attributes its own
announcement carries, including its identity. A joiner that learns a peer only
from session state SHALL be able to name it on the same terms as one learned
from an announcement — otherwise identity is systematically missing for the
peers that have gone quiet, which are the ones a joiner cannot name by any
other means.

Peers SHALL NOT answer one another's announcements in order to make a joiner
aware of them. That answering behaviour is the only step in the peer protocol
whose message count grows with the number of peers, and periodic announcement
makes it redundant: a joiner learns a silent peer from that peer's next
announcement regardless of whether anyone answered.

A joiner that receives no session state SHALL still learn the peer set from
subsequent announcements, within the announcement interval. Session state SHALL
NOT be the only means by which a peer becomes discoverable.

#### Scenario: A joiner learns existing peers without an answer cascade

- **WHEN** a peer joins a session and receives session state
- **THEN** that state SHALL identify the peers currently present
- **AND** no peer SHALL emit an announcement in response to the joiner's own

#### Scenario: Join cost does not grow with session size

- **WHEN** a peer joins a session
- **THEN** the number of messages emitted in response SHALL NOT grow with the
  number of peers already present

#### Scenario: A joiner that receives no session state still discovers peers

- **WHEN** a peer joins a session in which no session state is sent to it
- **THEN** it SHALL still learn every present peer from their periodic
  announcements
- **AND** SHALL do so within the announcement interval

#### Scenario: Every peer converges on the same peer set

- **WHEN** a joiner adopts the peer set from session state
- **AND** subsequently receives announcements from those peers
- **THEN** its peer table SHALL agree with the sender's
- **AND** host election evaluated against it SHALL reach the same host

#### Scenario: A peer learned from session state is as identifiable as one that announced

- **WHEN** a joiner adopts the peer set from session state
- **THEN** each adopted peer SHALL carry the identity its own announcement would
  have carried
- **AND** the joiner SHALL be able to name it without waiting for that
  announcement

### Requirement: Peer departure does not alter master election

Removal of a peer from the peer table SHALL NOT change how the session master is
elected or how master failover is detected. Master remains a state-sync role
with its own failover mechanism, distinct from any role elected from the peer
table.

#### Scenario: A departing non-master peer leaves mastership untouched

- **WHEN** a peer that is not the master departs
- **THEN** the session master SHALL be unchanged
- **AND** no master failover SHALL be triggered by the departure

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

### Requirement: Role stripping mirrors visibility and position field stripping

The authority module SHALL provide role-based field-group stripping matching the shape of the existing visibility and position stripping, and the manager SHALL apply it from the same broadcast choke point, ahead of both existing enforcement steps.

A single broadcast SHALL be able to have its field groups stripped by role, by visibility authority, by the position lease, by any combination, or by none, independently. The existing suppressed status SHALL retain its present meaning — "sent, with some fields stripped" — and SHALL NOT be redefined to mean "not sent".

Stripping SHALL happen in one core function rather than at call sites, so that no host application and no individual broadcast method reimplements the decision.

#### Scenario: Role strips a field group ahead of category authority

- **WHEN** a peer whose role forbids the structure category emits a structural broadcast
- **THEN** the structure field group SHALL be stripped by the role check
- **AND** the category authority check SHALL NOT be reached for that field group

#### Scenario: A message loses one field group to role and keeps another

- **WHEN** a peer holding the `reviewer` role emits a playback message carrying both position and visibility field groups
- **THEN** the visibility fields SHALL be stripped and the position fields retained
- **AND** the call SHALL report the suppressed status

#### Scenario: A role-stripped broadcast does not confirm a lease

- **WHEN** a peer's field group is stripped by the role check
- **THEN** the lease-confirmation path for that category SHALL NOT run

#### Scenario: A permissive role policy strips nothing

- **WHEN** every peer holds the `driver` role
- **THEN** no field group SHALL be stripped by the role check
- **AND** the sequence of enforcement calls SHALL leave every outgoing message as it was before role was introduced

### Requirement: The peer table carries each peer's role by both population paths

`SyncManager`'s peer table SHALL carry a role for each peer, populated by both paths that already populate it: the periodic peer announcement, and the peer roster carried in session state.

The announcement SHALL carry role as an additional field beside the application and capability fields it already carries. No separate role message SHALL be introduced: the announcement already exists, already reaches every peer, and already serves as the liveness heartbeat, so a second message would duplicate it with its own cadence and its own storm risk.

A peer entry carrying no role SHALL resolve to the session's default role wherever role is read, rather than being treated as ineligible or as holding the most restrictive role.

#### Scenario: An announcement carries the sender's role

- **WHEN** a peer announces its presence
- **THEN** the announcement SHALL carry that peer's role alongside its application and capabilities

#### Scenario: An adopted peer carries its role

- **WHEN** a joining peer adopts the peer roster from session state
- **THEN** each adopted entry SHALL carry that peer's role
- **AND** host election evaluated against the adopted table SHALL reach the same result as one evaluated after those peers announce

#### Scenario: A roster written by older code does not empty the candidate set

- **WHEN** a peer roster arrives carrying no role for its entries
- **THEN** each entry SHALL resolve to the session's default role
- **AND** host election SHALL NOT report the session as having no eligible driver on that basis

### Requirement: STATE_SNAPSHOT carries session role policy for late joiners

`StateSnapshot` SHALL gain a role policy section carrying the session's default role and its identity-keyed role memory. The section SHALL follow the same backwards-compatibility convention already established for the elected host and the ownership section on this message: it SHALL be omitted from the payload when the session declares no role policy, and a null or absent value on receipt SHALL be ignored rather than interpreted as an empty policy — so a peer running code that predates roles cannot clear a session's role policy by sending or relaying a snapshot.

Adoption of received role policy SHALL go through a single named operation, mirroring the existing host and ownership adoption operations, rather than direct assignment to local policy state.

#### Scenario: A snapshot reports the session's role policy

- **WHEN** a peer builds a snapshot for a session that declares a role policy
- **THEN** the role policy section SHALL carry the default role and the identity-keyed role memory

#### Scenario: A session with no policy omits the section

- **WHEN** a peer builds a snapshot for a session that declares no role policy
- **THEN** the role policy section SHALL be omitted rather than sent empty

#### Scenario: A snapshot from an old peer cannot clear a policy

- **WHEN** a snapshot arrives with no role policy section
- **THEN** the receiving peer SHALL leave its role policy unchanged

### Requirement: Master self-election prefers a driver without introducing a timing window

Self-election as session master SHALL rank candidate peers so that a peer holding the `driver` role is preferred, because the master holds the session's canonical state and needs full broadcast capability to serve it.

The preference SHALL be expressed as a ranking evaluated at the existing discovery timeout, and SHALL NOT introduce a new wall-clock deferral in which a non-driver waits to see whether a driver appears. Self-election SHALL remain one operation owning every transition it entails, reached from its existing named callers.

The preference SHALL be a preference and not a restriction: where no driver is available, a peer holding another role SHALL still self-elect, so that a session always has a master. A peer promoted to master on that basis SHALL be master for state synchronisation only, and SHALL NOT thereby acquire the `driver` role.

#### Scenario: A driver is preferred as master

- **WHEN** self-election is evaluated and a peer holding the `driver` role is available
- **THEN** the ranking SHALL prefer that peer

#### Scenario: A session without a driver still has a master

- **WHEN** self-election is evaluated in a session containing no driver
- **THEN** a peer SHALL still self-elect as master
- **AND** its role SHALL be unchanged by that election

#### Scenario: No new deferral is introduced

- **WHEN** a peer's discovery timeout expires
- **THEN** self-election SHALL be decided at that point
- **AND** no additional waiting period SHALL be introduced by the role preference

### Requirement: Role and driverless state are exposed to the test inspector

The role held by this peer, the elected host, and whether the session currently has any eligible driver SHALL be readable through the test inspection surface, alongside the host and ownership state it already exposes.

Fields exposed for inspection SHALL be declared in the test runner's ignored-key set where they are not part of state comparison, following the convention established when host authority state was first exposed.

#### Scenario: A test can assert on role without reaching into internals

- **WHEN** a test inspects a running peer
- **THEN** it SHALL be able to read that peer's role, the elected host, and whether an eligible driver is present

#### Scenario: Newly exposed fields do not break state comparison

- **WHEN** the inspector exposes role and driverless state
- **THEN** existing state comparisons SHALL be unaffected


### Requirement: Visibility stripping gates on the lease, not on being host

The single enforcement point for the visibility field group SHALL strip on
whether this peer holds the visibility lease, rather than on whether it is the
elected host.

The field group SHALL continue to be stripped as a whole. The failure that rule
exists to prevent — a follower that drops `view_mode` but still sends a
`clip_guid`, which asserts what the session should look at — is unchanged by
where the authority comes from.

`SUPPRESSED` SHALL keep its established meaning of "sent, with fields stripped",
never "not sent".

#### Scenario: A peer holding the lease sends its view
- **WHEN** a peer holding visibility broadcasts a view state
- **THEN** the visibility fields SHALL be sent intact

#### Scenario: A peer not holding the lease has its view stripped
- **WHEN** a peer that does not hold visibility broadcasts a view state
- **THEN** the visibility fields SHALL be stripped
- **AND** the call SHALL report `SUPPRESSED`

#### Scenario: Position and visibility are stripped independently
- **WHEN** a peer holds the position lease but not visibility
- **THEN** the outgoing message SHALL retain its position fields and lose its visibility fields

### Requirement: A visibility claim is refused when the role forbids the category

The claim operation SHALL refuse a visibility claim from a peer whose role does
not permit the category, and SHALL refuse rather than release.

Plugins claim unconditionally on a local action, so a role that forbids
visibility must be enforced at the claim, not left to the caller. Refusing
rather than releasing keeps a role-blocked peer from disturbing the peer that
legitimately holds the category.

#### Scenario: A role-forbidden visibility claim is refused
- **WHEN** a peer whose role forbids visibility claims the category
- **THEN** the claim SHALL be refused

#### Scenario: A refusal does not release the current holder
- **WHEN** a visibility claim is refused
- **THEN** the peer currently holding the category SHALL keep it

#### Scenario: Disabling enforcement restores unconditional claims
- **WHEN** the ownership enforcement switch is disabled
- **THEN** a visibility claim SHALL behave as if every peer always held every lease

### Requirement: A refused structural broadcast reports that it was refused

The structural broadcast operations SHALL report to their caller whether the
patch was emitted or refused, using the same status vocabulary as the playback
broadcast path.

Today a refusal is a log line and an early return, indistinguishable from
success to the caller. Divergence recovery is triggered by the refusal, so the
refusal has to be a value the caller can act on. Reusing the existing status
vocabulary keeps one meaning of `SUPPRESSED` across the codebase; where a
structural patch is refused outright rather than emitted with fields removed,
the distinction between "withheld" and "sent, reduced" SHALL be reportable.

#### Scenario: A role-refused structural patch reports refusal
- **WHEN** a structural broadcast is refused because the peer's role forbids the category
- **THEN** the operation SHALL report the refusal to its caller

#### Scenario: An emitted structural patch reports that it was sent
- **WHEN** a structural broadcast is emitted
- **THEN** the operation SHALL report that it was sent

#### Scenario: Existing callers are unaffected by the report
- **WHEN** a caller ignores the reported status
- **THEN** its behaviour SHALL be unchanged from before this requirement

### Requirement: State may be re-requested from the synchronised state

A peer SHALL be able to request the session's state while synchronised, and
apply the resulting snapshot, without first leaving the session or re-entering
discovery.

The existing request path is reachable only from joining, which reflects an
assumption that a peer's state can only be wrong before it has one. Divergence
recovery breaks that assumption. A peer that has to leave the session to repair
itself loses its host election standing and any leases it holds, and reappears
to every other peer as a departure and a rejoin.

A re-request SHALL buffer and replay concurrent deltas on the same terms as a
joining peer's request, so material broadcast while the snapshot is in flight is
not lost.

#### Scenario: A synchronised peer requests state
- **WHEN** a synchronised peer requests the session's state
- **THEN** the request SHALL be sent
- **AND** the peer SHALL NOT be reported to other peers as having left

#### Scenario: Deltas arriving during a re-request are replayed
- **WHEN** a structural or annotation message arrives while a re-requested snapshot is in flight
- **AND** that message is newer than the snapshot
- **THEN** it SHALL be applied after the snapshot

#### Scenario: An unanswered re-request returns the peer to synchronised
- **WHEN** a re-requested snapshot does not arrive within the session's timeout
- **THEN** the peer SHALL return to the synchronised state
- **AND** SHALL NOT enter discovery

#### Scenario: Serving a mid-session request is indistinguishable from serving a join
- **WHEN** a peer receives a state request from a peer that is already in the session
- **THEN** it SHALL answer it exactly as it answers a joining peer's request
