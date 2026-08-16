# xStudio Plugin Module Structure Specification

## Purpose
Define the layout, modularity, threading invariant, and relationships between components in the xStudio sync plugin.
## Requirements
### Requirement: Module layout

The xStudio sync plugin SHALL be organised as a set of Python modules within the `xstudio_plugin/ori_sync/` package, with `ori_sync_plugin.py` as the entry-point module and `__init__.py` re-exporting `create_plugin_instance` and `ORISyncPlugin`.

The module set SHALL be:

| Module | Responsibility |
|---|---|
| `ori_sync_plugin.py` | `ORISyncPlugin(PluginBase)`, menus, session lifecycle, poll loop, command-queue drain, both dispatch tables, thin event handlers, `create_plugin_instance` |
| `utils.py` | Logger, URI/path normalisation, session-string parsing, module constants |
| `timeline_build.py` | `TimelineBuildController` — OTIO timeline construction (master side) |
| `playback_sync.py` | `PlaybackSyncController` — playback, playhead, position, and selection sync |
| `display_sync.py` | `DisplaySyncController` — viewport display-state sync |
| `structure_sync.py` | `StructureSyncController` — structural sync (reorders, new media, deletions, playlists, renames, remote structural apply) |
| `annotation_sync.py` | `AnnotationSyncController` — annotation broadcast and apply |
| `media_map.py` | `MediaMapController` — sync-GUID ↔ xStudio-media mapping |

#### Scenario: Plugin loads successfully with split modules

- **WHEN** xStudio loads the `ORI Sync Review` plugin package
- **THEN** `ori_sync_plugin.py` SHALL import all controller modules and `utils.py` without error
- **AND** the plugin SHALL initialise identically to the pre-split single-file version

#### Scenario: Entry-point exports preserved

- **WHEN** the `ori_sync` package is imported
- **THEN** `create_plugin_instance` and `ORISyncPlugin` SHALL be importable from the package as before
- **AND** `create_plugin_instance(connection)` SHALL return an `ORISyncPlugin` instance

### Requirement: Delegated controller pattern

Each domain controller SHALL be a plain Python class that receives a back-reference to the `ORISyncPlugin` instance in its constructor and stores it as `self.plugin`. Controllers SHALL own their domain-specific state and methods.

#### Scenario: Controller instantiation

- **WHEN** `ORISyncPlugin.__init__` runs
- **THEN** it SHALL instantiate `MediaMapController`, `TimelineBuildController`, `PlaybackSyncController`, `DisplaySyncController`, `StructureSyncController`, and `AnnotationSyncController`
- **AND** store them as `self.media`, `self.builder`, `self.playback`, `self.display`, `self.structure`, and `self.annotation`

#### Scenario: media_map instantiated first

- **WHEN** `ORISyncPlugin.__init__` instantiates the controllers
- **THEN** `MediaMapController` SHALL be instantiated before the controllers that depend on it (playback, structure, annotation)

#### Scenario: Cross-controller access

- **WHEN** a controller needs to call a method on a sibling controller
- **THEN** it SHALL access it via `self.plugin.<sibling_controller>.<method>()`
- **AND** it SHALL NOT import sibling controller modules at module top level

### Requirement: Threading invariant preserved

The split SHALL preserve the existing threading model: only the poll thread (`_poll_loop`) touches the `SyncManager` after startup, and xStudio event handlers SHALL only mutate cheap local state or enqueue onto `_cmd_queue`. Moving a method into a controller SHALL NOT change which thread it executes on.

The discovery-timeout path SHALL obey the same invariant. The timeout task runs
on its own short-lived thread, and that thread SHALL NOT read or mutate the
`SyncManager` beyond the single status check that decides whether the timeout
still applies — it SHALL NOT register timelines, elect this peer as master, or
broadcast. Self-election on discovery timeout SHALL instead be enqueued onto
`_cmd_queue` and executed on the poll thread, so the manager keeps a single
writer.

