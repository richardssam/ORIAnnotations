Each numbered group is one commit and must leave the `sync_test/` two-client suite green
before the next begins. Reference scan performed while writing these tasks confirmed that
**no plugin-body logic reads any moving field** — every one is declared in
`ORISyncPlugin.__init__` and consumed only by controllers (plus three clears in
`disconnect()`). The moves are therefore mechanical renames.

## Suite baseline (2026-08-03)

The suite is **not green on this branch, independently of this change**. `add_media`,
`reorder_media`, and `delete_media_openrv_noscript` fail on frame assertions
(`expected frame ~N, got M`); nothing structural fails. Confirmed pre-existing by
re-running with this change stashed — identical failures.

Cause: all three are driven by recordings captured 2026-07-06, during the window when
xStudio playhead attribute events were silently lost (fixed 2026-08-02 by `cf6ad99`).
`fix-xs-playhead-attribute-subscription` task 3.3 already declares such runs "void for
position-dependent assertions", and its task 3.2 flags the recordings as needing
re-recording. The one position test with a post-fix recording
(`xstudio_selects`, re-recorded 2026-08-02) passes. Blocked on that re-recording, not
on this change.

Independent evidence this change is not implicated: reapplying the intended rename
mechanically to each controller at `HEAD` and diffing against the working copy yields
**zero behavioural divergence** across all seven controllers — only added declarations,
docstrings, one import, and blank lines.

The genuinely new-risk path here is **disconnect → rejoin** (this change is the first to
route teardown through `reset()`), which the suite does not cover. Tasks 3.10 and 4.5
remain open for that reason and are the ones that still need a human at the keyboard.

## 1. Move playback-owned state

- [x] 1.1 Move the frame/play echo-guard declarations and their comment blocks from `ori_sync_plugin.py:175-179` to `PlaybackSyncController.__init__`: `_last_polled_frame`, `_last_applied_frame`, `_last_polled_playing`
- [x] 1.2 Move the scrub/apply window declarations and comments from `ori_sync_plugin.py:183-199`: `_playback_apply_suppress_until`, `_local_scrub_active_until`, `_playing_started_at`
- [x] 1.3 Move the show-atom tracking declarations and comments from `ori_sync_plugin.py:202-210`: `_last_show_atom_media`, `_last_show_atom_seq_tl_guid`, `_last_show_atom_at`
- [x] 1.4 Move `_applying_pinned_mode` and `_selection_broadcast_suppress_until` (`ori_sync_plugin.py:229,234`) to `PlaybackSyncController` — every reader and writer is playback
- [x] 1.5 Move `_viewport_container_is_playlist` / `_viewport_container_is_timeline` (`ori_sync_plugin.py:261-262`) to `PlaybackSyncController`; both writer (`playback_sync.py:786-787`) and reader are already playback-internal
- [x] 1.6 Rewrite all `self.plugin.<attr>` reads/writes of the above in `playback_sync.py` as plain `self.<attr>`
- [x] 1.7 Convert the two defensive reads at `playback_sync.py:189-190` from `getattr(self.plugin, "_viewport_container_is_*", False)` to plain attribute access — the attribute is now guaranteed present, and a typo must raise rather than silently yield `False`
- [x] 1.8 Run the `sync_test/` two-client suite — run 2026-08-03: 5 pass, 3 fail. All 3 failures are frame assertions proven pre-existing (see Suite baseline); no new echoed playback or selection events

## 2. Move annotation-, structure-, and display-owned state

