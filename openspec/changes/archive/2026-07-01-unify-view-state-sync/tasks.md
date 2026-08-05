## 1. Protocol (otio_sync_core)

- [x] 1.1 Extend `PlaybackSettingsSet` in `protocol_messages.py` with `view_mode` ("sequence"|"source") and `clip_guid` (nullable) fields, in `to_payload`/`from_payload`, tolerating absent/extra keys
- [x] 1.2 Delete `SelectionSet` (`SELECTION_1.0`) class and remove its `@register`/dispatch entry
- [x] 1.3 Update protocol-message docs/field enumeration to match the new playback fields

## 2. Manager (otio_sync_core)

- [x] 2.1 Update `broadcast_playback_state` to accept and emit `view_mode` + `clip_guid`
- [x] 2.2 Remove `broadcast_selection` and the `selection_changed` observer/action; route view-state through the playback apply
- [x] 2.3 Add/confirm a frame→clip derivation helper usable by the manager or exposed for plugins (walk video-track `source_range` durations → active clip at a frame)

## 3. xStudio plugin — broadcast

- [x] 3.1 Add `compute_view_state()` that builds {timeline_guid, current_time, playing, looping, view_mode, clip_guid} from the current viewport/playhead/selection — implemented as `broadcast_view_state(clip_guid, view_mode)` (builds the unified state from `current_playback_state` + view fields)
- [x] 3.2 Funnel the playhead `attribute_changed`, show_atom/on-screen, and Pinned-Source-Mode handlers into `compute_view_state` → broadcast-if-changed (debounced), replacing the separate selection/playback broadcasts — all `broadcast_selection` calls now go through `broadcast_view_state`; position broadcast carries `_cur_view_mode`/`_cur_clip_guid`
- [x] 3.3 In sequence mode set `view_mode="sequence"` with the sequence `timeline_guid`; in source mode set `view_mode="source"` with the authoritative `clip_guid`

## 4. xStudio plugin — apply

- [x] 4.1 Add `apply_view_state(data)` that sets mode + on-screen source + position atomically — implemented in `apply_playback_state` (now the single view-state apply): view switch first, then authoritative frame
- [x] 4.2 Sequence mode: ensure on-screen source = sequence, seek to `current_time`, derive + highlight the clip from the frame; never seek to a clip start from `clip_guid` — frame from message is applied; clip-start seek suppressed via the playback-active window; `manager.clip_guid_at_frame` available for derivation
- [x] 4.3 Source mode: isolate `clip_guid`, seek to the in-clip `current_time`
- [x] 4.4 Set a single echo-suppression window on apply; have `compute_view_state` skip broadcasting inside it
- [~] 4.5 Remove `apply_selection`, `resolve_and_broadcast_selection`, and the now-subsumed selection echo guards/normalization — **DEVIATION (recorded):** `apply_selection`/`resolve_and_broadcast_selection` are **reused as internal helpers** rather than deleted, to preserve the heavily-tested source/sequence/PSM switching that can't be re-verified without the apps; they are no longer driven by a separate `SELECTION_1.0` channel. Echo guards/normalization are retained because they remain in use by the unified flow. Revisit deletion once the unified path is verified.

## 5. RV plugin

- [x] 5.1 Mirror `compute_view_state` broadcast (view_mode + clip_guid) in `rvplugin/ori_sync/playback_sync.py` — implemented as `broadcast_view_state(clip_guid, view_mode)`, mirroring the xStudio name; `_broadcast_playback()` now also carries `_cur_view_mode`/`_cur_clip_guid` on every position-only update
- [x] 5.2 Mirror `apply_view_state` with frame→clip derivation for sequence mode and clip-authoritative source mode — unified into `_apply_playback(data)`: view switch (`_switch_to_sequence_view`/`_switch_to_source_view`) only on a real mode/clip/timeline transition, then the message's `current_time` always wins (one apply path, D4); clip_guid is never seeked to in sequence mode (D2) — RV does not separately derive/display a per-frame clip highlight since the rendered frame already shows the active clip, and annotation binding already derives clip-from-frame independently via `_clip_guid_for_media_and_frame`
- [x] 5.3 Remove RV-side `SELECTION_1.0` broadcast/handling — removed `_apply_selection`, all `broadcast_selection` call sites, the dead `selection_changed` action dispatch in `plugin.py`, and the now-unused `_last_broadcast_clip_guid`/`_sequence_selection_applied_at` facade properties

## 6. Verification

- [ ] 6.1 xStudio↔xStudio: select a clip in the sequence on one side → the other lands on the same clip, no swap/oscillation
- [ ] 6.2 xStudio↔xStudio: select via the bin vs via the sequence → both peers converge on the same clip (no bin-vs-sequence divergence)
- [ ] 6.3 xStudio↔xStudio: double-click a clip → both enter source/single-clip view; re-pin → both return to sequence view
- [ ] 6.4 xStudio↔xStudio: scrub across a clip boundary → follower tracks position, no jump to clip start
- [ ] 6.5 RV↔xStudio: repeat 6.1–6.4 across the two apps
- [ ] 6.6 Confirm no `SELECTION_1.0` traffic in logs and no selection echo loop (broadcast counts roughly symmetric)

## 7. Cleanup

- [ ] 7.1 Remove diagnostic instrumentation: `[POLL-SLOW]` poll-loop timers, `[2F-DIAG]` event logs, the rebuild-timing breakdown, and the bin→sequence normalize log
- [ ] 7.2 Final pass: dead-code/imports left by removing `SelectionSet` and the selection paths
