# OpenRV Sync Plugin Specification

## Purpose
Enable real-time, bi-directional synchronization between OpenRV instances using OpenTimelineIO and RabbitMQ.
## Requirements
### Requirement: Network Transport (RabbitMQ)
The plugin SHALL use RabbitMQ fanout exchanges for session-based broadcasting of sync events. The exchange name SHALL be derived from the session id, and a peer SHALL discard messages it published itself.

#### Scenario: Broadcast reaches every other peer in the session

- **WHEN** a peer publishes a sync message in session `{name}`
- **THEN** it SHALL be published to a fanout exchange derived from `{name}`
- **AND** every other peer bound to that exchange SHALL receive it

#### Scenario: Sessions are isolated

- **WHEN** two peers are connected to different session names on the same broker
- **THEN** neither SHALL receive the other's messages

#### Scenario: A peer ignores its own broadcast

- **WHEN** a peer receives a message whose `source_guid` equals its own guid
- **THEN** it SHALL discard the message without applying it
- **AND** no echo SHALL be re-broadcast as a result

### Requirement: Session State Management

The plugin SHALL support runtime-configurable session identity. The session name and RabbitMQ host SHALL be determined at connect time from either the `ORI_SESSION` environment variable or interactive user input, replacing the previously hardcoded `SYNC_SESSION_ID` constant.

#### Scenario: Late joiner synchronization

- **WHEN** a new instance joins an active session
- **THEN** it SHALL request a full state snapshot from the Master peer
- **AND** it SHALL rebuild its local RV session (media sources, timeline) based on the received snapshot.

#### Scenario: A failed annotation replay does not block the rest of the join

- **WHEN** the joining instance replays the snapshot's annotation clips and one clip's event raises an exception (e.g. an unexpected event shape, or a lookup that fails for that specific clip)
- **THEN** that one event's replay SHALL be skipped and logged
- **AND** every other event, clip, and kind SHALL still be replayed
- **AND** the join SHALL still apply playback state, display state, and color sync, none of which SHALL be silently skipped as a side effect of the annotation-replay failure

#### Scenario: Session name from ORI_SESSION

- **WHEN** `ORI_SESSION` is set at launch
- **THEN** the plugin SHALL parse `[host:]session_name` and call `connect_to_session(host, name)` automatically, with no hardcoded fallback name

#### Scenario: Session name from interactive menu

- **WHEN** `ORI_SESSION` is not set and the user selects Create or Join Session from the OTIO Sync menu
- **THEN** the plugin SHALL present a two-field dialog and call `connect_to_session(host, name)` on confirm

### Requirement: Synchronized Playback
The plugin SHALL synchronize the playhead (frame) and playback state (play/stop) between all instances.

The broadcast frame is expressed relative to the view the sender is displaying, so the accompanying timeline guid SHALL identify **that** view — the isolated clip's own timeline when a single clip is displayed, the sequence's timeline when the sequence is displayed. A position SHALL NOT be attributed to a timeline the sender is not displaying, because a receiver has no other way to tell which coordinate space a frame belongs to and would apply it to the wrong one.

When the displayed view has no timeline shared with the session, the broadcast SHALL carry no timeline guid rather than substituting the session's active timeline. "A position in a view you do not have" and "a position in your sequence" are different claims, and only the first one is true.

#### Scenario: Scrubbing while paused

- **WHEN** a paused peer moves its playhead to a new frame
- **THEN** it SHALL broadcast the new playback state, carrying the frame, the playing flag, the playback mode, and the timeline guid
- **AND** every other peer SHALL move its playhead to the corresponding frame on that timeline

#### Scenario: Play and stop propagate

- **WHEN** a peer starts or stops playback
- **THEN** every other peer SHALL enter the same playing/stopped state

#### Scenario: Applying a remote state does not echo

- **WHEN** a peer applies a playback state received from another peer
- **THEN** it SHALL NOT re-broadcast that state back to the session

#### Scenario: An isolated clip is labelled with its own timeline

- **WHEN** OpenRV is displaying a single isolated clip and broadcasts a playback state
- **THEN** the timeline guid SHALL be that clip's own timeline guid
- **AND** SHALL NOT be the guid of the sequence the clip belongs to