#### Scenario: xStudio event handler delegation

- **WHEN** xStudio fires an event on its message-dispatch thread (playhead, selection, position, annotation, timeline-item)
- **THEN** the `_on_*` handler on `ORISyncPlugin` SHALL remain a thin shim that enqueues onto `_cmd_queue` or delegates to a controller method
- **AND** it SHALL NOT call any method that touches the `SyncManager` directly on the xStudio thread

#### Scenario: Poll-thread-only manager access

- **WHEN** a controller method touches `self.plugin.manager`
- **THEN** that method SHALL only be invoked from the poll thread (via `_drain_cmd_queue`/`_execute_command` or `_handle_manager_event`)

#### Scenario: Discovery timeout defers election to the poll thread

- **WHEN** the discovery timeout expires with the session still discovering
- **THEN** the timeout thread SHALL enqueue a self-election command onto
  `_cmd_queue` and perform no further manager access
- **AND** timeline registration, election, and the `I_AM_MASTER` broadcast SHALL
  all run on the poll thread when that command is drained

#### Scenario: Election is skipped if a master appeared meanwhile

- **WHEN** the queued self-election command is drained and the session is no
  longer discovering — because a peer's `I_AM_MASTER` was processed in the
  interval — or the session has been disconnected
- **THEN** the command SHALL be a no-op
- **AND** no `I_AM_MASTER` SHALL be broadcast, so a late timeout cannot create a
  second master

#### Scenario: Solo start still elects and registers its timelines

- **WHEN** an xStudio peer starts a session with no other peer present
- **THEN** after the discovery timeout it SHALL be master, `SYNCED`, and SHALL
  have registered its current session's timelines with the manager, exactly as
  before this change

### Requirement: Dispatch tables in entry-point

Both routing tables SHALL remain in `ori_sync_plugin.py`: `_handle_manager_event` for remote sync events and `_execute_command` (with `_execute_sync_container`) for drained command-queue items. Each SHALL route to the appropriate controller method.

#### Scenario: Dispatching a remote display action

- **WHEN** `_handle_manager_event` receives `action="display_settings"`
- **THEN** it SHALL call `self.display.apply_display_state(data)` (the relocated `_apply_display_state`)

#### Scenario: Dispatching a remote annotation insert

- **WHEN** `_handle_manager_event` receives `action="insert_child"` carrying annotation commands
- **THEN** it SHALL call the annotation controller's apply method (the relocated `_apply_remote_annotation`)

#### Scenario: Dispatching a queued command

- **WHEN** `_execute_command` drains a `live_stroke` command
- **THEN** it SHALL call the annotation controller's live-stroke broadcast method (the relocated `_broadcast_live_stroke_from_json`)

### Requirement: Import dependency DAG

Module imports SHALL form a strict directed acyclic graph: `utils ← {media_map, timeline_build, playback_sync, display_sync, structure_sync, annotation_sync} ← ori_sync_plugin`. No controller SHALL import another controller module at top level. Imports within the package SHALL be relative (e.g. `from .utils import _log`).

#### Scenario: No circular imports

- **WHEN** any module in `xstudio_plugin/ori_sync/` is imported
- **THEN** the import SHALL succeed without `ImportError` or `AttributeError` caused by circular references

#### Scenario: Relative imports used within the package

- **WHEN** a module references a sibling module in the package
- **THEN** it SHALL use a relative import (`from .<module> import ...`)

### Requirement: Behaviour unchanged

The refactor SHALL NOT change any externally observable behaviour: protocol messages, sync semantics, menu items, attribute/preference names, or QML integration SHALL be identical to the pre-split version.

Relocating state between the plugin and its controllers SHALL likewise be observationally
inert: suppression semantics, echo-guard timing, and the order of emitted protocol
messages SHALL be identical before and after the move.

#### Scenario: Two-client sync regression passes

- **WHEN** the `sync_test/` two-client integration suite is run against the split plugin
- **THEN** all scenarios that passed before the split SHALL pass after it
- **AND** no protocol message format or sequence SHALL differ