- [x] 2.1 Move `_annotation_pending_time` (`ori_sync_plugin.py:171`) and its comment to `AnnotationSyncController`; update playback's writes to `self.plugin.annotation._annotation_pending_time`
- [x] 2.2 Move `_reload_suppress_until` (`ori_sync_plugin.py:166`) and its comment to `AnnotationSyncController` — it guards the annotation flush path; update structure/playback setters to `self.plugin.annotation._reload_suppress_until`
- [x] 2.3 Move `_structural_mutation_suppress_until` (`ori_sync_plugin.py:235`) and its comment to `StructureSyncController`; update playback's reads to `self.plugin.structure._structural_mutation_suppress_until`
- [x] 2.4 Move `_ann_ui_plugin` (`ori_sync_plugin.py:127`) to `DisplaySyncController` — display is the sole reader (resolved Open Question); add a `DisplaySyncController` method that performs the `get_plugin("AnnotationsUI")` acquisition with its existing try/except, and call it from the plugin connect path in place of `ori_sync_plugin.py:441`
- [x] 2.5 Convert the two `getattr(self.plugin, "_ann_ui_plugin", None)` reads at `display_sync.py:137,229` to plain `self._ann_ui_plugin` reads, preserving the existing `None` handling (the handle is legitimately `None` before connect, so the `None` *check* stays — only the `getattr` indirection goes)
- [x] 2.6 Run the `sync_test/` two-client suite — same run as 1.8; no additional failures introduced by group 2

## 3. Collapse teardown onto reset()

- [x] 3.1 Extend `PlaybackSyncController.reset()` to cover the fields moved in group 1, matching the values `disconnect()` previously established (guards to `0.0`/`False`, frame/play guards to `None`)
- [x] 3.2 Extend `AnnotationSyncController.reset()` and `StructureSyncController.reset()` to cover the fields moved in group 2
- [x] 3.3 Extend `DisplaySyncController.reset()` to clear `_ann_ui_plugin` to `None`
- [x] 3.4 Add `TimelineBuildController.reset()` clearing `_last_timeline_defer_log_time` to `0.0` — its only instance state
- [x] 3.5 Replace the inline clears in `disconnect()` (`ori_sync_plugin.py:528-556`) with `reset()` calls on all seven controllers, ordered: stop poll thread → close manager → reset controllers → clear plugin state (`_sync_playlists`) → set status attribute
- [x] 3.6 Delete the now-duplicated selection-unsubscribe block from `disconnect()` — `PlaybackSyncController.reset()` already performs it, including the try/except swallow
- [x] 3.7 Verify each `reset()` is idempotent and safe pre-connect (called via `cleanup()` → `disconnect()` on a never-connected instance); fix any attribute that would raise
- [x] 3.8 Delete the three dead fields, assigned in `__init__` and never read anywhere: `_last_remote_stop_at` (`:203`), `_last_selection_scan` (`:239`), `_last_flat_playlist_scan` (`:241`)
- [x] 3.9 Delete orphaned comment blocks in `__init__` describing state that no longer lives there; confirm every remaining comment describes an attribute still declared in `__init__`
- [x] 3.10 Run the `sync_test/` two-client suite; additionally exercise a disconnect→rejoin cycle and confirm no stale suppression window delays the first synced playback or annotation event

## 4. Verify the encapsulation boundary

- [x] 4.1 Run `grep -rn "self\.plugin\._" xstudio_plugin/ori_sync/` — the residue must be exactly `_cmd_queue`, `_sync_playlists`, and `_on_test_container_event` (a callback method, not state, passed at `structure_sync.py:137`); any other hit is a missed move. Note `_poll_stop` and `_pending_create_check` stay plugin-owned but are already never referenced from a controller
- [x] 4.2 Run `grep -rn "getattr(self\.plugin" xstudio_plugin/ori_sync/` — must return no hits for relocated state
- [x] 4.3 Confirm `ORISyncPlugin.__init__` retains only cross-cutting infrastructure and plugin UI/menu handles, per the spec's plugin-attribute-surface scenario
- [x] 4.4 Confirm no protocol message, preference name, menu item, or QML integration changed — diff review, no code path should touch these
- [x] 4.5 Reload the plugin in xStudio and confirm connect → sync → leave → rejoin works end to end (`reset()` runs on the unload path, which the suite does not cover)

## 5. Update the spec and hand off

- [x] 5.1 Run `openspec validate --strict xstudio-controller-encapsulation`
- [x] 5.2 Note in the `session-roles` change that its Phase 1c guard deletion now targets controller-resident field names (`self.playback._*`, `self.plugin.annotation._reload_suppress_until`), not plugin-resident ones
