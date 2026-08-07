# Capability: xstudio-event-sync

## Purpose
TBD

## Requirements

### Requirement: Event-Driven Playhead Sync

The xStudio plugin SHALL sync the playhead state by subscribing to playhead events (e.g. `position_atom`, `play_forward_atom`) instead of relying on a polling thread.

The plugin SHALL **own** that subscription. It SHALL wire the active playhead's `attribute_changed` callback itself, at every site that adopts an active playhead, and SHALL NOT delegate the job to `PluginBase.subscribe_to_playhead_events()`. On the supported xStudio build that base call establishes a second subscription route into a broadcast group the plugin already joins, and tears down the previous playhead's message handler on every viewport playhead event; because the client shares one listener actor per connection, either action can revoke the membership the plugin's own callbacks depend on. The callbacks then remain registered and silently stop firing.

Two obligations follow, and both are load-bearing because the failure they prevent is silent:

- The plugin SHALL NOT create a second subscription route into a broadcast group it already listens to. This applies across *all* of the plugin's subscription paths, not just the playhead ones: an xStudio object can qualify for two of them at once — a sequence Timeline is both a tracked timeline and, once viewed, the viewed container — and the second join silences the first. The plugin SHALL therefore track which of its paths owns each joined group, admit only one owner per group, and release that ownership when the subscription is released.
- The plugin SHALL NOT issue a `leave` on a group whose membership other live callbacks share. On the supported build every subscription in the process shares one listener, so this means the plugin SHALL NOT leave any event group for the life of the connection. A handler that is finished SHALL detach from its group's dispatch instead. Leaving is not a safe way to resolve a duplicate join: doing so was observed to deafen the plugin to playhead, selection and timeline events alike, which is strictly worse than the duplicate it was meant to fix.

Re-acquisition SHALL NOT itself churn subscriptions. Adopting a playhead re-wires its callback, so any periodic or speculative re-check SHALL resolve the *same* viewport on every attempt rather than whichever the host answers with first, and SHALL act only on a reading it has confirmed. An unstable re-check destroys the subscription it exists to maintain.

Playhead identity SHALL be compared by a stable key. Remote actor handles are fresh objects per access and do not compare equal even when they address the same actor, so identity comparison SHALL NOT rely on comparing those handles directly.

Position sync SHALL survive playhead replacement — viewport switches, on-screen source changes, and clip isolation all replace the playhead — without any further subscription call.

The plugin SHALL acquire the active playhead from the **viewport**, and SHALL NOT acquire it from the host's global-active-playhead accessor (`PluginBase.current_playhead()`). That accessor answers with a value that is initialised to a stand-in "dummy" playhead which emits no position events, and which is not updated when a viewport connects to a playhead — so it can address a different playhead than the one on screen, for an unbounded period. Any read whose answer depends on *which* playhead is on screen — position, play state, or Pinned Source Mode — is subject to this obligation.

Playhead acquisition SHALL NOT depend solely on host-emitted playhead-change events. Not every replacement is announced: the host suppresses its viewport-playhead event when the viewport's playhead is unchanged, so a session can change what the playhead is *sourced from* — for example when a sequence is built out of a bin — with no event and no selection change. The plugin SHALL therefore re-check the active playhead periodically as well, and that re-check SHALL be a no-op when the playhead is unchanged, so it neither re-subscribes nor allocates.

#### Scenario: Local playback updates trigger broadcast

- **WHEN** the user scrubs or starts playback in the local xStudio viewport
- **THEN** the plugin receives a playhead event and queues a `playback_settings` message to the network

#### Scenario: Remote playback updates are guarded against echo loops

- **WHEN** a remote peer updates the local xStudio playhead frame
- **THEN** the resulting local playhead event is caught by an echo guard (e.g. checking against `_last_applied_frame`) and is NOT broadcast back to the network

#### Scenario: Position events survive playhead replacement

- **WHEN** the active playhead is replaced — a viewport switch, an on-screen source change, or entering/leaving single-clip isolation
- **THEN** the plugin SHALL wire `attribute_changed` on the newly adopted playhead
- **AND** subsequent scrubs SHALL still broadcast, with no further subscription call required

#### Scenario: A sequence built from a bin still broadcasts position

- **WHEN** the user builds a sequence out of media in a bin and views it, with no accompanying selection change and no host playhead-change event
- **THEN** the plugin SHALL still end up wired to the playhead the viewport is using
- **AND** scrubbing that sequence SHALL broadcast position to peers
- **AND** recovery SHALL NOT depend on a remote peer driving the local view

#### Scenario: Viewing a tracked timeline does not silence its item events

