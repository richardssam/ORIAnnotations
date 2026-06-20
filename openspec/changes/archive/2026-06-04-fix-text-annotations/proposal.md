## Why

Text annotations (captions) are currently failing to sync robustly between xStudio and OpenRV due to three distinct bugs in parsing and event handling:
1. **Font Size Shrinkage**: An asymmetric scaling factor between xStudio and OpenRV causes text to shrink by a factor of 3 upon every round trip.
2. **Duplicate Text Nodes**: xStudio's parser fails to preserve text UUIDs, causing remote edits to append duplicate text nodes instead of updating existing ones.
3. **Dropped New Annotations**: When OpenRV receives a brand new text annotation from xStudio, a local variable reference error (`UnboundLocalError`) causes the plugin to crash and drop the annotation entirely.

This change fixes these bugs to ensure text annotations can be created, edited, and round-tripped cleanly.

## What Changes

- Update `rvplugin/openrv_sync_plugin/plugin.py` to use `5000.0` as the font size scaling factor for both import and export (currently it incorrectly uses `15000.0` on import).
- Update `python/otio_sync_core/xs_annotation_codec.py` to correctly map the `uuid` field from OTIO `TextAnnotation` SyncEvents into the resulting xStudio caption dictionaries.
- Update `_apply_annotation_render` in `rvplugin/openrv_sync_plugin/plugin.py` to define `text_val` securely outside of the UUID-matching conditional block, preventing crashes on new annotations.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `otio-annotation-sync`: Clarifying the coordinate mapping and scaling constraints for `TextAnnotation` to explicitly call out font size conversion symmetry requirements.

## Impact

- `rvplugin/openrv_sync_plugin/plugin.py`
- `python/otio_sync_core/xs_annotation_codec.py`
- OpenRV and xStudio sync behaviors are preserved and no network payload format changes are required.