#### Scenario: A position from an unshared view is not attributed to a shared timeline

- **WHEN** OpenRV is displaying media that has no timeline shared with the session
- **THEN** the broadcast SHALL carry no timeline guid
- **AND** peers SHALL NOT move their playheads in response to it

#### Scenario: Sequence views are unaffected

- **WHEN** OpenRV is displaying a sequence and broadcasts a playback state
- **THEN** the timeline guid SHALL be that sequence's timeline guid
- **AND** peers displaying the same sequence SHALL move their playheads to the corresponding frame

### Requirement: Synchronized Selection
The plugin SHALL synchronize the active node/clip selection.

#### Scenario: Selection propagates to peers

- **WHEN** a user selects a clip in one instance
- **THEN** every other instance SHALL reflect the same clip as selected

#### Scenario: Applying a remote selection does not echo

- **WHEN** an instance applies a selection received from a peer
- **THEN** it SHALL NOT re-broadcast that selection

### Requirement: Synchronized Annotations
The plugin SHALL synchronize paint strokes between instances by intercepting RV drawing events, translating them into the flat view `SyncEvent` format, and broadcasting them. Upon receiving flat view annotations, the plugin SHALL apply them back to the RV property graph such that the annotation is actually rendered by RV, not merely present as unread node properties.

The plugin SHALL additionally bind RV's internal `clear-paint` and `clear-all-paint` events (in addition to the existing `graph-state-change` binding) so that local annotation deletion is detected and broadcast, and SHALL bind changes to `<node>.paint.show` so that toggling annotation visibility is detected and broadcast.

Because RV cannot mutate a dynamically-created pen node's properties from outside the call that created it, applying a mid-gesture partial update creates a fresh pen node per tick and supersedes the previous tick's node. When superseding a partial-tick pen node, the plugin SHALL delete that node's own RV properties (not merely remove it from the frame `order`), so mid-gesture ticks do not accumulate as orphaned property components on the node for the lifetime of the RV process.

#### Scenario: Translating stroke to flat view
- **WHEN** a user completes a paint stroke in RV
- **THEN** the plugin SHALL extract the stroke properties and broadcast them as a flat view annotation payload.

#### Scenario: Applying flat view stroke
- **WHEN** the plugin receives a flat view annotation payload or snapshot
- **THEN** it SHALL translate the flat data back into OpenRV's node-based property graph
- **AND** the written properties SHALL match the property set and per-frame key convention RV's own native annotate tool uses for that annotation kind, so the stroke is actually displayed rather than silently absent from the render
- **AND** this SHALL hold identically whether the stroke arrives via a live per-event broadcast, a delta insert, or a full state-snapshot replay on join

#### Scenario: Applied strokes key their RV frame bucket and startFrame by RV's native per-source frame
- **WHEN** the plugin resolves the target `RVPaint` node for an incoming annotation via `metaEvaluateClosestByType`
- **THEN** it SHALL use the frame number that call reports for that node — not a sequence-position or clip-local frame number the plugin computed independently — as both the paint node's `frame:<N>` bucket key and the written stroke's `startFrame`
- **AND** any internal bookkeeping keyed by "the frame this annotation occupies" (e.g. mid-gesture partial-stroke tracking) SHALL use that same reported frame number, so it stays consistent with the actual RV property location

#### Scenario: Immediate text annotation broadcast
- **WHEN** a user types or modifies a text annotation in OpenRV
- **THEN** the plugin SHALL immediately reconstruct the frame's annotation state and broadcast it using `REPLACE_ANNOTATION_COMMANDS`
- **AND** the plugin SHALL NOT buffer text annotations in the pending stroke queue.

#### Scenario: Clear Frame is detected and broadcast
- **WHEN** the user chooses "Clear Frame" in RV's Annotate mode, firing the `clear-paint` internal event
- **THEN** the plugin SHALL identify the affected annotation clip and broadcast its surviving (possibly empty) commands via `REPLACE_ANNOTATION_COMMANDS`

