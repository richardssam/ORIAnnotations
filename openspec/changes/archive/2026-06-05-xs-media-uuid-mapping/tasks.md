## 1. Mapping infrastructure

- [x] 1.1 Add `_sync_guid_to_xs_media: dict` and `_xs_uuid_to_sync_guid: dict` to `__init__`
- [x] 1.2 Clear both dicts in `_reset_session_state()` (called on disconnect)
- [x] 1.3 Add `_register_media(media_obj, sync_guid)` helper that writes both dicts atomically
- [x] 1.4 Add `_evict_media(sync_guid)` helper that removes from both dicts
- [x] 1.5 Add `_media_for_sync_guid(sync_guid)` lookup replacing `_find_media_for_clip_guid`
- [x] 1.6 Add `_sync_guid_for_xs_uuid(xs_uuid_str)` lookup replacing `_clip_guid_for_media_name`

## 2. Bootstrap

- [x] 2.1 Add `_bootstrap_media_mapping(playlist, otio_tl)` that normalises target_url paths and builds the initial mapping after `load_otio()` / `add_media()` completes
- [x] 2.2 Use `_uri_to_posix_path` + `os.path.normpath` for path comparison; basename-stem as fallback
- [x] 2.3 Log a warning for any unmatched media item (do not raise)
- [x] 2.4 Call `_bootstrap_media_mapping` from both the sequence OTIO path and the flat-playlist path in `_do_load_timelines`
- [x] 2.5 Call `_bootstrap_media_mapping` from `_build_otio_timelines` (master path) after the mapping between xs_tl clips and media is established

## 3. Duplicate removal after load_otio

- [x] 3.1 After `_bootstrap_media_mapping`, scan for sync GUIDs mapped to more than one xs_uuid
- [x] 3.2 For each duplicate pair, identify which media item is referenced by the loaded timeline's clips
- [x] 3.3 Remove the unreferenced duplicate from `playlist.media`; log a warning if reference cannot be determined and retain both
- [x] 3.4 Remove the `add_media()` pre-population block (lines ~1053–1069 in `_do_load_timelines`) that causes duplicates

## 4. Dynamic maintenance

- [x] 4.1 In `_apply_remote_clip_insert`: call `_register_media` after the media item is created in xStudio
- [x] 4.2 In `_apply_flat_playlist_insert`: call `_register_media` after the media item is created
- [x] 4.3 In `_poll_sequence_new_media`: call `_register_media` for each newly detected item before broadcasting INSERT_CHILD
- [x] 4.4 In `_poll_flat_playlist_new_media`: call `_register_media` for each newly detected item before broadcasting INSERT_CHILD
- [x] 4.5 In `_apply_remote_remove_child`: call `_evict_media` for the removed clip's sync GUID
- [x] 4.6 In `_poll_sequence_track_deletions` (or equivalent local removal): call `_evict_media`

## 5. Replace name-based lookups

- [x] 5.1 Replace `{m.name: m for m in playlist.media}` scan in `_load_snapshot_annotations` with `_media_for_sync_guid` lookup
- [x] 5.2 Replace `_clip_guid_for_media_name` usages (selection event handlers, viewport show_atom) with `_sync_guid_for_xs_uuid`
- [x] 5.3 Replace `_find_media_for_clip_guid` usages (annotation placement, bookmark creation) with `_media_for_sync_guid`
- [x] 5.4 Replace `name_to_media` scans in `_apply_remote_move_child` / `_apply_remote_remove_child` with mapping lookups
- [x] 5.5 Replace `_xs_flat_playlists` stored name lists (currently `[m.name for m in media_list]`) with sync-GUID lists where order tracking is needed

## 6. Flat-playlist / sequence split (xStudio master side)

- [x] 6.1 In `_build_otio_timelines`, after exporting the sequence OTIO for a playlist-with-timeline, also call `_build_otio_from_playlist_media(playlist)` to build the flat-playlist OTIO
- [x] 6.2 Set `xs_sequence_guid = str(xs_tl.uuid)` in the flat-playlist OTIO metadata before appending to result
- [x] 6.3 Ensure the flat-playlist OTIO is registered/managed correctly alongside the sequence OTIO (separate `_xs_flat_playlists` entry)

## 7. Flat-playlist receive (xStudio client side)

- [x] 7.1 In `_do_load_timelines`, when processing a flat-playlist OTIO with `xs_sequence_guid` present, look up the existing playlist entry via `xs_sequence_guid` in `_sync_playlists`
- [x] 7.2 Reorder that playlist's bin to match the flat-OTIO clip order using xStudio's reorder API (or remove+re-add sequence if no direct API)
- [x] 7.3 Do NOT create a new playlist for a flat-playlist OTIO that matches a known `xs_sequence_guid`
- [x] 7.4 If `xs_sequence_guid` is not found in `_sync_playlists`, fall through to existing flat-playlist creation path

## 8. RV: skip flat-playlist OTIOs

- [x] 8.1 In `_rebuild_rv_from_otio_snapshot`, add a guard at the top of the timeline loop: skip any `tl` where `tl.metadata.get("xs_flat_playlist")` is truthy

## 9. Verification

- [x] 9.1 xStudio joining RV-mastered session: `playlist.media` has exactly N items (no duplicates), in sequence edit order
- [x] 9.2 xStudio joining xStudio-mastered session: sequence edit order correct; bin order matches master's bin order
- [x] 9.3 RV joining session: no change in behaviour; flat-playlist OTIOs in snapshot are silently ignored
- [x] 9.4 Annotation placement still works after name-based lookups are removed (uses `_media_for_sync_guid`)
- [x] 9.5 Selection sync still works after `_clip_guid_for_media_name` is replaced
- [x] 9.6 Live media add/remove during session: mapping stays in sync (register/evict)

## 10. Bug Fixes (Reorder Sync & Clip Replication)

- [x] 10.1 Remove `is_master` check from `_poll_sequence_reorders` and `_poll_flat_playlist_reorders` to allow client-side reorders to sync.
- [x] 10.2 Update `_xs_media_order` on master-side timeline registration in `_build_otio_timelines` and add fallback initialization in `_poll_sequence_reorders`.
- [x] 10.3 Implement `_prepare_otio_for_load` to rewrite clip target URLs with matched existing media URIs from the playlist.
- [x] 10.4 Use `_prepare_otio_for_load` before calling `xs_timeline.load_otio` in remote events (clip insert, move, remove).
- [x] 10.5 In `_apply_flat_playlist_move`, update the stored order in `self._xs_flat_playlists` after the move is applied.
