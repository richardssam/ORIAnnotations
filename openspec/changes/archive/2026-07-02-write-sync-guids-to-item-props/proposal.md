## Why

`poll_sequence_reorders` (and related polling functions) call `xs_tl.to_otio_string()` to inspect the current clip order in xStudio, then try to match each exported clip back to an ORI sync GUID using URL/path comparison. xStudio exports `MissingReference` for its internal `xstudio://` media URIs, so `clip.media_reference.target_url` is always empty — the match always fails and `current_order` is always empty. Reorder detection (and source-range polling) is silently broken on the xStudio client.

The fix is to persist ORI sync GUIDs into xStudio's `item.prop()` immediately after `timeline_build.py` assigns them. `item.prop()` is the OTIO metadata dict: whatever is in it at export time appears verbatim in `to_otio_string()` output. Once sync GUIDs are in `item.prop()`, the exported OTIO carries them and the URL-matching loop can be replaced with a direct `clip.metadata["sync"]["guid"]` lookup.

## What Changes

- After `timeline_build.py` assigns a sync GUID to an OTIO clip, write `{"sync": {"guid": <guid>}}` into the corresponding xStudio timeline item's `item_prop` (tracks and clips; timelines already have an actor-level GUID).
- In `structure_sync.poll_sequence_reorders`, replace the URL/stem-matching loop with a direct read of `clip.metadata.get("sync", {}).get("guid")` from the `to_otio_string()` output.
- Apply the same substitution to any other polling function that currently does URL-based clip identity resolution against `to_otio_string()` output (`poll_sequence_track_deletions`, `poll_sequence_source_ranges`).
- Remove the now-dead URL-matching helper code from the affected functions.

## Capabilities

### New Capabilities
- `xs-item-prop-sync-guid`: The plugin SHALL write ORI sync GUIDs into xStudio timeline item props (`item_prop`) so that native `to_otio_string()` exports carry them, enabling stable clip identity without URL resolution.

### Modified Capabilities
- `xstudio-event-sync`: The sequence reorder and source-range polling requirements change — clip identity resolution SHALL use `clip.metadata["sync"]["guid"]` from the exported OTIO rather than URL/path matching against `clip.media_reference`.

## Impact

- `xstudio_plugin/ori_sync/timeline_build.py` — add `item_prop` writes after GUID assignment for tracks and clips
- `xstudio_plugin/ori_sync/structure_sync.py` — replace URL-matching loops in `poll_sequence_reorders`, `poll_sequence_track_deletions`, `poll_sequence_source_ranges`
- No protocol changes; no RV plugin changes; no xStudio C++ changes required
- The `pr/otio-export-uuid` branch (commit `8bcd39d3`) is no longer needed as a prerequisite for this fix
