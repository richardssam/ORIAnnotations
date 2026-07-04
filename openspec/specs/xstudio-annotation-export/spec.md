## Purpose

Specifies the xStudio Python plugin that exports a playlist's annotations as an ORIAnnotations-compatible `.otio` file, and imports an ORIAnnotations `.otio` file back into xStudio bookmarks.

## Requirements

### Requirement: Plugin Installation

The plugin SHALL be loadable by xstudio when `XSTUDIO_PYTHON_PLUGIN_PATH` includes the `xstudio_plugin/` directory of the ORIAnnotations repository, with no modifications to the xstudio installation.

#### Scenario: Plugin discovered via env var

- **WHEN** xstudio starts with `XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/ORIAnnotations/xstudio_plugin`
- **THEN** the `ori_annotations` plugin SHALL be loaded and its menu entries SHALL appear in the xstudio UI

#### Scenario: Missing env var

- **WHEN** `XSTUDIO_PYTHON_PLUGIN_PATH` is not set
- **THEN** the plugin SHALL not load and xstudio SHALL start normally without error

### Requirement: Export Menu Entry

The plugin SHALL register an "Export Annotations (OTIO)..." menu item under the "File|Export" group in xstudio's main menu bar.

#### Scenario: Menu entry visible

- **WHEN** the plugin is loaded
- **THEN** "Export Annotations (OTIO)..." SHALL appear under File > Export in the menu bar

### Requirement: Export Directory Dialog

When the export menu item is triggered, the plugin SHALL present a directory picker dialog with options controlling the export.

#### Scenario: User picks directory and confirms

- **WHEN** the user selects a directory and clicks the confirm button
- **THEN** the export SHALL proceed using the selected directory as the output root

#### Scenario: User cancels dialog

- **WHEN** the user dismisses the dialog without confirming
- **THEN** no files SHALL be written and no error SHALL be shown

#### Scenario: Dialog options

- **WHEN** the dialog is shown
- **THEN** it SHALL offer: OTIO filename input, "Include media files" checkbox, "Include annotation images" checkbox

### Requirement: Playlist Annotation Export

The plugin SHALL export all annotated bookmarks from every media item in the current playlist.

#### Scenario: Export collects all bookmarks

- **WHEN** the export runs
- **THEN** it SHALL iterate all media in the current inspected playlist and collect all bookmarks with annotation data

#### Scenario: Media with no bookmarks

- **WHEN** a media item has no bookmarks
- **THEN** it SHALL be included in the OTIO media track but have no annotation clips in the review track

### Requirement: Pen Stroke Conversion

The plugin SHALL convert xstudio pen stroke data to `SyncEvent.PaintStart` + `SyncEvent.PaintPoints` event pairs.

#### Scenario: Normal pen stroke

- **WHEN** a bookmark's serialized data contains a `pen_stroke` with `is_erase_stroke: false`
- **THEN** a `PaintStart` event SHALL be created with `type='paint'`, RGBA from stroke `r,g,b,opacity`, and a `PaintPoints` event SHALL carry interleaved x/y coordinates and per-point widths computed as `thickness * size_pressure` (or `thickness` if size_pressure is zero)

#### Scenario: Erase stroke

- **WHEN** a `pen_stroke` has `is_erase_stroke: true`
- **THEN** the `PaintStart` event SHALL have `type='erase'`

### Requirement: Caption Conversion

The plugin SHALL convert xstudio captions to `SyncEvent.TextAnnotation` events. Because xStudio has no per-caption scale field, the emitted `scale` SHALL be `1.0`; on import, the `scale` field MAY be dropped since xStudio cannot represent it. Coordinate and font-size conversions SHALL use the shared helpers (`coords` for geometry, the xStudio codec for xStudio's font factor), not inline constants.

#### Scenario: Caption to TextAnnotation

- **WHEN** a bookmark's serialized data contains a `caption`
- **THEN** a `TextAnnotation` event SHALL be created with `text`, `position`, `font` (from `font_name`), `font_size`, `rgba` from caption colour+opacity, `scale=1.0`, `rotation=0.0`

#### Scenario: Scale round-trip on xStudio is lossless within its capability

- **WHEN** a `TextAnnotation` is imported into xStudio and later exported again
- **THEN** the re-exported `TextAnnotation.scale` SHALL be `1.0`
- **AND** no error SHALL result from xStudio lacking a native scale field

### Requirement: OTIO File Output

The plugin SHALL write a valid ORIAnnotations OTIO file to the selected directory.

#### Scenario: Successful export

- **WHEN** the export completes
- **THEN** an `.otio` file SHALL exist at `<output_dir>/<otio_name>` containing a media track (one clip per media item) and a review track (annotation clips with SyncEvent metadata)

#### Scenario: Export result notification

- **WHEN** the export completes successfully
- **THEN** the plugin SHALL display a message indicating the output path and number of annotated frames exported

### Requirement: Optional Annotation Image Export

When the user opts in, the plugin SHALL render each annotated bookmark frame to a PNG alongside the OTIO.

#### Scenario: Annotation images rendered

- **WHEN** "Include annotation images" is checked
- **THEN** for each bookmark the plugin SHALL call `render_bookmark_with_transparency()` with `include_image=False, include_drawings=True` and reference the output path in the corresponding `ReviewItemFrame.annotation_image`

#### Scenario: Annotation images skipped

- **WHEN** "Include annotation images" is unchecked
- **THEN** no image rendering SHALL occur and `ReviewItemFrame.annotation_image` SHALL be `None`

### Requirement: Optional Media Copy

When the user opts in, the plugin SHALL copy source media files into the output directory and reference them with relative paths in the OTIO.

#### Scenario: Media copied

- **WHEN** "Include media files" is checked
- **THEN** each media file SHALL be copied to `<output_dir>/` and the OTIO SHALL reference it by basename only

#### Scenario: Media not copied

- **WHEN** "Include media files" is unchecked
- **THEN** the OTIO SHALL reference media by absolute path
