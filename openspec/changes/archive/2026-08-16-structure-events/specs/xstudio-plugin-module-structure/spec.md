## ADDED Requirements

### Requirement: Structure changes are discovered from xStudio's own events

The plugin SHALL subscribe to the events xStudio emits when session structure
changes, and SHALL treat those events as the primary means of discovering a local
structural change.

The subscription SHALL cover creation of a playlist in the session, creation of a
sequence within a playlist, renaming of a container, and removal of a container.

Subscription SHALL be established at whatever level emits the event. A
session-level subscription alone is insufficient: a sequence created inside an
existing playlist is announced on that playlist's own event group, and the
session does not relay it. Detecting only what the session announces reproduces
the failure this change exists to remove.

Discovery SHALL NOT depend on the health of the structural poll. A poll pass that
is slow, throttled, or blocked SHALL delay reconciliation of state already known,
never the discovery of a change that has just happened.

#### Scenario: A sequence created inside a playlist is discovered

- **WHEN** a user creates a sequence in an existing playlist on this peer
- **THEN** the plugin SHALL discover it from the event emitted on that playlist
- **AND** SHALL NOT wait for a structural poll pass to do so

#### Scenario: A new playlist is discovered, and becomes observable itself

- **WHEN** a playlist is created in the session on this peer
- **THEN** the plugin SHALL discover it from the session-level event
- **AND** SHALL thereafter receive that playlist's own structural events

#### Scenario: A rename is discovered without a poll

- **WHEN** a tracked container is renamed on this peer
- **THEN** the plugin SHALL discover the new name from the rename event

#### Scenario: A stalled poll does not delay discovery

- **WHEN** a structural poll pass is blocked or has not run for longer than its
  usual interval
- **AND** a structural change occurs on this peer during that time
- **THEN** the change SHALL still be discovered when its event arrives

#### Scenario: Removal is discovered from the event that names the container

- **WHEN** a container is removed on this peer
- **THEN** the plugin SHALL learn which container was removed from the event
- **AND** SHALL NOT need to read the identity of the removed container's actor to
  determine that it is gone

### Requirement: The structural poll remains the backstop

The structural poll SHALL be retained. It SHALL continue to detect structural
changes independently of any event, so that correctness does not depend on an
individual event arriving.

Retaining it is the decision, not a transitional step. A subscription can be
missed, fail, or never be established — for a playlist that appeared while a join
failed, for a session already populated when this peer connected — and a
detection mechanism with no independent check turns each of those into a silently
unsynced session that nothing reports.

The poll's interval MAY be relaxed, since it is no longer what bounds discovery
latency.

#### Scenario: A change missed by an event is still detected

- **WHEN** a structural change occurs and no corresponding event reaches the
  plugin
- **THEN** the poll SHALL detect it and it SHALL be published as before

#### Scenario: Structure present before subscription is still detected

- **WHEN** the plugin begins syncing a session that already contains playlists and
  sequences
- **THEN** those SHALL be detected and published, whether or not any event is
  emitted for structure that already existed

#### Scenario: A failed subscription self-heals

- **WHEN** joining a playlist's event group fails
- **THEN** that playlist's structure SHALL still be detected by the poll
- **AND** the join SHALL be re-attempted rather than abandoned

#### Scenario: Disabling event-driven discovery leaves a working system

- **WHEN** event-driven discovery is unavailable or turned off
- **THEN** structure SHALL still be detected and published by the poll alone
- **AND** the session SHALL behave as it did before events were subscribed to

### Requirement: A change is published once, by one path

An event and a poll that observe the same structural change SHALL produce a
single publication of it.

Both routes SHALL converge on the same publishing logic rather than each
implementing it, so a change discovered either way is handled identically. A
second implementation reached only when one route wins a race is exercised rarely
and tested less.

#### Scenario: An event and a poll observing the same change publish it once

- **WHEN** a structural change is discovered by its event
- **AND** a subsequent poll pass observes the same change
- **THEN** it SHALL be published exactly once

#### Scenario: Discovery route does not change the outcome

- **WHEN** the same structural change is discovered by event on one occasion and
  by poll on another
- **THEN** what is published SHALL be identical in both cases

### Requirement: Event handlers obey the threading invariant

An xStudio structural event handler SHALL NOT touch the `SyncManager`, read
container content, or publish on the thread the event arrives on. It SHALL record
cheap local state or enqueue work onto the command queue, as every other xStudio
event handler does.

The invariant is inherited, but here it protects something further: these events
arrive on an xStudio actor's callback, so work done inline blocks that actor
rather than the plugin's poll thread. Publishing inline would relocate the stall
this change exists to remove into a worse place.

#### Scenario: A structural event does not publish inline