#### Scenario: Clear All Frames on Timeline is detected and broadcast
- **WHEN** the user chooses "Clear All Frames on Timeline" in RV's Annotate mode, firing the `clear-all-paint` internal event
- **THEN** the plugin SHALL identify every affected annotation clip and broadcast each one's surviving (possibly empty) commands via `REPLACE_ANNOTATION_COMMANDS`

#### Scenario: Show Drawings toggle is detected and broadcast
- **WHEN** the user toggles "Show Drawings" for an RV source, changing `<node>.paint.show`
- **THEN** the plugin SHALL broadcast the new value as `annotations_visible` via `display_settings`

#### Scenario: Superseded partial-tick pen node is deleted, not just dereferenced
- **WHEN** a later mid-gesture partial update supersedes an earlier tick's pen node for the same live gesture
- **THEN** the plugin SHALL delete the superseded pen node's own RV properties in addition to removing it from the frame `order`

### Requirement: Dynamic OTIO Sync menu reflects connection state

The plugin SHALL rebuild the "OTIO Sync" menu via `defineModeMenu` whenever connection state changes, showing session management items appropriate to the current state. The connected/disconnected menus below apply when the sync core imported successfully; when it did not, the unavailable menu defined in "Sync core import failure is visible in the menu" applies instead.

The "Sync Status" item is replaced by "Session State…", which opens the shared Session State panel (see the `session-state-ui` spec) instead of printing a one-line summary to the console. The connected menu additionally offers "Force Resync", which re-requests the full session state from the master.

#### Scenario: Disconnected menu

- **WHEN** the plugin is not in a session and the sync core is available
- **THEN** the OTIO Sync menu SHALL contain "Create Session…", "Join Session…", a separator, "Add Clip to Timeline…", and "Session State…"
- **AND** "Add Clip to Timeline…" SHALL be in `DisabledMenuState`

#### Scenario: Connected menu

- **WHEN** the plugin is in a session named `{name}`
- **THEN** the OTIO Sync menu SHALL contain "Leave Session ({name})", "Force Resync", a separator, "Add Clip to Timeline…", and "Session State…"
- **AND** "Create Session…" and "Join Session…" SHALL NOT appear

#### Scenario: Force Resync is unavailable to the master

- **WHEN** the plugin is in a session and this peer is the master
- **THEN** "Force Resync" SHALL be in `DisabledMenuState`
- **AND** selecting it on a non-master peer SHALL call `request_state()` on the sync manager

### Requirement: connect_to_session and disconnect_from_session methods

The plugin SHALL expose `connect_to_session(host, session_name)` and `disconnect_from_session()` as first-class methods callable from menu callbacks and startup code.

#### Scenario: connect_to_session initialises SyncManager

- **WHEN** `connect_to_session(host, name)` is called
- **THEN** the plugin SHALL create a `SyncManager` with `session_id=name`, create a `RabbitMQNetwork` with `host=host`, call `start_session()`, and update menu state

#### Scenario: disconnect_from_session tears down cleanly

- **WHEN** `disconnect_from_session()` is called
- **THEN** all background threads SHALL stop, the `SyncManager` SHALL be set to `None`, and the menu SHALL rebuild to the disconnected state

### Requirement: Asynchronous Polling

The plugin SHALL use a background consumer thread to receive messages without blocking the RV UI. The poll loop (`poll_network`) SHALL reside in `plugin.py` and SHALL delegate action handling to domain-specific controller methods via `_handle_action`. Structural polling (sequence reorders, new sequences, renames) and display state polling SHALL be delegated to the `SequenceSyncController` and `DisplaySyncController` respectively.

#### Scenario: Poll loop delegates structural checks

- **WHEN** the poll timer fires and `sync_manager.status` is `STATE_SYNCED`
- **THEN** `poll_network` SHALL call `self.sequence.check_sequence_reorders()`, `self.sequence.poll_new_sequences()`, `self.sequence.poll_sequence_renames()`, and `self.display.broadcast_display_state()`

### Requirement: Synchronized timeline deletion

The RV plugin SHALL detect when a user deletes a synced sequence/playlist and
propagate the deletion to peers, and SHALL tear down the local viewer container
when a peer's deletion is received.