- **WHEN** the viewed container becomes an xStudio object the plugin already subscribes to by another path — for example a sequence Timeline that is also a tracked timeline
- **THEN** the plugin SHALL NOT join that object's event group a second time
- **AND** SHALL NOT leave the existing subscription in order to replace it
- **AND** the existing subscription SHALL keep delivering, including the signals the second subscriber needs
- **AND** subsequent edits to that timeline SHALL still be broadcast to peers

#### Scenario: Moving a handler to a different container keeps events flowing

- **WHEN** a subscription that follows the viewed container — selection events, add_media detection — moves because the user views something else
- **THEN** the plugin SHALL detach the handler from the old group's dispatch rather than leaving that group
- **AND** every other subscription in the process SHALL keep delivering across the move

#### Scenario: An unchanged playhead is not re-adopted

- **WHEN** a playhead observation reports the same underlying actor as the one already active
- **THEN** the plugin SHALL treat it as unchanged
- **AND** SHALL NOT construct a new playhead wrapper or re-subscribe

#### Scenario: Silent loss of position broadcast is detectable

- **WHEN** the user scrubs while connected and synced
- **THEN** at least one position broadcast SHALL be observable in the plugin log
- **AND** an absence of such broadcasts across a session SHALL be treated as a defect, not as an idle session — selection-driven messages continuing to flow is NOT evidence that playhead sync is alive

### Requirement: Event-Driven Selection Sync
The xStudio plugin SHALL sync selection states by subscribing to the container's selection events (e.g. `selection_actor_atom`).

#### Scenario: Local selection updates trigger broadcast
- **WHEN** the user selects or deselects a clip in the xStudio timeline
- **THEN** the plugin receives a selection event and queues a `selection_changed` message to the network

### Requirement: Event-Driven Sequence Mutation Sync

The xStudio plugin SHALL sync timeline edits (insertions, deletions, reorders, renames) by subscribing to container events (e.g. `change_atom` and `item_atom`).

Sequence reconciliation SHALL converge. A reconciliation pass over a timeline that has not changed since the previous pass SHALL broadcast nothing, and a single user edit SHALL produce a bounded number of messages regardless of how many passes run. Broadcasting the correct message repeatedly is not conformance: the observed failure broadcast a valid `INSERT_CHILD` 153 times for three clips, which satisfied the requirement as previously written while leaving the peer wrong and both sessions unusable.

Reconciliation SHALL have a single authority per pass. Where the plugin can both reconcile incrementally (diff the host timeline and emit per-clip mutations) and rebuild wholesale (reconstruct the timeline and emit a replacement), one pass SHALL NOT do both for the same timeline — a rebuild replaces the state an incremental diff is computed against, so the two in combination cannot reach a fixed point even though each is correct alone.

The incremental diff SHALL be computed against a record of what this peer has already broadcast for that timeline, not against local structural state that other paths may replace.

A reconciliation pass that makes no changes SHALL be observable in the plugin log at a bounded rate, so that convergence can be confirmed from a log rather than inferred from the absence of complaints.

#### Scenario: Clip structural edits trigger broadcast

- **WHEN** the user modifies the timeline structure (e.g. adds a new clip or reorders clips)
- **THEN** the plugin receives a change event and queues the corresponding OTIO mutation (e.g. `insert_child`, `remove_child`) to the network

#### Scenario: An unchanged sequence is not re-broadcast

- **WHEN** a reconciliation pass runs against a sequence whose xStudio contents are unchanged since the previous pass
- **THEN** the plugin SHALL broadcast no structural message for that sequence
- **AND** repeated passes SHALL continue to broadcast nothing while it stays unchanged

#### Scenario: A clip appended to a sequence reaches the peer once

- **WHEN** the user adds a clip to the end of a sequence on the host
- **THEN** the peer SHALL show that clip in the same position
- **AND** the host SHALL broadcast a bounded number of structural messages for that edit, not one per reconciliation pass

#### Scenario: Rebuild and incremental reconciliation do not run together

- **WHEN** a pass determines that a timeline needs a wholesale rebuild
- **THEN** incremental per-clip reconciliation for that timeline SHALL be skipped for that pass
- **AND** the clips the incremental path would have inserted SHALL still reach the peer, carried by the rebuild

#### Scenario: Local structural state does not make sent edits look unsent

- **WHEN** a timeline is re-registered locally, replacing the object an earlier insert was written into
- **THEN** the plugin SHALL NOT re-broadcast the clips it has already sent for that timeline

### Requirement: Non-Blocking Event Handlers
The xStudio plugin SHALL process event callbacks efficiently without blocking the xStudio UI or internal event threads.

#### Scenario: Network broadcasts are asynchronous
- **WHEN** an event callback fires and requires a network broadcast
- **THEN** the callback pushes the mutation to an asynchronous command queue (`_cmd_queue`) where a dedicated background worker consumes it for RabbitMQ transmission
