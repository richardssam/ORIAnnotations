## REMOVED Requirements

### Requirement: Shared cross-thread state ownership

**Reason**: The requirement conflated thread safety with attribute placement. Cross-thread
safety comes from the poll-thread invariant and the GIL — not from which object holds a
field — so parking domain state on `ORISyncPlugin` bought nothing and cost encapsulation
(~200 `self.plugin.<attr>` reach-backs from `playback_sync.py` alone, plus a `disconnect()`
that hand-clears controller privates). Replaced by "State ownership follows domain" below;
the threading guarantee is carried unchanged by the existing "Threading invariant
preserved" requirement.

**Migration**: State named by the old requirement moves to its owning controller —
`_selection_broadcast_suppress_until`, `_applying_pinned_mode`, and the frame/play
echo-guard fields (`_last_polled_frame`, `_last_applied_frame`, `_last_polled_playing`)
to `PlaybackSyncController`; `_reload_suppress_until` to `AnnotationSyncController`;
`_structural_mutation_suppress_until` to `StructureSyncController`. `manager`,
`_cmd_queue`, and `_sync_playlists` remain plugin attributes. Reads previously written
`self.plugin.<attr>` become `self.<attr>` within the owning controller and
`self.plugin.<owner>.<attr>` from a sibling.

## ADDED Requirements

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

## MODIFIED Requirements

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
