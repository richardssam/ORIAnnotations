---
layout: default
title: XStudio Plugins
parent: ORI Annotations
---
# ORIAnnotations xSTUDIO Plugin

Exports annotations (bookmarks) from xSTUDIO as an ORIAnnotations-compatible OTIO file that can be imported by the OpenRV plugin (`rvplugin/ori_annotations/ori_annotations_plugin.py`).

## Installation

No build step required. Point xSTUDIO at this directory via environment variables.

### Required environment variables

```bash
# Tell xSTUDIO where to find the plugin
export XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/ORIAnnotations/xstudio_plugin

# Make the ORIAnnotations Python module importable
export PYTHONPATH=/path/to/ORIAnnotations/python:$PYTHONPATH

# Register the SyncEvent OTIO schemadef
export OTIO_PLUGIN_MANIFEST_PATH=/path/to/ORIAnnotations/otio_event_plugin/plugin_manifest.json
```

> **Note:** The plugin automatically extends `OTIO_PLUGIN_MANIFEST_PATH` and `sys.path` at load time, so only `XSTUDIO_PYTHON_PLUGIN_PATH` is strictly required if the ORIAnnotations repo is on `PYTHONPATH`. Setting all three explicitly avoids any ordering issues.

### Debug logging environment variables (Optional)

To write detailed execution and debug logs to a file, set the following environment variables before launching xSTUDIO:

```bash
# Enable file logging for the Live Sync plugin
export ORI_SYNC_LOG_FILE=/path/to/sync_plugin.log

# Enable file logging for the manual Annotations Export plugin
export ORI_ANNOTATIONS_LOG_FILE=/path/to/annotations_plugin.log
```

### Example (bash, absolute paths)

```bash
export REPO=/Users/sam/git/ORIAnnotations
export XSTUDIO_PYTHON_PLUGIN_PATH=$REPO/xstudio_plugin
export PYTHONPATH=$REPO/python:$PYTHONPATH
export OTIO_PLUGIN_MANIFEST_PATH=$REPO/otio_event_plugin/plugin_manifest.json
```

## Usage

1. Open a session with annotated bookmarks in xSTUDIO.
2. Select the playlist you want to export in the Media panel (it becomes the *inspected container*).
3. Go to **File → Export → Export Annotations (OTIO)...**.
4. Pick an output directory and configure options:
   - **OTIO filename** — name of the `.otio` file written to the output directory.
   - **Copy media files** — copies source media into the output directory and uses relative paths in the OTIO. Useful for self-contained packages.
   - **Render annotation images** — renders each annotated frame as a transparent PNG (annotations only, no background image) alongside the OTIO.
5. Click **Export**.

The resulting `.otio` file can be imported into OpenRV using the **Tools → Import annotations** menu entry provided by `rvplugin/ori_annotations/ori_annotations_plugin.py`.

## What gets exported

- All bookmarks on every media item in the selected playlist.
- Pen strokes (including erase strokes) → `SyncEvent.PaintStart` + `SyncEvent.PaintPoints`.
- Text captions → `SyncEvent.TextAnnotation`.
- Bookmark note text → `ReviewItemFrame.note`.
- Bookmarks with no stroke data and no note text are skipped.

## Coordinate system

xSTUDIO and RV both use a normalized coordinate system with `(0, 0)` at the image centre and `±0.5` spanning half the image width. No coordinate transformation is applied during export.
