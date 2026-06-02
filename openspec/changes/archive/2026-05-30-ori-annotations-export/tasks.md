## 1. Plugin Scaffold

- [x] 1.1 Create `xstudio_plugin/ori_annotations/` directory with `__init__.py` exporting `create_plugin_instance`
- [x] 1.2 Create `ori_annotations.py` with a `PluginBase` subclass skeleton, `create_plugin_instance` factory, and the "Export Annotations (OTIO)..." menu entry registered under `File|Export`

## 2. Export Dialog

- [x] 2.1 Implement `QAnnotationFileDialog` (non-native `QFileDialog` subclass) with: directory picker, OTIO filename `QLineEdit`, "Include media files" `QCheckBox`, "Include annotation images" `QCheckBox`
- [x] 2.2 Wire the dialog to the menu callback: open dialog, read selections, call `do_export()`

## 3. Stroke Conversion

- [x] 3.1 Implement `_strokes_to_sync_events(pen_strokes)` — converts xstudio `pen_stroke` dicts to `(PaintStart, PaintPoints)` event pairs, computing per-point widths from `thickness * size_pressure` (or constant `thickness` when pressures are zero), setting `type='erase'` for erase strokes
- [x] 3.2 Implement `_captions_to_sync_events(captions)` — converts xstudio `caption` dicts to `TextAnnotation` events with correct position, color, font, font_size, scale=1.0, rotation=0.0

## 4. Bookmark Collection

- [x] 4.1 Implement `_collect_bookmarks(playlist)` — iterates `playlist.media`, calls `media.ordered_bookmarks()`, computes frame number via `round(bookmark.start.total_seconds() / media.media_source().rate.seconds())`, fetches annotation JSON via `request_receive(bookmark.remote, serialise_atom())`
- [x] 4.2 Skip bookmarks with empty annotation data (no pen_strokes and no captions) — still create the ReviewItemFrame with just the note text if the note is non-empty

## 5. ORIAnnotations Model Assembly

- [x] 5.1 Build `Media` objects from xstudio media items: `media_path` from `media_source().media_reference.target_url`, `frame_rate` from `media_source().rate.fps()`, `start_frame` from media reference timecode, `duration` from frame list
- [x] 5.2 Assemble `ReviewItemFrame` objects with `frame`, `note` (from `bookmark.note`), `annotation_commands` (from stroke/caption conversion)
- [x] 5.3 Build `ReviewItem`, `Review`, `ReviewGroup` and call `reviewgroup.export_otio_timeline(as_nested_stacks=True)`

## 6. Optional Image Export

- [x] 6.1 When "Include annotation images" is checked, call `self.connection.api.app.snapshot_viewport.render_bookmark_with_transparency(path, bookmark.uuid, include_image=False, include_drawings=True)` for each bookmark and set the resulting path on `ReviewItemFrame.annotation_image`
- [x] 6.2 When "Include media files" is checked, `shutil.copy()` each source media file into the output directory and rewrite the `Media.media_path` to the basename

## 7. OTIO Output and Feedback

- [x] 7.1 Call `otio.adapters.write_to_file(timeline, output_path)` to write the OTIO file
- [x] 7.2 Show a result message via `self.popup_message_box()` reporting the output path and number of annotated frames exported; show an error message on any exception

## 8. Environment and Documentation

- [x] 8.1 Add a `README.md` under `xstudio_plugin/` documenting the required env vars: `XSTUDIO_PYTHON_PLUGIN_PATH`, `OTIO_PLUGIN_MANIFEST_PATH`, `PYTHONPATH` (for the ORIAnnotations Python module)
- [x] 8.2 Verify the plugin loads and the menu item appears in a running xstudio instance
- [x] 8.3 Run a full export from a test session with bookmarks and verify the `.otio` can be imported by the existing RV plugin