#### Scenario: State relocation preserves echo suppression

- **WHEN** the `sync_test/` two-client integration suite is run after domain state moves from `ORISyncPlugin` to its owning controllers
- **THEN** every scenario that passed before the move SHALL pass after it
- **AND** no additional echoed playback, selection, or annotation event SHALL appear
- **AND** no protocol message format or sequence SHALL differ

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

### Requirement: State ownership follows domain

State SHALL live on the controller that owns its domain, including suppression guards and
echo-guard fields. `ORISyncPlugin` attributes SHALL be limited to cross-cutting
infrastructure: the `SyncManager` reference (`manager`), the command queue (`_cmd_queue`),
the poll-thread lifecycle (`_poll_stop`, `_poll_thread`), the canonical timeline registry
(`_sync_playlists`), session-menu and poll-loop lifecycle flags, and the plugin's own UI
attribute/menu handles.

Placement SHALL NOT be used as a thread-safety mechanism. Cross-thread safety is
established solely by the "Threading invariant preserved" requirement.

A controller SHALL access state it owns as `self.<attr>`, and state owned by a sibling
domain as `self.plugin.<owner>.<attr>`. A controller SHALL NOT maintain a private copy of
a guard owned by another controller.

#### Scenario: Controller reads a guard it owns

- **WHEN** a controller method reads or sets a suppression guard belonging to its own domain
- **THEN** it SHALL access the guard as a plain attribute on `self`
- **AND** it SHALL NOT read it through `self.plugin`

#### Scenario: Controller reads a guard owned by a sibling domain

- **WHEN** a controller method reads or sets a suppression guard whose owning domain is a sibling controller
- **THEN** it SHALL access it as `self.plugin.<owner>.<attr>`
- **AND** it SHALL NOT copy the value onto itself

#### Scenario: Plugin attribute surface is limited to infrastructure

- **WHEN** the `xstudio_plugin/ori_sync/` package is searched for controller references to private plugin members
- **THEN** the only plugin-owned *state* referenced SHALL be `_cmd_queue` and `_sync_playlists`
- **AND** no suppression guard, echo-guard field, or other domain state SHALL be among them
- **AND** references to plugin *methods* passed as event callbacks SHALL be permitted, since they are behaviour rather than state

#### Scenario: Owned state is read without defensive fallbacks

- **WHEN** a controller reads state its own domain owns
- **THEN** it SHALL use plain attribute access rather than `getattr(..., default)`
- **AND** a missing attribute SHALL surface as an error rather than silently yielding a default

#### Scenario: Manager access from controller

- **WHEN** a controller method needs the SyncManager
- **THEN** it SHALL access `self.plugin.manager`
- **AND** it SHALL only do so on the poll thread, consistent with the threading invariant

### Requirement: Controller reset owns teardown

Every domain controller SHALL expose a `reset()` method that returns the state it owns to
its post-construction defaults, including release of resources it acquired (event-group
unsubscriptions and cached xStudio handles).

State that deliberately outlives a session SHALL be exempt, and each exemption SHALL be
documented at the `reset()` that skips it. Specifically, the annotation identity and
dedup caches — which record that a local bookmark originated from a remote peer — SHALL
survive disconnect, because clearing them would make the next session's flush scan treat
those bookmarks as new local annotations and re-broadcast them as duplicates. Concurrency
primitives such as locks SHALL NOT be re-created by `reset()`.

`reset()` SHALL be idempotent and SHALL succeed when the plugin has never connected, since
plugin unload invokes it via `cleanup()` → `disconnect()`.

`disconnect()` SHALL delegate all controller teardown to `reset()`. It SHALL NOT clear any
controller-owned attribute inline. Its own responsibilities are limited to stopping the
poll thread, closing the manager, resetting each controller, clearing plugin-owned state,
and setting the status attribute — in that order, so that no poll tick observes
half-reset state.

