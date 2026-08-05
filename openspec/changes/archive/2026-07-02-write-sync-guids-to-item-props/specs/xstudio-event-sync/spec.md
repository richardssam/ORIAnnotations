## MODIFIED Requirements

### Requirement: Event-Driven Sequence Mutation Sync

The xStudio plugin SHALL sync timeline edits (insertions, deletions, reorders, renames) by subscribing to container events (e.g. `change_atom` and `item_atom`).

When the plugin resolves clip identity from a `to_otio_string()` export — in reorder detection (`poll_sequence_reorders`), track-deletion detection (`poll_sequence_track_deletions`), and source-range detection (`poll_sequence_source_ranges`) — it SHALL read the clip's sync GUID directly from `clip.metadata["sync"]["guid"]` rather than matching `clip.media_reference.target_url` against stored media paths. Because xStudio exports `MissingReference` for its internal media, URL/path matching MAY only be used as a fallback for clips whose exported metadata has no sync GUID (e.g. a clip the user just added that has not yet been through a build pass).

#### Scenario: Clip structural edits trigger broadcast

- **WHEN** the user modifies the timeline structure (e.g. adds a new clip or reorders clips)
- **THEN** the plugin receives a change event and queues the corresponding OTIO mutation (e.g. `insert_child`, `remove_child`) to the network

#### Scenario: Reorder detection resolves clip identity by sync GUID

- **WHEN** the user reorders clips in an xStudio sequence and the plugin re-serialises the timeline with `to_otio_string()`
- **THEN** the plugin maps each exported clip to its sync GUID via `clip.metadata["sync"]["guid"]` and broadcasts the resulting `MOVE_CHILD` mutations, without depending on `clip.media_reference.target_url`

#### Scenario: Clip without a sync GUID falls back to URL matching

- **WHEN** an exported clip has no `metadata["sync"]["guid"]` (e.g. it was just added and has not yet had its `item_prop` written)
- **THEN** the plugin falls back to URL/stem matching for that clip only, and does not treat the sequence as empty