- **WHEN** a structural event arrives on an xStudio callback thread
- **THEN** the handler SHALL enqueue the work rather than publish
- **AND** SHALL NOT access the `SyncManager` on that thread

#### Scenario: A slow publish does not block the emitting actor

- **WHEN** publishing a discovered structural change takes a long time
- **THEN** the xStudio actor that emitted the event SHALL NOT be blocked for that
  duration

#### Scenario: An unrelated event on a shared subscription is ignored cheaply

- **WHEN** an event arrives that this subscription does not act on
- **THEN** the handler SHALL ignore it without reading xStudio state

### Requirement: A newly created container is not published before it can be described

A structural change discovered by event SHALL be published only once the plugin
can read what it needs to describe it.

An event announces that a container exists, not that it is populated. Publishing
on arrival risks broadcasting an empty sequence and then correcting it, which
peers apply as one structural change followed by another — a worse artefact than
the latency it replaces, and one that lands on every peer rather than one.

#### Scenario: An empty-on-arrival sequence is not published as empty

- **WHEN** a sequence-creation event arrives before the sequence's content is
  readable
- **THEN** the plugin SHALL NOT publish it as empty
- **AND** SHALL publish it once its content can be read

#### Scenario: A container that never becomes readable does not block others

- **WHEN** a discovered container cannot be read
- **THEN** it SHALL NOT prevent other structural changes from being discovered or
  published

### Requirement: A remotely-applied structural change is not re-broadcast

A structural event caused by this peer applying a peer's change SHALL NOT be
published back to the session.

Applying a remote change mutates local xStudio structure, which emits the same
events a local user action would. Without a guard, every applied change echoes
back — the failure mode the existing structural-mutation suppression already
exists to prevent, now reachable by a second route.

#### Scenario: Applying a peer's new timeline does not echo

- **WHEN** this peer applies a peer's structural change
- **AND** xStudio emits structural events as a result
- **THEN** those events SHALL NOT cause a broadcast

#### Scenario: A local change during the suppression window is still published

- **WHEN** a user makes a genuine local structural change
- **THEN** it SHALL be published, whether or not a remote apply occurred recently

## MODIFIED Requirements

### Requirement: Structural controller propagates timeline deletion

The `StructureSyncController` SHALL broadcast timeline removal when a user deletes
a synced playlist/timeline in xStudio, and SHALL tear down the local container
when a peer's removal is received. This extends the controller's existing
ownership of structural deletions and playlist handling.

Deletion SHALL be discovered from the removal event xStudio emits, which names the
container that was removed. The structural poll SHALL continue to detect deletions
independently, as the backstop for a removal whose event does not arrive.

Where the poll judges liveness, it SHALL do so from the live enumeration rather
than by reading the stored (possibly-dead) actor, so a deleted playlist's actor
read cannot freeze the poll thread. Where reading a tracked container's identity
is unavoidable, that read SHALL be bounded, and a read that does not complete
SHALL be treated as "still present" and re-checked on a later pass — never as
evidence of deletion. Inferring removal from a read that did not answer would
broadcast the removal of a live timeline to every peer, which is a worse failure
than noticing a real deletion one pass late.

Local container teardown SHALL remove the container by its **container uuid**
(`create_playlist`'s first return value, resolved from `session.playlist_tree`),
not the `Playlist` actor's uuid — `session.remove_container` keys on the former,
and using the latter silently removes nothing and lets detection re-run and
resurrect the timeline. The teardown SHALL set the structural-mutation suppression
guard so the removal's own xStudio events do not echo back as a re-broadcast.

#### Scenario: User deletes a synced playlist/timeline in xStudio

- **WHEN** a tracked timeline's container is removed in xStudio
- **THEN** the plugin SHALL call `broadcast_remove_timeline` with that timeline's
  GUID, whether it learned of the removal from the event or from the poll

#### Scenario: An identity read that does not complete is not a deletion

- **WHEN** the poll cannot read a tracked container's identity within its bound
- **THEN** that container SHALL be treated as still present
- **AND** no removal SHALL be broadcast for it
- **AND** it SHALL be re-checked on a later pass

#### Scenario: Peer removal tears down the xStudio container

- **WHEN** the plugin receives a `remove_timeline` action from the sync manager
- **THEN** `StructureSyncController` SHALL remove the xStudio container by its
  resolved container uuid, symmetric to container creation on `add_timeline`
- **AND** the removed timeline SHALL NOT be re-broadcast by a subsequent event or
  poll pass

#### Scenario: Removal flows through the existing dispatch tables

- **WHEN** a `remove_timeline` event is routed
- **THEN** it SHALL be handled via the existing entry-point dispatch tables
  (`_handle_manager_event`), with no new protocol message format or sequence
  beyond `REMOVE_TIMELINE` itself
