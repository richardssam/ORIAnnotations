## Context

The xStudio plugin (`ori_sync_plugin.py`) currently identifies media items exclusively by display name. The main lookup pattern throughout is:

```python
name_to_media = {m.name: m for m in playlist.media}
media = name_to_media.get(clip.name)
```

This is fragile in several ways: `add_media(path)` stores the full path as the name while `load_otio()` stores just the filename, causing duplicates. Renames break lookups. Two xStudio sessions in the same sync have different internal UUIDs for the same clip.

xStudio objects expose `.uuid` (C++ actor UUID). These are ephemeral — they change on restart and differ between sessions — but are stable for the duration of one process. The sync GUID (`clip.metadata["sync"]["guid"]`) is the durable cross-app identity issued by the OTIO sync manager.

## Goals / Non-Goals

**Goals:**

- Bidirectional session-local mapping: `sync_guid ↔ xs_media_uuid ↔ media_obj`
- Bootstrap from existing session state at connect time using path/URI normalisation
- Dynamic maintenance: map updated on every add/remove event (local and remote)
- Correct deduplication after `load_otio()` — keep the timeline-referenced item
- Two-OTIO representation of xStudio playlist+sequence (bin order + edit order)
- xStudio client correctly receives and reconstructs bin order without creating a new playlist
- RV ignores flat-playlist OTIOs entirely

**Non-Goals:**

- Persisting the mapping across restarts (xs UUIDs are not stable — rebuild each session)
- Changing the OTIO wire format or sync protocol
- Handling xStudio playlists that have no Timeline child (flat-only playlists already work)

## Decisions

### D1: Two dicts, cleared on disconnect

```python
self._sync_guid_to_xs_media: dict[str, media_obj]   # sync_guid → live media object
self._xs_uuid_to_sync_guid:  dict[str, str]          # str(media.uuid) → sync_guid
```

Cleared in `_reset_session_state()` alongside the other session dicts. Holding live media object references is safe within a session; objects are evicted on removal events.

**Alternative considered**: Single dict `{sync_guid: (xs_uuid, media_obj)}`. Rejected — reverse lookup (xs_uuid → sync_guid) needed in event handlers that only have the xs object, and the split dict makes that O(1) without tuple unpacking everywhere.

### D2: Bootstrap once, maintain incrementally

Bootstrap runs immediately after `_do_load_timelines()` completes. It scans `playlist.media` and resolves each item to an OTIO clip using `_uri_to_posix_path` normalised comparison against `clip.media_reference.target_url`. Filename-stem fallback handles cases where xStudio strips paths.

After bootstrap, **every** code path that creates or destroys a media item updates the mapping:

| Event | Handler | Mapping action |
| --- | --- | --- |
| Remote add | `_apply_remote_clip_insert` / `_apply_flat_playlist_insert` | register new item |
| Local add (seq) | `_poll_sequence_new_media` | register + broadcast |
| Local add (flat) | `_poll_flat_playlist_new_media` | register + broadcast |
| Remote remove | `_apply_remote_remove_child` | evict both dicts |
| Local remove | `_poll_sequence_track_deletions` | evict both dicts |

**Alternative considered**: Rebuild mapping on every poll tick. Rejected — O(n) scan every 500 ms is wasteful and would re-introduce name-based matching.

### D3: Deduplication keeps the timeline-referenced item

After `load_otio()`, if two media items share a sync GUID:

1. Collect all media items referenced by the newly loaded timeline's clips (via `xs_timeline.clips` or equivalent).
2. The item present in that set is the keeper; remove the other from `playlist.media`.
3. Register only the keeper in the mapping.

This prevents "media offline" on the timeline clips that would occur if the wrong item were removed.

### D4: Flat-playlist OTIO linked to sequence via `xs_sequence_guid`

When xStudio is master and builds the snapshot, `_build_otio_timelines` emits two timelines per playlist-with-timeline:

```
sequence OTIO  (guid = str(xs_tl.uuid))
  metadata: { xs_playlist_name: "<playlist name>" }

flat-playlist OTIO  (guid = str(playlist.uuid))
  metadata: { xs_flat_playlist: true, xs_sequence_guid: str(xs_tl.uuid) }
  tracks[0]: clips in playlist.media order
```

When xStudio is a client receiving a flat-playlist OTIO with `xs_sequence_guid`:
- Look up the corresponding sync playlist entry by `xs_sequence_guid`
- Reorder that playlist's bin to match the flat-OTIO clip order (reuse existing `_apply_remote_move_child` / direct xStudio API reorder)
- Do NOT create a new playlist

### D5: RV skips flat-playlist OTIOs

In `_rebuild_rv_from_otio_snapshot`, skip any timeline where `tl.metadata.get("xs_flat_playlist")` is True. One guard, no RVSequenceGroup created.

### D6: Remove `add_media()` pre-population

The pre-`load_otio` block that calls `add_media(path)` for each clip (lines ~1053–1069 in `_do_load_timelines`) is removed. `load_otio()` owns media creation. The D3 deduplication handles any residual duplicates if xStudio's `load_otio` internally creates entries differently than expected.

## Risks / Trade-offs

**[Risk] Bootstrap fails to match a media item** → Mitigation: log a warning with the unmatched item's name and URI; fall back to legacy name-based lookup for that item only so annotations/selection still work degraded rather than breaking.

**[Risk] `load_otio()` creates media with full paths on some xStudio versions, filenames on others** → Mitigation: bootstrap normalises both sides; the path normalisation helper handles `file://` URI schemes, absolute paths, and bare filenames consistently.

**[Risk] Deduplication removes wrong item (timeline references the full-path item, not the filename-only item)** → Mitigation: always probe the timeline's live clip references before deciding which item to keep (D3). If the timeline reference cannot be determined, keep both and log a warning rather than risk media going offline.

**[Risk] Remote add event fires before `load_otio()` bootstrap completes** → Mitigation: bootstrap is synchronous within `_do_load_timelines`; remote events are processed in the poll loop after `_do_load_timelines` returns. Race condition is not possible within Python's GIL.

## Resolved Questions

**Q: Does xStudio expose the media object referenced by each timeline clip?**
Yes. xStudio `Clip` objects have a `.media` property returning the live media object directly. The set of UUIDs actively referenced by a loaded timeline is:

```python
referenced_media_uuids = {
    str(clip.media.uuid)
    for track in xs_tl.tracks
    if track.is_video
    for clip in track.clips
    if clip.media
}
```

This makes D3 deduplication straightforward: anything in the bin whose `str(media.uuid)` is NOT in `referenced_media_uuids` (and shares a sync GUID with something that IS) is the unreferenced duplicate to remove.

**Q: Is there a direct API for reordering the flat-playlist bin without remove+re-add?**
Yes. `playlist.move_media(moved_media, before=before_media)` already exists and is used by `_apply_flat_playlist_move`. D4 bin reorder uses this directly: iterate the flat-OTIO clip order, resolve each clip to its media object via `_sync_guid_to_xs_media`, and call `move_media` to place items in order. No destructive remove+re-add needed.
