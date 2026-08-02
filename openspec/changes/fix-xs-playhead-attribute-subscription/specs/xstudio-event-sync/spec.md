# xstudio-event-sync (delta)

## MODIFIED Requirements

### Requirement: Event-Driven Playhead Sync

The xStudio plugin SHALL sync the playhead state by subscribing to playhead events (e.g. `position_atom`, `play_forward_atom`) instead of relying on a polling thread.

The plugin SHALL **own** that subscription. It SHALL wire the active playhead's `attribute_changed` callback itself, at every site that adopts an active playhead, and SHALL NOT delegate the job to `PluginBase.subscribe_to_playhead_events()`. On the supported xStudio build that base call establishes a second subscription route into a broadcast group the plugin already joins, and tears down the previous playhead's message handler on every viewport playhead event; because the client shares one listener actor per connection, either action can revoke the membership the plugin's own callbacks depend on. The callbacks then remain registered and silently stop firing.

Two obligations follow, and both are load-bearing because the failure they prevent is silent:

- The plugin SHALL NOT create a second subscription route into a broadcast group it already listens to.
- The plugin SHALL NOT issue a `leave` on a group whose membership other live callbacks share.

Playhead identity SHALL be compared by a stable key. Remote actor handles are fresh objects per access and do not compare equal even when they address the same actor, so identity comparison SHALL NOT rely on comparing those handles directly.

Position sync SHALL survive playhead replacement — viewport switches, on-screen source changes, and clip isolation all replace the playhead — without any further subscription call.

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

#### Scenario: An unchanged playhead is not re-adopted

- **WHEN** a playhead observation reports the same underlying actor as the one already active
- **THEN** the plugin SHALL treat it as unchanged
- **AND** SHALL NOT construct a new playhead wrapper or re-subscribe

#### Scenario: Silent loss of position broadcast is detectable

- **WHEN** the user scrubs while connected and synced
- **THEN** at least one position broadcast SHALL be observable in the plugin log
- **AND** an absence of such broadcasts across a session SHALL be treated as a defect, not as an idle session — selection-driven messages continuing to flow is NOT evidence that playhead sync is alive
