## Context

xstudio stores annotations as `Bookmark` objects managed by the `session.bookmarks` manager. Each bookmark carries:
- Timing: `start` (RationalTime) and `duration` relative to the owning media's frame rate
- Text metadata: `note`, `author`, `subject`, `category`
- Stroke/caption data: serialized JSON retrieved via `request_receive(bookmark.remote, serialise_atom())`

The serialized annotation JSON format (`"Annotation Serialiser Version": 256`) contains:
```json
{
  "Data": {
    "pen_strokes": [{ "r","g","b","opacity","thickness","is_erase_stroke","points":[x,y,sp,op,...] }],
    "captions":   [{ "position","text","font_name","font_size","colour","opacity","wrap_width" }]
  },
  "plugin_uuid": "46f386a0-cb9a-4820-8e99-fb53f6c019eb"
}
```

The ORIAnnotations ecosystem has an established OTIO format using `SyncEvent` schema defs (`PaintStart`, `PaintPoints`, `TextAnnotation`) and the `ORIAnnotations.py` data model. The RV import plugin already reads this format. The xstudio plugin just needs to produce it.

## Goals / Non-Goals

**Goals:**
- New xstudio Python plugin that exports a playlist's annotations as an ORIAnnotations-compatible `.otio` file
- Convert xstudio pen strokes and captions to `SyncEvent` types with correct coordinate and color mapping
- Optionally render annotation-only PNG images via `render_bookmark_with_transparency()`
- Zero changes to the xstudio codebase; installed via `XSTUDIO_PYTHON_PLUGIN_PATH`

**Non-Goals:**
- Importing OTIO annotations back into xstudio (future work)
- Custom QML dialog (use `PluginBase.popup_*` + standard Qt dialogs via PySide6)
- Support for xstudio shape types beyond pen strokes and captions (`quads`, `polygons`, `ellipses`)
- Integration with xstudio's built-in `AnnotationsExporter` plugin

## Decisions

### Plugin lives in the ORIAnnotations repo, not xstudio

The xstudio plugin is one side of the ORIAnnotations bridge. Keeping it alongside the RV plugin in `/xstudio_plugin/ori_annotations/` makes the package self-contained and distributable independently of the xstudio build system.

**Alternative**: Fork the xstudio repo and add alongside existing plugins. Rejected because it creates unnecessary coupling and requires a build/install step.

### No custom QML UI

The export dialog uses a standard PySide6 `QFileDialog` (non-native, with appended checkbox options) following the same pattern as the RV plugin's `QAnnotationFileDialog`. This avoids the QML registration and resource embedding complexity of `PluginBase`'s QML support.

**Alternative**: Full QML dialog like `AnnotationsExporter`. Rejected for initial version — adds significant boilerplate for no functional gain.

### Stroke data via `serialise_atom()`, not rendered images only

The existing `AnnotationsExporter` only renders bitmaps. This plugin reads the raw JSON from `serialise_atom()` to produce vector `SyncEvent` data that RV can apply natively. Bitmap images can optionally be exported alongside as `annotation_image` references in `ReviewItemFrame`.

### Coordinate system: xstudio uses image-relative normalized coords

xstudio stores stroke points as `(x, y)` in a normalized system where `(0, 0)` is the image center and `±0.5` spans half the image width. The y axis is up-positive. RV's annotation system uses the same convention (verified by the RV plugin's direct use of `points` without any transformation). No coordinate conversion is needed.

### Per-point width from thickness + size_pressure

xstudio encodes points as `[x, y, size_pressure, opacity_pressure, ...]`. The stroke's `thickness` is the base width, and `size_pressure` (0–1) modulates it per point. The `SyncEvent.PaintVertices.size` list is computed as:

```python
width_per_point = [thickness * sp for sp in size_pressures]
```

If all size pressures are 0 (non-pressure-sensitive stroke), use `[thickness] * n_points`.

### `serialise_atom` import

`serialise_atom` is available in `xstudio.core` (all atoms are exposed there via `from xstudio.core import *`). The plugin imports it explicitly: `from xstudio.core import serialise_atom`.

## Risks / Trade-offs

**`serialise_atom` may not be exported from `xstudio.core`** → Mitigation: Fall back to `get_annotation_atom` (read-only annotation fetch) if `serialise_atom` is unavailable; document the required xstudio version.

**Coordinate system assumption** → Mitigation: Export a test annotation, compare coordinates in the OTIO with what RV renders. Add a coordinate verification note to the plugin README.

**Bookmark iteration performance** → For large playlists with many bookmarks, `ordered_bookmarks()` makes one request per bookmark to fetch `detail`. Acceptable for now; can be batched later.

**xstudio `start` → frame number rounding** → `bookmark.start.total_seconds() / media.media_source().rate.seconds()` may produce non-integer results due to floating point. Always `round()` to nearest integer before using as frame index.

## Open Questions

- Does the `render_bookmark_with_transparency` call block until the image is written, or is it async? If async, we need to wait before the OTIO file is written.
- What is the `plugin_uuid` field in the annotation JSON used for on re-import? (Likely identifies the annotation plugin for re-hydration — not needed for export.)
