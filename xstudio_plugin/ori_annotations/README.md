---
layout: default
title: XStudio Annotation Plugin
parent: ORI Annotations
nav_order: 2.4
---
# ORIAnnotations xSTUDIO Plugin

Exports annotations (bookmarks) from xSTUDIO as an ORIAnnotations-compatible OTIO file that can be imported by the OpenRV plugin (`rvplugin/ori_annotations/ori_annotations_plugin.py`).

## Installation

No build step, no environment variables required, no packages to install into xStudio's interpreter. Point xStudio at the plugin folder.

The packaging script at the repo root (`./make_xstudio_package.sh --help` for the full list of modes) produces a self-contained tree containing this plugin, its sibling `ori_sync/`, and their supporting payload. Pick whichever install mode suits your site:

- **Suggested default** — install into the app bundle, which xStudio scans unconditionally: `./make_xstudio_package.sh --install-app /path/to/xSTUDIO.app` (re-run after rebuilding xStudio)
- **A persistent folder, independent of any one app bundle** — for a plugin location shared across multiple xStudio builds/versions: `./make_xstudio_package.sh --install-dir /path/to/a/persistent/folder`, then either `export XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/that/folder` or add the path via Preferences → Python → "Python Plugins Search Paths"
- **A versioned artifact to hand someone** — `./make_xstudio_package.sh --package` stages and zips into `dist/`

A repository checkout's `xstudio_plugin/` directory also works directly as a search folder while developing, with no packaging step:

```bash
export XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/ORIAnnotations/xstudio_plugin
```

See [`../ori_sync/README.md`](../ori_sync/README.md) for the full installation rationale — both plugins share the same folder and the same install routes.

### Debug logging environment variables (Optional)

Nothing is required for the plugin to function. To write detailed execution and debug logs to a file, set the following environment variables before launching xSTUDIO:

```bash
# Enable file logging for the Live Sync plugin
export ORI_SYNC_LOG_FILE=/path/to/sync_plugin.log

# Enable file logging for the manual Annotations Export plugin
export ORI_ANNOTATIONS_LOG_FILE=/path/to/annotations_plugin.log
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