Fields that are deliberately not cleared on disconnect SHALL retain that behaviour; the
values `reset()` restores SHALL match what disconnect previously established, so that
echo-suppression timing after a reconnect is unchanged.

#### Scenario: Disconnect delegates to controllers

- **WHEN** `disconnect()` runs
- **THEN** it SHALL call `reset()` on every controller it owns
- **AND** it SHALL NOT assign to or clear any controller attribute directly

#### Scenario: Reset before any connection

- **WHEN** `cleanup()` runs on a plugin instance that never connected to a session
- **THEN** each controller's `reset()` SHALL complete without raising

#### Scenario: Reset is idempotent

- **WHEN** a controller's `reset()` is called twice in succession
- **THEN** the second call SHALL complete without raising and leave state identical to the first

#### Scenario: Subscriptions released by the owning controller

- **WHEN** a controller holds an active xStudio event-group subscription at teardown
- **THEN** that controller's `reset()` SHALL unsubscribe it and clear the stored subscription id
- **AND** a failure to unsubscribe SHALL be swallowed rather than aborting teardown

#### Scenario: Reconnect after disconnect suppresses nothing unexpectedly

- **WHEN** a client disconnects and rejoins a session
- **THEN** the guard values in effect after reconnect SHALL match those of a freshly constructed plugin
- **AND** no stale suppression window SHALL delay the first synced playback or annotation event

#### Scenario: Remote-origin annotations are not re-broadcast after rejoin

- **WHEN** a client that applied remote annotations disconnects and rejoins a session
- **THEN** the bookmarks it created from those remote annotations SHALL still be recognised as remote-origin
- **AND** they SHALL NOT be broadcast back to peers as new local annotations

### Requirement: Declarations carry their own documentation

Comments describing an attribute SHALL sit at that attribute's declaration site. When an
attribute moves between modules its explanatory comment SHALL move with it, and comments
describing state that no longer lives at their location SHALL be deleted rather than left
in place.

#### Scenario: Comment travels with a relocated attribute

- **WHEN** an attribute declaration moves from `ori_sync_plugin.py` to a controller
- **THEN** any comment block describing it SHALL appear at the new declaration site
- **AND** SHALL NOT remain in `ori_sync_plugin.py`

#### Scenario: No orphaned comment blocks remain

- **WHEN** `ORISyncPlugin.__init__` is read after the change
- **THEN** every comment in it SHALL describe an attribute declared in `__init__`

### Requirement: Remote structural applies are filtered by an apply-scope guard, not a wall-clock window
`StructureSyncController` SHALL suppress the echo of a remote structural apply using an apply-scope guard tied to the extent of that apply, rather than a fixed-duration wall-clock suppression window. The guard SHALL remain active for exactly as long as the remote structural apply it scopes is in progress, including applies that take an extended, variable time (such as a full timeline rebuild), rather than for a fixed duration chosen to outlast the typical case.

#### Scenario: A long remote rebuild stays suppressed for its full duration
- **WHEN** a remote structural message triggers a rebuild that takes longer than the previous fixed suppression window
- **THEN** the controller's own structural poll SHALL NOT treat that rebuild's in-progress changes as a local edit for any part of its duration

#### Scenario: Suppression ends when the apply ends, not on a timer
- **WHEN** a remote structural apply completes
- **THEN** the apply-scope guard SHALL clear at that point
- **AND** SHALL NOT remain active for a fixed duration afterward

### Requirement: A bounded horizon filters late-arriving asynchronous echo callbacks
Because xStudio delivers some structural and playback change callbacks asynchronously, arriving after the apply scope that caused them has exited, `claim_category()` SHALL be a no-op for a bounded horizon after the controller stamps a remote apply, so a late-arriving echo callback cannot trigger a claim. This horizon SHALL be the only remaining time-window mechanism in the claim path; its failure mode SHALL be limited to an unnecessary pending claim, never a re-broadcast.

