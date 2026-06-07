## 1. Create utils.py

- [x] 1.1 Create `rvplugin/ori_sync/utils.py` with logger setup (`_make_otio_logger`, `_otio_logger`, `_log`, `_log_exc`, `_install_excepthook`), warning popups (`_show_warning`, `_show_warning_main`), session parsing (`_parse_ori_session`), path normalisation (`_media_path`), and track classification (`_is_media_track`). Import `rv.commands` and `otio` as needed.
- [x] 1.2 Remove the moved functions and `@staticmethod` methods from `plugin.py` and replace with imports from `utils`.

## 2. Create SequenceSyncController

- [x] 2.1 Create `rvplugin/ori_sync/sequence_sync.py` with `SequenceSyncController` class. Move state: `_rv_node_to_timeline_guid`, `_sequence_input_order`, `_sg_to_path_cache`, `_sequence_settle_until`, `_active_media_track_guid`, `_track`.
- [x] 2.2 Move methods: `_init_timelines_from_sequences`, `_init_single_timeline`, `_retry_init_timelines`, `_make_otio_clip_for_sg`, `_make_clip`, `_edl_frame_counts`, `_source_groups_for_sequences`, `_get_sequence_inputs`, `_path_to_source_group_map`, `_check_sequence_reorders`, `_poll_new_sequences`, `_poll_sequence_renames`, `_create_rv_sequence_for_timeline`, `_apply_insert_child`, `_apply_remove_child`, `_apply_move_child`, `_rebuild_rv_session`.
- [x] 2.3 Update all `self._media_path(...)` and `self._is_media_track(...)` calls to use imported `utils` functions.
- [x] 2.4 Update all `self.sync_manager` references to `self.plugin.sync_manager` and `self._rv_updating` to `self.plugin._rv_updating`.

## 3. Create PlaybackSyncController

- [x] 3.1 Create `rvplugin/ori_sync/playback_sync.py` with `PlaybackSyncController` class. Move state: `_last_broadcast_frame`, `_last_selection`, `_last_broadcast_clip_guid`, `_sequence_selection_applied_at`.
- [x] 3.2 Move methods: `_broadcast_playback`, `_apply_playback`, `_apply_selection`, `_clip_guid_for_media_path`.
- [x] 3.3 Update shared state references to use `self.plugin.*`.

## 4. Create DisplaySyncController

- [x] 4.1 Create `rvplugin/ori_sync/display_sync.py` with `DisplaySyncController` class. Move state: `_last_display_state`. Move constants: `_RV_FLOOD_TO_CH`, `_RV_CH_TO_FLOOD`.
- [x] 4.2 Move methods: `_rv_display_color_nodes`, `_rv_display_color_node`, `_rv_color_node_for_current_source`, `_read_rv_display_state`, `_broadcast_display_state`, `_apply_display_state`.
- [x] 4.3 Update shared state references to use `self.plugin.*`.

## 5. Create AnnotationSyncController

- [x] 5.1 Create `rvplugin/ori_sync/annotation_sync.py` with `AnnotationSyncController` class. Move state: `_pending_stroke`, `_next_stroke_uuid`, `_stroke_timer`, `_last_partial_point_count`, `_partial_pen_nodes`, `_last_sent_replace_sig`, `_ignore_annotations_until`.
- [x] 5.2 Move methods: `_import_existing_rv_annotations`, `_resolve_media_path_for_paint_node`, `_apply_partial_annotation`, `_apply_annotation_render`, `_apply_annotation_replace`, `_find_paint_node_for_media`, `_apply_annotation`, `_text_uuid_exists_in_rv`, `_apply_text_annotation`, `_construct_annotation_events`, `_broadcast_frame_annotations_replace`, `_broadcast_annotation`, `_send_partial_stroke`, `_flush_pending_stroke`, `_stop_stroke_timers`, `_on_pen_up`.
- [x] 5.3 Update shared state references to use `self.plugin.*` and cross-controller calls (e.g. `self.plugin.sequence.path_to_source_group_map()`, `self.plugin.playback.clip_guid_for_media_path()`).

## 6. Refactor plugin.py

- [x] 6.1 Add imports for all 4 controller classes and `utils`.
- [x] 6.2 Instantiate controllers in `__init__`: `self.sequence`, `self.playback`, `self.display`, `self.annotation`.
- [x] 6.3 Update `_handle_action` to dispatch to controller methods.
- [x] 6.4 Update event handler methods (`on_rv_play_start`, `on_rv_play_stop`, `on_rv_frame_changed`, `on_rv_selection_changed`, `on_rv_graph_state_change`, `on_rv_view_changed`, `on_rv_pen_up`) to delegate to controllers.
- [x] 6.5 Update `poll_network` to call controller methods for structural polling and display broadcast.
- [x] 6.6 Update `connect_to_session` / `disconnect_from_session` / `_init_as_master` to call controller methods where needed (e.g. `self.sequence._init_timelines_from_sequences`).
- [x] 6.7 Update `_rebuild_rv_session` call in `on_synced` callback to `self.sequence.rebuild_rv_session()`.
- [x] 6.8 Keep `_session_dialog`, `do_create_session`, `do_join_session`, `do_leave_session`, `do_add_clip`, `do_show_status`, `_build_menu`, `_rebuild_menu`, `deactivate`, `createMode` in `plugin.py`.

## 7. Update packaging

- [x] 7.1 Update `makepackage.csh` zip command to include `utils.py`, `sequence_sync.py`, `playback_sync.py`, `display_sync.py`, `annotation_sync.py`.

## 8. Fix tests

- [x] 8.1 Update `tests/otio_sync/test_openrv_annotations.py`: remove `patch.object(OpenRVSyncPlugin, '_setup_sync')` context managers from all test methods.
- [x] 8.2 Mock `os.environ.get` for `ORI_SESSION` to suppress auto-connect during test instantiation.
- [x] 8.3 Update any import paths or method references that moved to controller classes.
- [x] 8.4 Run `pytest tests/otio_sync/test_openrv_annotations.py` and verify all 4 tests pass.

## 9. Verification

- [x] 9.1 Verify no circular imports: `python -c "import plugin"` from the `rvplugin/ori_sync/` directory succeeds.
- [x] 9.2 Verify `makepackage.csh` produces a valid `.rvpkg` containing all 6 Python modules.
- [x] 9.3 Verify `plugin.py` is under ~400 lines (excluding compatibility descriptors), `utils.py` under ~150 lines, and no controller exceeds ~1500 lines.
