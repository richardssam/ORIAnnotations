## 1. Extract utils

- [x] 1.1 Create `xstudio_plugin/ori_sync/utils.py` and move stateless helpers into it: `_make_logger`, `_log`, `_log_exc`, `_uri_to_posix_path`, `_parse_ori_session`, and module-level logging constants.
- [x] 1.2 Replace those definitions in `ori_sync_plugin.py` with relative imports (`from .utils import _log, _log_exc, ...`).
- [ ] 1.3 Smoke-test that `import xstudio_plugin.ori_sync` succeeds and `create_plugin_instance`/`ORISyncPlugin` still resolve from the package.

## 2. Extract MediaMapController (instantiated first)

- [x] 2.1 Create `media_map.py` with `MediaMapController(plugin)` storing `self.plugin`; move mapping state `_sync_guid_to_xs_media`, `_xs_uuid_to_sync_guid`, `_flat_clip_to_media` onto it.
- [x] 2.2 Move methods `_register_media`, `_evict_media`, `_media_for_sync_guid`, `_bootstrap_media_mapping`, `_sync_guid_for_xs_uuid`, `_clip_guid_for_media_name`, `_find_media_for_clip_guid` onto the controller.
- [x] 2.3 Instantiate `self.media = MediaMapController(self)` first in `ORISyncPlugin.__init__`; rewrite all call-sites to `self.media.<method>()` / `self.plugin.media.<method>()`.
- [x] 2.4 Grep for the moved attribute/method names to confirm zero stale `self.<name>` references remain on the plugin class.

## 3. Extract TimelineBuildController

- [x] 3.1 Create `timeline_build.py` with `TimelineBuildController(plugin)`; move `_build_otio_timelines`, `_build_otio_from_viewed_container`, `_build_otio_from_playlist_media`, `_build_single_sequence_otio`, `_do_load_timelines`, `_prepare_otio_for_load`, `_fill_source_ranges`.
- [x] 3.2 Instantiate `self.builder` in `__init__`; rewrite call-sites (including the `load_timelines` command and `state_request`/`state_request_timeout` dispatch paths) to `self.builder.<method>()`.
- [x] 3.3 Confirm `self.plugin._sync_playlists` remains the shared registry (not moved) and the builder reads/writes it via the back-reference.

## 4. Extract DisplaySyncController

- [x] 4.1 Create `display_sync.py` with `DisplaySyncController(plugin)`; move display state `_xs_base_scale`, `_viewport`, `_last_display_state`, `_last_pinned_source_mode`, `_last_display_scan` and methods `_get_viewport`, `_read_xs_display_state`, `_apply_display_state`, `_poll_and_broadcast_display`.
- [x] 4.2 Instantiate `self.display` in `__init__`; rewrite the `display_settings` dispatch in `_handle_manager_event` and the poll-loop display call to the controller.
- [x] 4.3 Verify `_applying_pinned_mode` stays on the plugin (cross-thread guard) and is accessed via `self.plugin._applying_pinned_mode`.

## 5. Extract PlaybackSyncController

- [x] 5.1 Create `playback_sync.py` with `PlaybackSyncController(plugin)`; move playback/selection domain state (`_pending_seek_*`, `_current_selection_*`, `_last_viewed_clip_guid`, `active_playhead` if domain-local, scan timestamps) and methods: `_on_global_playhead_event`, `_subscribe_container_selection`, `_on_selection_event`, `_enqueue_selection_update`, `_check_and_update_active_playhead`, `_on_position_event`, `_subscribe_timeline_item_events` (if playback-scoped), `_apply_pending_seek`, `_resolve_and_broadcast_selection`, `_current_playback_state`, `_get_viewed_container_safe`, `_get_local_viewed_timeline_guid`, `_apply_playback_state`, `_playhead_for_clip`, `_apply_selection`, `_resolve_clip_at_frame`.
- [x] 5.2 Keep the `_on_*` handlers as thin shims on `ORISyncPlugin` that delegate/enqueue (preserve xStudio-thread invariant); move only the heavy logic into the controller.
- [x] 5.3 Instantiate `self.playback` in `__init__`; rewrite the `selection_changed`, `broadcast_playback_state`, `broadcast_selection`, `resolve_selection` dispatch paths.
- [x] 5.4 Confirm frame/play echo-guards (`_last_polled_frame`, `_last_applied_frame`, `_last_polled_playing`) and `_selection_broadcast_suppress_until` stay on the plugin and are accessed via `self.plugin.*`.

