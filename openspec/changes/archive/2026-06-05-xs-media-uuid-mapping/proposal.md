## Why

xStudio has two distinct concepts per edit unit — a **Playlist** (flat media bin, ordered bag of clips) and a **Sequence** (timeline with edit order) — but the sync protocol currently only represents one of them. When xStudio joins an RV-mastered session, it receives a sequence OTIO, creates a playlist, calls `add_media()` to pre-populate the bin, then calls `load_otio()` on the timeline — both creating media items. Because `add_media` names items by full path (`/full/path/sparks.mov`) while `load_otio` names them by clip name (`sparks.mov`), the same file appears twice and the bin shows duplicates with wrong ordering.

More broadly, all media identity in the plugin routes through display names (`{m.name: m for m in playlist.media}`), which breaks on renames, ambiguous filenames, and multi-session scenarios where two xStudio instances have different internal UUIDs for the same logical clip.

## What Changes

### Playlist / sequence management

- Remove the `add_media()` pre-population block in `_do_load_timelines` that causes duplicate media items; let `load_otio()` own media creation.
- When xStudio receives a sequence OTIO from any master (RV or xStudio), the resulting xStudio playlist bin order is set to match the sequence order — bin mirrors edit on initial join.
- When xStudio is master and has a Playlist-with-Timeline, emit **two** OTIO timelines in the snapshot: the sequence timeline (existing) and a flat-playlist timeline (`xs_flat_playlist: true`, linked via `xs_sequence_guid`) representing the bin order.
- When xStudio receives a flat-playlist OTIO with `xs_sequence_guid` metadata, update the **existing** playlist's bin order rather than creating a new playlist.
- RV ignores `xs_flat_playlist` timelines (no RVSequenceGroup created for bin). RV has no bin concept.
- Initial naming on creation: sequence node keeps its name; a corresponding bin representation is labelled `<name> Playlist` so artists can distinguish them. Identity tracked by GUID thereafter.

### Media identity (UUID mapping)

- Add two session-local dicts: `_sync_guid_to_xs_media` and `_xs_uuid_to_sync_guid`, cleared on disconnect.
- **Bootstrap** at connect time: after `load_otio()` creates media items, scan `playlist.media` and match each item to its OTIO clip via normalised path comparison (`_uri_to_posix_path` / `os.path.normpath`), falling back to filename stem. This produces the initial `xs_uuid → sync_guid` entries.
- **Dynamic maintenance** — the mapping is live for the session duration, not static:
  - Remote `INSERT_CHILD` / `_apply_remote_clip_insert`: register the new media item as soon as xStudio creates it.
  - Local add detected by `_poll_sequence_new_media` / `_poll_flat_playlist_new_media`: register and broadcast.
  - `REMOVE_CHILD` / local deletion: evict both entries from both dicts.
- **Path / URI normalisation**: bootstrap matching uses a shared helper that strips `file://` schemes and normalises platform separators before comparing, consistent with existing `_uri_to_posix_path` usage.
- **Deduplication after `load_otio()`**: when two media items map to the same sync GUID, identify which item is actively referenced by the loaded timeline's clips; keep that item and remove the unreferenced duplicate from the bin. Removing the wrong item would leave timeline clips showing "media offline."
- Replace all name-scan patterns (`{m.name: m}`, `_clip_guid_for_media_name`, `_find_media_for_clip_guid`) with direct mapping lookups.

## Capabilities

### New Capabilities

- `xs-media-uuid-mapping`: Session-local bidirectional mapping between xStudio media item UUIDs and OTIO sync GUIDs, with bootstrap logic and duplicate detection.
- `xs-playlist-sequence-split`: Two-OTIO representation of xStudio playlist+sequence pairs; correct bin-order reconstruction on receive; RV ignores bin OTIOs.

### Modified Capabilities

<!-- No existing spec-level requirements change; this is a refactor of internal identity tracking and playlist handling. -->

## Impact

- `xstudio_plugin/ori_sync/ori_sync_plugin.py`: primary change site — mapping dicts, bootstrap method, `_do_load_timelines`, `_build_otio_timelines`, updated lookups throughout.
- `rvplugin/openrv_sync_plugin/plugin.py`: one guard to skip `xs_flat_playlist` timelines in `_rebuild_rv_from_otio_snapshot`.
- No protocol schema changes — sync GUIDs and OTIO format are unchanged; `xs_flat_playlist` and `xs_sequence_guid` metadata keys already exist.
- xStudio sessions joining any master will no longer show duplicate media items or wrong bin order.
- All in-session annotation, selection, and reorder operations become name-independent.