Detection SHALL occur in the structural poll loop, as a counterpart to
`poll_new_sequences`. When a previously-synced sequence is no longer present in
the RV node graph, the plugin SHALL call `broadcast_remove_timeline` with that
timeline's GUID. Following the ordering contract, the plugin SHALL ensure the
on-screen source has moved to a surviving sequence before broadcasting the
removal, so the removed timeline is not the active one except when it is the last
remaining timeline.

#### Scenario: User deletes a synced sequence in RV

- **WHEN** the structural poll detects that a previously-synced sequence is no
  longer present in the RV node graph
- **THEN** the plugin SHALL call `broadcast_remove_timeline` with that timeline's
  GUID after switching the on-screen source to a surviving sequence

#### Scenario: Peer removal tears down the RV container

- **WHEN** the plugin receives a `remove_timeline` action from the sync manager
- **THEN** it SHALL tear down the RV viewer container corresponding to the removed
  timeline, symmetric to the container creation performed on `add_timeline`

#### Scenario: Removal of an unknown timeline is ignored

- **WHEN** a `remove_timeline` action references a timeline the plugin has no
  container for
- **THEN** the plugin SHALL take no action and SHALL NOT raise

### Requirement: Track identity survives re-initialisation
When OpenRV re-initialises a timeline's tracks, the rebuilt tracks SHALL keep
the sync GUIDs the originals had, so that peers' references to them stay valid.

OpenRV re-initialises a sequence's tracks after the first media is added. If the
rebuilt Media track is given a fresh GUID, every later insertion addresses a
container no peer holds, and the media silently never reach them.

#### Scenario: Media added after a re-initialisation reach peers
- **WHEN** an OpenRV peer adds media, causing its tracks to be re-initialised
- **AND** further media are added afterwards
- **THEN** every added item SHALL reach the other peers
- **AND** each SHALL become viewable there

#### Scenario: A peer that joined earlier is unaffected by the rebuild
- **WHEN** a peer joined before the re-initialisation
- **THEN** the container GUIDs it holds SHALL still resolve afterwards

### Requirement: OpenRV follows the host's view instead of deriving its own
When OpenRV is not the session host, it SHALL NOT broadcast visibility changes, and SHALL adopt the host's reported clip and view mode rather than selecting a view of its own.

OpenRV currently broadcasts a visibility change whenever its local view node changes — including when that change was itself caused by applying a remote message — and independently decides between sequence and isolated-clip views. Both behaviours belong to a peer that owns visibility, not to a follower.

#### Scenario: A local view-node change is not broadcast by a follower
- **WHEN** OpenRV is not the host and its view node changes for any reason
- **THEN** it SHALL NOT broadcast a visibility change

#### Scenario: OpenRV adopts the host's view mode
- **WHEN** the host reports viewing an isolated clip
- **THEN** OpenRV SHALL display that clip in an isolated view
- **WHEN** the host reports viewing the sequence
- **THEN** OpenRV SHALL display the sequence

#### Scenario: OpenRV retains position and annotation authority
- **WHEN** an OpenRV user scrubs, plays, stops, or annotates while not the host
- **THEN** those actions SHALL be broadcast and honoured by other peers, as before

#### Scenario: OpenRV hosts when it is the only capable peer
- **WHEN** a session contains OpenRV peers only
- **THEN** one SHALL be elected host and SHALL broadcast visibility changes normally

### Requirement: Sync core import failure is visible in the menu

When `otio_sync_core` or `RabbitMQNetwork` cannot be imported, the plugin SHALL
surface that failure in the OTIO Sync menu rather than presenting session items
that silently do nothing. A swallowed import error — as in the vendored-`pika`
packaging incident — must not be indistinguishable from a working plugin.

The failure SHALL additionally be reported to the plugin log and to stderr,
including the underlying exception text, so the cause is recoverable from a
session log without attaching a debugger.

#### Scenario: Sync core unavailable

