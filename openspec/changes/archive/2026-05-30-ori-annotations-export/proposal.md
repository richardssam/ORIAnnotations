## Why

xstudio has no way to export annotations for review in RV. The ORIAnnotations ecosystem already defines the data model and OTIO format, and the RV import plugin already exists — what's missing is the xstudio side of the bridge.

## What Changes

- New xstudio Python plugin (`xstudio_plugin/ori_annotations/`) that adds an "Export Annotations (OTIO)..." menu entry under File|Export
- The plugin reads bookmarks (annotations) from the current playlist, converts xstudio stroke/caption data to SyncEvent types, builds an ORIAnnotations ReviewGroup, and writes an `.otio` file
- Optionally renders annotation images alongside the OTIO using xstudio's offscreen viewport
- Installed via `XSTUDIO_PYTHON_PLUGIN_PATH` pointing at the new `xstudio_plugin/` directory — no changes to the xstudio repo required

## Capabilities

### New Capabilities

- `xstudio-annotation-export`: Export xstudio bookmarks/annotations as an ORIAnnotations-compatible OTIO file that can be imported by the existing RV plugin

### Modified Capabilities

- `otio-annotation-sync`: The shared OTIO format gains a documented xstudio ↔ SyncEvent stroke mapping (coordinate system, width/pressure normalization, erase mode)

## Impact

- New directory: `xstudio_plugin/ori_annotations/` in the ORIAnnotations repo
- Depends on: `xstudio.plugin.PluginBase`, `xstudio.core` atoms (`serialise_atom`, `bookmark_detail_atom`), `xstudio.api.intrinsic.viewport.OffscreenViewport`
- Depends on: ORIAnnotations Python module (`python/ORIAnnotations.py`), `otio_event_plugin` SyncEvent schemadefs
- No changes to xstudio C++ or existing Python plugin code
- Users must set `XSTUDIO_PYTHON_PLUGIN_PATH` and `OTIO_PLUGIN_MANIFEST_PATH` in their environment
