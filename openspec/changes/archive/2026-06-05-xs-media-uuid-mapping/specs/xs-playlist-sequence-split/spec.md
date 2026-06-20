## ADDED Requirements

### Requirement: xStudio master emits flat-playlist OTIO alongside sequence OTIO

When xStudio is master and a playlist contains a Timeline, `_build_otio_timelines` SHALL emit two OTIO timelines per playlist:
1. The sequence OTIO (existing behaviour, `guid = str(xs_timeline.uuid)`)
2. A flat-playlist OTIO (`xs_flat_playlist: true`, `guid = str(playlist.uuid)`, `xs_sequence_guid = str(xs_timeline.uuid)`) with clips in `playlist.media` order.

#### Scenario: Both timelines appear in snapshot
- **WHEN** xStudio master builds the state snapshot for a playlist that has a Timeline child
- **THEN** the snapshot `timelines` dict contains both the sequence OTIO and a flat-playlist OTIO with `xs_sequence_guid` linking them

#### Scenario: Flat playlist carries bin order
- **WHEN** the xStudio master playlist media bin order differs from the sequence edit order
- **THEN** the flat-playlist OTIO clips are in bin order and the sequence OTIO clips are in edit order

---

### Requirement: xStudio client updates existing bin on flat-playlist receive

When xStudio is a client and receives a flat-playlist OTIO that contains `xs_sequence_guid` in its metadata, the plugin SHALL reorder the existing playlist's media bin to match the flat-OTIO clip order. It SHALL NOT create a new playlist.

#### Scenario: Bin reordered to match flat-OTIO
- **WHEN** xStudio client receives a flat-playlist OTIO with a known `xs_sequence_guid`
- **THEN** the corresponding playlist's media bin is reordered to match and no new playlist is created

#### Scenario: Unknown xs_sequence_guid treated as new flat playlist
- **WHEN** xStudio client receives a flat-playlist OTIO whose `xs_sequence_guid` does not match any known playlist
- **THEN** the flat-playlist OTIO is treated as a standalone flat playlist (existing flat-playlist path)

---

### Requirement: RV ignores flat-playlist OTIOs

When RV receives a state snapshot containing a timeline with `xs_flat_playlist: true` in its metadata, it SHALL skip that timeline entirely. No `RVSequenceGroup` SHALL be created for it.

#### Scenario: RV rebuild skips flat-playlist timeline
- **WHEN** `_rebuild_rv_from_otio_snapshot` iterates the snapshot timelines
- **THEN** any timeline with `metadata.get("xs_flat_playlist")` set to `true` is skipped without creating any RV nodes

---

### Requirement: Bin mirrors edit order on initial join from RV master

When xStudio joins a session mastered by RV (which has no bin concept), the newly created playlist's media bin order SHALL match the sequence edit order received in the OTIO.

#### Scenario: Bin = edit order when no flat-playlist OTIO present
- **WHEN** xStudio receives a snapshot with a sequence OTIO but no corresponding flat-playlist OTIO
- **THEN** the playlist media bin is populated in the same order as the Media track clips in the sequence OTIO

---

### Requirement: No duplicate media items on sequence OTIO receive

When xStudio creates a playlist and loads a sequence OTIO, it SHALL NOT call `add_media()` before `load_otio()`. `load_otio()` is solely responsible for media creation.

#### Scenario: No duplicates after load_otio
- **WHEN** xStudio client joins a session and `_do_load_timelines` processes a sequence OTIO
- **THEN** `playlist.media` contains exactly as many items as Media track clips in the OTIO, with no duplicates

---

### Requirement: Distinguishable initial names for sequence and bin nodes

When a bin representation is created in RV from a flat-playlist OTIO, it SHALL be named `<sequence_name> Playlist` to distinguish it from the sequence node. Identity SHALL be tracked by GUID thereafter regardless of subsequent renames.

#### Scenario: Bin node named with Playlist suffix
- **WHEN** RV receives a flat-playlist OTIO (if bin nodes are created in future scope)
- **THEN** the corresponding RV node is initialised with the name `<sequence_name> Playlist`

#### Scenario: Rename tracked by GUID
- **WHEN** a RENAME_TIMELINE event is received for the flat-playlist GUID
- **THEN** the corresponding RV node is renamed to the new name regardless of the current display name