## 6. Extract StructureSyncController

- [x] 6.1 Create `structure_sync.py` with `StructureSyncController(plugin)`; move structural state `_xs_flat_playlists`, `_xs_sequence_playlists`, `_xs_sequence_media_names`, `_xs_sequence_track_names`, `_xs_media_order`, `_timeline_item_sub_ids`, `_timeline_item_dirty`/`_timeline_item_lock`, `_test_container_sub_id`, `_pending_create_check`, `_pending_snapshot_requesters`, structural scan timestamps.
- [x] 6.2 Move methods: `_poll_flat_playlist_reorders`, `_poll_sequence_reorders`, `_update_xs_media_order`, `_poll_flat_playlist_new_media`, `_poll_new_playlists`, `_poll_playlist_renames`, `_poll_sequence_new_media`, `_poll_sequence_track_deletions`, `_apply_remote_clip_insert`, `_apply_flat_playlist_move`, `_apply_flat_playlist_insert`, `_apply_sequence_insert`, `_apply_remote_remove_child`, `_apply_remote_move_child`, plus the `_on_test_container_event`/`_on_timeline_item_event` heavy logic.
- [x] 6.3 Instantiate `self.structure` in `__init__`; rewrite `_execute_sync_container`, the `move_child`/`remove_child`/`insert_child`/`add_timeline`/`timeline_renamed` dispatch, and poll-loop structural calls.
- [x] 6.4 Confirm `_structural_mutation_suppress_until` and `_reload_suppress_until` stay on the plugin; structure controller reads them via `self.plugin.*`.

## 7. Extract AnnotationSyncController

- [x] 7.1 Create `annotation_sync.py` with `AnnotationSyncController(plugin)`; move annotation state (`_annotation_bookmarks`, `_bookmark_strokes_cache`, `_bookmark_captions_cache`, `_our_bookmark_uuids`/lock, `_our_annotation_clip_guids`, `_our_bookmark_clip_frame`, `_last_sent_captions`, `_annotation_pending_time`, `_last_annotation_scan`, `_annotation_flush_retries`, `_core_events_received`, `_stroke_uuid_cache`, `_live_stroke_current_key`, all `_hot_scan_*`).
- [x] 7.2 Move methods: `_on_annotation_event`, `_on_core_annotation_event`, `_hot_scan_active_annotation`, `_broadcast_live_stroke_from_json`, `_flush_pending_annotations`, `_broadcast_local_bookmark`, `_caption_signature`, `_extract_caption_uuids`, `_load_snapshot_annotations`, `_refresh_annotation_bookmark`, `_apply_partial_annotation_xs`, `_apply_remote_annotation`.
- [x] 7.3 Keep `_on_annotation_event`/`_on_core_annotation_event` registration thin on the plugin where required by xStudio subscriptions; delegate to the controller.
- [x] 7.4 Instantiate `self.annotation` in `__init__`; rewrite the `partial_annotation`, `insert_child` (annotation path), `annotation_commands_added`, `annotation_commands_replaced` dispatch and the `hot_scan`/`live_stroke`/`clear_live_stroke` commands.

## 8. Slim the entry-point

- [x] 8.1 Reduce `ori_sync_plugin.py` to: `ORISyncPlugin` lifecycle (`__init__`, `connect_to_session`, `disconnect`, `cleanup`), menus, `_poll_loop`, `_drain_cmd_queue`, `_execute_command`, `_execute_sync_container`, `_handle_manager_event`, `_on_synced`, thin `_on_*` handlers, and `create_plugin_instance`.
- [x] 8.2 Verify the import DAG is acyclic (`utils ← controllers ← entry-point`) and all intra-package imports are relative.

## 9. Verify

- [ ] 9.1 Run the `sync_test/` two-client integration suite; confirm parity with pre-split behaviour (playback, selection, display, structure, annotation).
- [ ] 9.2 Interactive smoke test: load the plugin in xStudio, join a session, confirm the plugin loads and connects without import/attribute errors.
- [x] 9.3 Run `openspec validate xstudio-plugin-refactor --strict` and confirm every spec scenario is satisfied by the implementation.
