## ADDED Requirements

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
