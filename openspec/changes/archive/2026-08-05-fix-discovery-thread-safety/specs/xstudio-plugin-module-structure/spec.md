## MODIFIED Requirements

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