#### Scenario: A late async callback within the horizon does not claim
- **WHEN** an asynchronous callback attributable to a remote apply arrives within the horizon after that apply was stamped
- **THEN** any `claim_category()` call it triggers SHALL be a no-op

#### Scenario: A callback outside the horizon may claim normally
- **WHEN** an asynchronous callback arrives after the horizon has elapsed
- **THEN** `claim_category()` SHALL evaluate normally, as it would for any other input-driven call

### Requirement: The session dialog offers an identity override

xStudio's session dialog SHALL offer an optional identity field, pre-filled with the identity resolved from the local machine, which the user may replace before connecting.

Leaving the field as presented SHALL be equivalent to supplying no override. The dialog SHALL NOT block connection on the field being populated.

The resolved and overridden identities SHALL come from the shared sync core rather than from an xStudio-specific implementation, on the same terms as every other protocol behaviour the two host applications share.

#### Scenario: Connecting without touching the field

- **WHEN** a user connects without editing the identity field
- **THEN** the machine-resolved identity SHALL be used

#### Scenario: Connecting with an edited identity

- **WHEN** a user edits the identity field before connecting
- **THEN** peers SHALL see the edited identity
- **AND** it SHALL be marked as user-entered

#### Scenario: Identity resolution is not reimplemented per host

- **WHEN** the plugin resolves the local identity
- **THEN** it SHALL obtain it from the shared sync core
- **AND** SHALL NOT derive identity fields itself

### Requirement: The Session menu offers driverless-session recovery

The xStudio plugin SHALL offer a "Become Controller" item, which sets this peer's session role to `driver`. It SHALL sit in the same top-level "Session" menu as the existing session management items, consistent with those items being direct children rather than nested under a submenu.

The item SHALL be enabled only while the session contains no peer eligible to be host on account of role, and SHALL be disabled otherwise.

The item SHALL set the role and nothing else. Host follows from the next election rather than being assigned by the action.

The eligibility question SHALL be answered by the shared sync core rather than computed in the plugin, consistent with the module structure's rule that protocol decisions are not hand-replicated per application.

#### Scenario: The item sits in the Session menu

- **WHEN** the ORI Sync plugin is loaded in xStudio
- **THEN** "Become Controller" SHALL appear as a direct child of the "Session" menu

#### Scenario: The item is enabled only in the driverless condition

- **WHEN** the session contains at least one eligible driver
- **THEN** "Become Controller" SHALL be disabled

#### Scenario: Self-elevation grants the role

- **WHEN** the user selects "Become Controller" while the session has no eligible driver
- **THEN** this peer's role SHALL become `driver`
- **AND** the plugin SHALL NOT assign host locally
- **AND** the peer SHALL re-announce so other peers observe the new role

### Requirement: xStudio controllers broadcast unconditionally and let the core apply role

The xStudio controllers SHALL continue to invoke broadcast and category-claim operations without consulting this peer's role, leaving field stripping and claim refusal to the shared core. No controller SHALL acquire a role branch on a broadcast path.

Where role is needed for presentation, it SHALL be read from the shared core through a single predicate, in keeping with the module structure's state-ownership rule that a controller owns its domain's state and does not duplicate session-level decisions.

Local interaction SHALL NOT be blocked on account of role. A peer whose broadcasts are stripped SHALL respond to its own user normally and re-converge when a driver next broadcasts.

#### Scenario: No controller gates a broadcast on role

- **WHEN** any controller emits a broadcast
- **THEN** it SHALL do so without testing this peer's role

#### Scenario: A viewer still interacts locally in xStudio

- **WHEN** a user whose peer holds the `viewer` role scrubs, plays, or annotates in xStudio
- **THEN** xStudio SHALL respond locally as it normally would
- **AND** nothing session-visible SHALL be emitted

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

#### Scenario: Role reaches the panel through the shared projection

- **WHEN** the xStudio session state panel displays peer roles
- **THEN** it SHALL read them from the shared projection
- **AND** SHALL NOT derive them from controller state

