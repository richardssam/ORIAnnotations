## Context

The xStudio client identifies timeline clips by re-serialising the live xStudio
timeline to OTIO (`xs_tl.to_otio_string()`) and matching each exported clip back
to a stored ORI sync GUID. Matching is done by URL/path comparison against
`clip.media_reference.target_url`.

xStudio's internal media is addressed by `xstudio://` URIs. When it exports OTIO,
those become `MissingReference` schema objects with no `target_url`, so
`clip.media_reference.target_url` is always empty. The URL/stem-matching loop in
`poll_sequence_reorders` (and the equivalent code in
`poll_sequence_track_deletions` and `poll_sequence_source_ranges`) therefore
never matches, `current_order` is always empty, and reorder / source-range /
track-deletion detection is silently broken on the xStudio client.

The relevant GUID assignment already happens in
[timeline_build.py:281-291](../../../xstudio_plugin/ori_sync/timeline_build.py#L281-L291):
after `to_otio_string()` is parsed into an OTIO `tl`, deterministic sync GUIDs
are written into `track.metadata["sync"]["guid"]` and
`clip.metadata["sync"]["guid"]` on the *in-memory OTIO copy only*. Those GUIDs
never make it back into xStudio, so the next `to_otio_string()` does not carry
them.

xStudio's Python API exposes a per-item OTIO metadata dict via the
`Item.item_prop` property
([item.py:71-90](/Users/sam/git/xstudio/python/src/xstudio/api/session/playlist/timeline/item.py#L71-L90)),
a JSON-store getter/setter that round-trips through
`connection.request_receive(..., item_prop_atom())`. Whatever is in `item_prop`
at export time appears verbatim as that item's `metadata` in
`to_otio_string()` output. The timeline structure is reachable as
`xs_tl.tracks` → each track's `.children` (Clips/Gaps), in the same order as the
parsed OTIO.

## Goals / Non-Goals

**Goals:**
- Persist the ORI sync GUID for each xStudio timeline track and clip into that
  item's `item_prop`, so native `to_otio_string()` exports carry
  `metadata["sync"]["guid"]`.
- Replace URL/path-based clip identity resolution in the sequence polling
  functions with a direct `clip.metadata["sync"]["guid"]` read.
- Restore working reorder, track-deletion, and source-range detection on the
  xStudio client.

**Non-Goals:**
- No protocol, RV-plugin, or xStudio C++ changes.
- No change to how GUIDs are *derived* (the deterministic seed scheme in
  `build_otio_timelines` / `build_single_sequence_otio` stays as-is).
- Flat playlists (playlist-media, not timeline items) are out of scope — they do
  not use `item_prop` and are not the source of the reorder bug.

## Decisions

### Write item props via a parallel structural walk

Add a helper (e.g. `_write_sync_item_props(xs_tl, tl)`) in `timeline_build.py`,
called immediately after the GUID-assignment loop in `build_otio_timelines`
and `build_single_sequence_otio`. It walks `xs_tl.tracks` in lockstep with
`tl.tracks`, and each track's `.children` Clips in lockstep with the OTIO Clip
children, writing the same GUID that was just assigned to the OTIO copy.

*Alternative considered:* look each xStudio item up by name/uuid. Rejected —
the parsed OTIO was produced from this exact `xs_tl` in this same call, so index
alignment is exact and cheaper than per-item lookups.

### Read-merge-write each item_prop, don't blind-overwrite

The setter replaces the item's whole prop dict. To avoid clobbering any props
xStudio itself stores, read the current `item_prop`, `setdefault("sync", {})`,
set `["guid"]`, and write it back.

*Trade-off:* this is two `request_receive` round-trips per item (get + set).

### Bound every item_prop round-trip

Per [[project_xstudio_request_receive_timeout]], `request_receive` against a
stale actor blocks the calling thread for ~100s. Every `item_prop` get/set MUST
be wrapped with the project's bounded-call helper (`utils.bounded`) and failures
logged and skipped, so a single bad item cannot freeze the build. Prop writes
are best-effort: a skipped write just leaves that clip on the URL-matching
fallback for one cycle.

### Poll functions: read guid, keep a narrow fallback

In each poll function, resolve clip identity by reading
`clip.metadata.get("sync", {}).get("guid")` from the `to_otio_string()` output
first. Because a clip the user has *just* dragged in will not have an
`item_prop` GUID until the next build pass, retain a minimal URL/stem fallback
for clips whose exported metadata has no sync guid, rather than deleting it
outright.

*Alternative considered:* delete the URL-matching code entirely (as the proposal
suggests). Rejected as the default because freshly-added clips would be
unidentifiable for a cycle; keeping a guarded fallback is strictly safer and the
dead branch only runs when the guid is absent.

## Risks / Trade-offs

- [Newly-added clips lack an item_prop GUID until the next build pass] → keep the
  guid-absent URL/stem fallback in the poll functions.
- [Extra `request_receive` round-trips (2 per item) add latency to build] →
  bound each call; writes are best-effort and only happen at build time, not per
  poll tick.
- [`to_otio_string()` might not emit `item_prop` as clip `metadata` on this
  xStudio build] → verify against a real session early (Task list includes a
  round-trip check) before ripping out URL matching.
- [Index misalignment if OTIO parse and `xs_tl.tracks` diverge (e.g. injected
  Annotations track)] → align on the same track/clip ordering used by the GUID
  assignment loop and skip non-Clip children consistently.

## Migration Plan

Pure additive + internal refactor; no persisted data or protocol change. Ships in
one commit. Rollback is a straight revert — the URL-matching fallback remains in
place, so reverting only re-disables reorder detection (the prior broken state),
it does not corrupt anything.
