## ADDED Requirements

### Requirement: Only the poll thread performs xStudio actor I/O

The xStudio Python `Connection` is not safe for concurrent use: a synchronous
`request_receive` issued while another thread has one in flight on the same
connection can block for the **entire** `default_timeout_ms` rather than
failing fast or merely running slowly. Exactly one thread — the poll thread —
SHALL therefore perform actor I/O.

"Actor I/O" means any operation that round-trips to an xStudio actor:
`request_receive` and its bounded variants, sends on an actor handle, reading
or writing a module attribute, reading a wrapper property backed by an actor
(playhead position, playing state, container selection), and constructing a
wrapper whose initialiser performs such reads.

Handlers invoked on an xStudio callback thread SHALL NOT perform actor I/O,
directly or through a helper. They SHALL confine themselves to data already
carried by the event, cheap local state, and enqueuing onto `_cmd_queue`.

Where a handler currently reads from an actor in order to decide how to
dispatch, the decision SHALL be deferred to the poll thread — by enqueuing the
event's own data or handles — rather than performed in the handler. Enqueuing
cannot return a value, so a handler that needs one is, by construction, doing
work that belongs on the poll thread.

Throttling, rate-limiting, or locking a violating call SHALL NOT be treated as
satisfying this requirement. Those reduce how often the collision occurs
without removing it, and a lock in particular converts an intermittent stall
into a guaranteed one for the blocked thread. Only moving the call off the
callback thread satisfies it.

This constrains *which thread issues actor I/O*, and is stricter than the
existing "Threading invariant preserved" requirement, which constrains access
to the `SyncManager`. A handler can respect that requirement — touching no
manager state — and still stall the session by reading a playhead attribute.

#### Scenario: An event handler needs playhead state

- **WHEN** an xStudio event handler requires the active playhead's position,
  playing state, or any attribute in order to act
- **THEN** it SHALL enqueue the work onto `_cmd_queue` rather than read the
  playhead on the callback thread

#### Scenario: An event handler dispatches on an attribute's identity

- **WHEN** a handler must know which attribute changed before it can act
- **THEN** the attribute handle SHALL be enqueued and its identity resolved on
  the poll thread, rather than read on the callback thread to choose a branch

#### Scenario: Throttling is not a substitute

- **WHEN** a callback-thread call to an actor is made less frequent, guarded by
  a lock, or given a shorter timeout
- **THEN** this requirement SHALL still be considered unmet until the call no
  longer runs on the callback thread

#### Scenario: Concurrency is safe under either listener topology

- **WHEN** the plugin runs against an xStudio build that dispatches each
  subscription's callbacks on its own thread, so that callbacks may execute
  concurrently with one another
- **THEN** the plugin SHALL continue to function, because no callback issues
  actor I/O and there is nothing for the additional concurrency to collide with
