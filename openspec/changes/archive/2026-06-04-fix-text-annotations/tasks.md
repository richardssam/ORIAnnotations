## 1. OpenRV Plugin Fixes

- [x] 1.1 In `rvplugin/openrv_sync_plugin/plugin.py`, change the `15000.0` text size scaling factor on lines 1265, 1290, 1413, and 1646 to `5000.0` for symmetric mapping.
- [x] 1.2 In `rvplugin/openrv_sync_plugin/plugin.py` `_apply_annotation_render()`, move the initialization of `text_val = ev.text or ""` to just outside the `if _paint_node_cache` block so it is safely scoped for brand new incoming annotations.

## 2. xStudio Annotation Codec Fixes

- [x] 2.1 In `python/otio_sync_core/xs_annotation_codec.py` `sync_events_to_xs_captions()`, conditionally read `cmd.uuid` (or `cmd.get("uuid")` for dictionaries).
- [x] 2.2 In `python/otio_sync_core/xs_annotation_codec.py`, inject the retrieved UUID into the output `captions` dictionary before appending it to the list.

## 3. Verification

- [x] 3.1 Rebuild the OpenRV `.rvpkg` package using `rvplugin/openrv_sync_plugin/makepackage.csh` to bundle the updated `plugin.py` and `otio_sync_core` files.
- [x] 3.2 Ensure `test_sync_recorder.py` and `run_tests.sh` in the `sync_test` directory still execute properly with these fixes integrated.
