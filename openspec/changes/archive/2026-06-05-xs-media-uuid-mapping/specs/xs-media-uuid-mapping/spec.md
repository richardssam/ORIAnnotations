## ADDED Requirements

### Requirement: Session-local media identity mapping

The plugin SHALL maintain two session-local dicts cleared on disconnect:
- `_sync_guid_to_xs_media: dict[str, media_obj]` — maps OTIO sync GUID to live xStudio media object.
- `_xs_uuid_to_sync_guid: dict[str, str]` — maps `str(media.uuid)` to sync GUID.

These dicts SHALL be the primary mechanism for resolving media identity. Name-based scans of `playlist.media` SHALL NOT be used as the primary lookup path.

#### Scenario: Mapping cleared on disconnect
- **WHEN** the plugin disconnects from a sync session
- **THEN** both `_sync_guid_to_xs_media` and `_xs_uuid_to_sync_guid` are cleared

#### Scenario: Mapping survives within a session
- **WHEN** media items are created during a session
- **THEN** they remain accessible via mapping lookup for the duration of that session without re-scanning `playlist.media`

---

### Requirement: Bootstrap mapping at session connect

After media items are created by `load_otio()` or `add_media()` at session join, the plugin SHALL scan `playlist.media` once and match each item to its OTIO clip using normalised path comparison against `clip.media_reference.target_url`. A filename-stem fallback SHALL be used when path matching fails.

Path/URI normalisation SHALL strip `file://` schemes and normalise platform separators using `_uri_to_posix_path` or equivalent before comparison.

#### Scenario: Successful path bootstrap
- **WHEN** a session join completes and `playlist.media` contains items with full-path names
- **THEN** each item is mapped to its OTIO clip sync GUID via normalised path comparison

#### Scenario: Filename-stem fallback
- **WHEN** a media item's name does not match any OTIO clip by full path
- **THEN** the plugin falls back to comparing the basename stem of the media name against the OTIO clip name

#### Scenario: Unmatched item logged as warning
- **WHEN** a media item cannot be matched to any OTIO clip after path and filename-stem comparison
- **THEN** a warning is logged and the item is excluded from the mapping (legacy name-based fallback used for that item only)

---

### Requirement: Dynamic mapping maintenance

The plugin SHALL update both dicts immediately when media items are created or destroyed during an active session.

#### Scenario: Remote media add registered
- **WHEN** `_apply_remote_clip_insert` or `_apply_flat_playlist_insert` creates a new xStudio media item
- **THEN** the new item's `str(media.uuid)` and sync GUID are registered in both dicts

#### Scenario: Local media add registered
- **WHEN** `_poll_sequence_new_media` or `_poll_flat_playlist_new_media` detects a locally added item
- **THEN** the item is registered in both dicts before the INSERT_CHILD broadcast

#### Scenario: Media removal evicts from mapping
- **WHEN** `_apply_remote_remove_child` or a local deletion is processed
- **THEN** the corresponding entries are removed from both `_sync_guid_to_xs_media` and `_xs_uuid_to_sync_guid`

---

### Requirement: Duplicate media detection and removal

After `load_otio()` creates media items in a playlist, the plugin SHALL detect duplicate media items that resolve to the same sync GUID. It SHALL keep the item actively referenced by the loaded timeline's clips and remove the unreferenced duplicate from the playlist bin.

#### Scenario: Duplicate detected — timeline-referenced item kept
- **WHEN** two media items in the bin resolve to the same sync GUID after bootstrap
- **THEN** the plugin identifies which item is referenced by the timeline's clips, removes the other item from the bin, and registers only the keeper in the mapping

#### Scenario: Timeline reference cannot be determined
- **WHEN** two items share a sync GUID but the timeline clip reference cannot be resolved
- **THEN** both items are retained, a warning is logged, and no removal occurs (safe degraded behaviour)