- **WHEN** the plugin loads and the `otio_sync_core` / `RabbitMQNetwork` import raised `ImportError`
- **THEN** the OTIO Sync menu SHALL show a single item labelled to state that sync is unavailable because the sync core failed to import
- **AND** that item SHALL be in `DisabledMenuState`
- **AND** "Create Session…", "Join Session…", and "Add Clip to Timeline…" SHALL NOT be offered

#### Scenario: Import failure is logged with its cause

- **WHEN** the sync core import fails
- **THEN** the plugin SHALL log the originating `ImportError` message
- **AND** SHALL also write it to stderr so it is visible in the RV console

#### Scenario: Sync core available

- **WHEN** the plugin loads and the sync core imports successfully
- **THEN** the OTIO Sync menu SHALL be built from the connected/disconnected states as before
- **AND** no unavailable item SHALL appear

#### Scenario: Controller modules do not abort the mode load

- **WHEN** any controller module (`sequence_sync.py`, `playback_sync.py`, `display_sync.py`, `annotation_sync.py`, `color_sync.py`) imports a name from `otio_sync_core` at module level
- **THEN** that import SHALL be wrapped so a missing sync core raises no exception out of the module
- **AND** `plugin.py` SHALL still import successfully and build the unavailable menu
- **AND** the substituted values SHALL NOT be functional stubs — any code path reaching one SHALL fail loudly rather than behave as if sync were working

### Requirement: Sync is unreachable when the core is unavailable

Tolerating a missing sync core at import time SHALL NOT make the plugin appear
functional. The import guards exist only so the failure can be *reported*; every
route into an actual session SHALL remain closed.

#### Scenario: No route into a session

- **WHEN** the sync core failed to import
- **THEN** `connect_to_session` SHALL return without creating a `SyncManager`
- **AND** the menu SHALL offer no item capable of opening a session
- **AND** no protocol message SHALL be sent or applied

### Requirement: A remote view is compared against the displayed view
OpenRV SHALL decide whether a remote view instruction requires action by
comparing it against the view it is **currently displaying**, not against the
last view it adopted from a peer.

Those two diverge the moment the user changes the view locally, which the
application permits and which no message records. A peer that compares against
the last adopted value then reads a correct instruction as a no-op: it believes
it is already showing what the host asked for, while showing something else.

#### Scenario: A locally isolated clip does not block a later sequence instruction
- **WHEN** the user isolates a clip in OpenRV, changing the view locally
- **AND** the host subsequently reports sequence view
- **THEN** OpenRV SHALL return to sequence view

#### Scenario: An instruction matching the displayed view is still a no-op
- **WHEN** the host reports the view OpenRV is already displaying
- **THEN** no view switch SHALL be performed

#### Scenario: Ignoring the host's view is recorded
- **WHEN** OpenRV receives the host's view and does not adopt it
- **THEN** it SHALL record that fact and the reason
- **AND** the record SHALL be observable without reading application logs

### Requirement: A broadcast describes the view being displayed when it is sent
The `view_mode` and `clip_guid` on an outbound playback broadcast SHALL describe the view OpenRV is displaying at the moment the message is built, not the last view the plugin recorded.

Those two are updated by the view-change handler, and a frame-changed broadcast can be dispatched before that handler runs — the frame changes as part of the switch. A broadcast built from the recorded values therefore describes the view the application has already left, while carrying the new view's frame.

This matters beyond the message itself: a peer that applies such a position moves its own playhead, which can present as a local selection on that peer and be reported back, undoing the switch that started it.

#### Scenario: A view switch does not broadcast the previous view

- **WHEN** OpenRV switches to an isolated clip and a frame-changed broadcast is dispatched during the switch
- **THEN** the broadcast SHALL NOT report the previously displayed view mode
- **AND** the reported view SHALL match the view node OpenRV is displaying

#### Scenario: The displayed view is observable in the log

- **WHEN** a playback broadcast is sent
- **THEN** the log line SHALL record both the displayed view and the broadcast view mode
- **AND** a disagreement between them SHALL be visible without attaching a debugger

#### Scenario: A settled view still broadcasts normally

- **WHEN** OpenRV is displaying a view it has finished switching to and the user scrubs
- **THEN** the broadcast SHALL carry that view's mode and clip guid as before
