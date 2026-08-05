## Why

"What is each peer looking at" is currently described by **two independent
channels** — `SELECTION_1.0` (which clip / view mode) and `PLAYBACK_SETTINGS_1.0`
(which timeline / frame / play state). They can disagree, and both echo
bidirectionally, so applying one peer's state mutates the local state and
re-broadcasts it. This is the root cause of a recurring family of bugs:

- a **selection swap/echo loop** where two peers endlessly bounce between two
  clips because each applies the other's selection and re-broadcasts its own;
- **bin-vs-sequence guid mismatch** — the same media is a separate OTIO object
  in the bin and in the sequence, with different sync guids, so a bin selection
  can't be matched by a peer that only has the sequence;
- **clip-start scrub jumps** — crossing a clip boundary while scrubbing fires a
  selection that seeks the follower to the clip's start, fighting the
  authoritative playhead position;
- **timeline_guid ambiguity** — a transient per-clip timeline guid gets
  broadcast instead of the sequence guid, so the follower ignores the update.

Every mitigation so far (echo guards, guid normalization, suppression windows)
is a band-aid over the two-source-of-truth design. Consolidating into a single
authoritative view-state removes the class of bug rather than patching instances.

## What Changes

- **BREAKING:** Retire the `SELECTION_1.0` message entirely. Its role (which clip
  is active, sequence vs. source view) moves into the playback message.
- **BREAKING:** Extend `PLAYBACK_SETTINGS_1.0` into the sole view-state message by
  adding two fields:
  - `view_mode`: `"sequence"` | `"source"`
  - `clip_guid`: the active clip (nullable)
- Define one authority per mode:
  - **sequence mode** — `timeline_guid` + `current_time` are authoritative; the
    receiver seeks to the frame and **derives** the active clip from the track's
    `source_range` durations. `clip_guid` is confirmation/highlight only and is
    never seeked to.
  - **source mode** — `clip_guid` is authoritative (the isolated single clip);
    `current_time` is the in-clip offset.
- Collapse broadcasting into one `compute_view_state` path fed by every
  view-affecting event (playhead move, selection/show change, view-mode change),
  and applying into one atomic `apply_view_state` path, guarded by a single
  echo-suppression window. Because position is authoritative and the clip is
  derived, two peers converge on the same frame instead of oscillating.
- Hard cutover: no version bump and no backward compatibility — the code is not
  in use by anyone else, so old `SELECTION_1.0` handling is deleted rather than
  kept behind a compatibility path.
- Remove the now-subsumed selection echo guards / guid normalization and the
  temporary diagnostic instrumentation (`[POLL-SLOW]`, `[2F-DIAG]`, rebuild
  timing, normalize logs) added while debugging.

## Capabilities

### New Capabilities
- `view-state-sync`: the unified view-state contract — the extended
  `PLAYBACK_SETTINGS_1.0` message, its `view_mode`/`clip_guid` semantics, the
  per-mode authority rules, frame→clip derivation in sequence mode, and the
  single-broadcast / single-apply / single-echo-window model that both the RV and
  xStudio plugins implement.

### Modified Capabilities
- `otio-sync-core`: protocol message set changes — `PlaybackSettingsSet` gains
  `view_mode` and `clip_guid`; `SelectionSet` (`SELECTION_1.0`) and its dispatch
  registration are removed; `broadcast_selection` / the `selection_changed`
  manager action are retired.

## Impact

- **Protocol:** `python/otio_sync_core/protocol_messages.py` (extend
  `PlaybackSettingsSet`, delete `SelectionSet`), dispatch registry in
  `python/otio_sync_core/manager.py` (`broadcast_playback_state` carries the new
  fields; remove `broadcast_selection` and the `selection_changed` action).
- **xStudio plugin:** `xstudio_plugin/ori_sync/playback_sync.py` — collapse the
  show_atom / `attribute_changed` / Pinned-Source-Mode handlers into one
  `compute_view_state` broadcaster; replace `apply_selection` /
  `resolve_and_broadcast_selection` with one `apply_view_state`; add the
  frame→clip derivation helper; delete the subsumed echo guards.
- **RV plugin:** `rvplugin/ori_sync/playback_sync.py` — mirror the broadcast +
  apply of view-state and the frame→clip derivation.
- **Cross-app:** both plugins must be updated together (hard cutover); a mixed
  old/new session is explicitly unsupported.
- **Cleanup:** remove diagnostic instrumentation added during debugging.
