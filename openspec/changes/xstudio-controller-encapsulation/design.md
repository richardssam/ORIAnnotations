## Context

`ORISyncPlugin.__init__` currently declares ~25 private fields that belong to domain controllers, because the module-structure spec's "Shared cross-thread state ownership" requirement parked anything touched across threads or domains on the plugin. Controllers reach back via `self.plugin.<attr>` (~200 occurrences in `playback_sync.py`), `disconnect()` hand-clears ~20 controller privates, and `__init__` contains comment blocks describing attributes that no longer exist there. A reference scan (grep across the package) shows the true ownership picture: most fields are touched only by `playback_sync.py` plus the plugin's `__init__`/`disconnect()`; three are cross-domain; three are dead.

This change is the Phase 0 prerequisite named in `session-roles/design.md` (D5): the echo guards must live in controller-local scope before Phase 1c deletes them.

## Goals / Non-Goals

**Goals:**

- Every domain field lives on the controller that owns its domain; `ORISyncPlugin` keeps only cross-cutting infrastructure.
- One `reset()` per controller; `disconnect()` becomes manager teardown plus a reset loop.
- `__init__` comments describe attributes that actually exist where the comment sits.
- Byte-identical sync behaviour — same guards, same timing, same protocol.

**Non-Goals:**

- No guard deletion, no echo-logic changes (that is `session-roles` Phase 1c).
- No accessor methods or property indirection for cross-domain guards (see D2).
- No changes to the RV plugin (its shim removal is the separate `rv-plugin-encapsulation` change).
- No changes to the threading model, locking, or the command queue.

## Decisions

### D1: Ownership map

Derived from actual reference sites, not from the `__init__` comment layout:

| Field | New owner | Notes |
|---|---|---|
| `_last_polled_frame`, `_last_applied_frame`, `_last_polled_playing` | `PlaybackSyncController` | referenced only by playback + plugin lifecycle |
| `_playback_apply_suppress_until`, `_local_scrub_active_until`, `_playing_started_at` | `PlaybackSyncController` | |
| `_last_show_atom_media`, `_last_show_atom_seq_tl_guid`, `_last_show_atom_at` | `PlaybackSyncController` | |
| `_applying_pinned_mode`, `_selection_broadcast_suppress_until` | `PlaybackSyncController` | resolves the playback-vs-structure ambiguity: all readers/writers are playback |
| `_viewport_container_is_playlist`, `_viewport_container_is_timeline` | `PlaybackSyncController` | the `getattr(self.plugin, ..., False)` defensive reads become plain attribute reads |
| `_annotation_pending_time` | `AnnotationSyncController` | cross-domain: playback also writes it (via `self.plugin.annotation.<attr>`, D2) |
| `_reload_suppress_until` | `AnnotationSyncController` | cross-domain: it guards the annotation flush path; structure/playback *set* it after reloads |
| `_structural_mutation_suppress_until` | `StructureSyncController` | cross-domain: playback also reads it |
| `_ann_ui_plugin` | `DisplaySyncController` | resolved (see Open Questions): `display_sync.py` is the *sole* reader |
| `_last_remote_stop_at`, `_last_selection_scan`, `_last_flat_playlist_scan` | **deleted** | initialised in `__init__`, never read or written anywhere else |

Remaining on `ORISyncPlugin`: `manager`, `_cmd_queue`, `_poll_stop`/`_poll_thread`, `_sync_playlists` (five modules reference it — genuinely cross-cutting), `_pending_create_check` (session-menu lifecycle), and `_last_fullstate_write` (test-inspector dump timer in the poll loop). Everything else moves or dies per the table.

The UI attributes (`mq_host_attr`, `status_attr`, …), menu setup, and subscriptions stay on the plugin — they are session lifecycle, which the module-layout spec already assigns to `ori_sync_plugin.py`.

### D2: Cross-domain guards are accessed as `self.plugin.<owner>.<attr>` — no accessor methods

Three fields are written by a non-owning domain (`_reload_suppress_until`, `_annotation_pending_time`, `_structural_mutation_suppress_until`). The existing spec already blesses sibling access via `self.plugin.<controller>.<method/attr>`, and these fields are scheduled for deletion or replacement by `session-roles` (the reload guard becomes an apply-scope guard; the playback windows are deleted). Adding `suppress_flushes_until()`-style methods now would be API design for code with weeks to live. Raw attribute access keeps this change purely mechanical — every edit is a rename, greppable and diffable.

- *Alternative — accessor methods on owning controllers:* cleaner in principle, rejected for churn on soon-to-die fields. If a guard survives `session-roles`, promote it to a method then.

### D3: `reset()` contract

Each controller defines `reset()` returning all its state to post-construction defaults. Rules:

- `reset()` must be idempotent and safe to call when never connected (it runs on plugin unload via `cleanup()` → `disconnect()`).
- Resource release (event-group unsubscribe, cached actor handles) belongs in the owning controller's `reset()` — e.g. playback's selection-subscription teardown (`_current_selection_sub_id` unsubscribe) moves out of `disconnect()` into `PlaybackSyncController.reset()`, which may call `self.plugin.unsubscribe_from_event_group(...)`.
- `disconnect()` orders: stop poll thread → close manager → `reset()` each controller → clear plugin-owned state → set status attribute. Controllers are reset *after* the manager closes so no poll tick observes half-reset state (same effective ordering as today's inline clears).
- **Correction (verified during specs):** `reset()` already exists on *all six* domain controllers — `media`, `color`, `annotation`, `playback`, `display`, `structure`. The gap is not missing methods but that `disconnect()` only calls three of them (`media`, `color`, `annotation`) and hand-clears the other three's fields inline, duplicating what their `reset()` already does. So the work is: extend each `reset()` to cover newly-moved fields, then delete the inline clears. `PlaybackSyncController.reset()` *already* performs the selection-subscription unsubscribe described above; `disconnect()` merely duplicates it.
- `TimelineBuildController` holds exactly one field (`_last_timeline_defer_log_time`, a log throttle) and so gains a real `reset()`, not an empty one.

### D4: Comments move with their attributes; orphans die

Every comment block in `__init__` describing a moved field travels to the field's new declaration site. Blocks describing already-moved state (the stranded paragraphs at the `_sync_playlists`/show-atom/scan-timestamp regions) are deleted — the controller-side declarations either already carry documentation or receive the comment if it still says something true.

### D5: The spec requirement is replaced, not amended

The delta spec rewrites "Shared cross-thread state ownership" to state the new rule: state SHALL live on the controller owning its domain; plugin attributes are limited to the manager reference, command queue, poll-thread lifecycle, and canonical timeline registry; cross-thread safety is carried by the "Threading invariant preserved" requirement (poll-thread-only manager access, GIL-atomic attribute reads/writes) and is independent of which object holds a field. The scenarios about reading guards via `self.plugin.<guard>` are replaced with scenarios reading via the owning controller.

## Risks / Trade-offs

- **[Silent behaviour change via `getattr` defaults]** `playback_sync.py` reads some plugin fields with `getattr(self.plugin, "...", default)`; a missed rename would silently return the default instead of raising. → Convert all such reads to plain attribute access as part of the move (the attribute is now guaranteed to exist on `self`), and grep the package for `self.plugin._` after the move — the residue must be exactly the sanctioned plugin-owned set.
- **[Echo-timing drift]** Any accidental re-initialisation difference (e.g. a guard reset on `reset()` that previously survived reconnect) changes suppression behaviour. → `reset()` values must equal today's `disconnect()` clears exactly; fields today *not* cleared on disconnect (e.g. `_last_fullstate_write`) keep that behaviour. Two-client `sync_test/` suite is the gate, per the spec's "Behaviour unchanged" requirement.
- **[Unsubscribe-during-teardown ordering]** Moving playback's selection unsubscribe into `reset()` runs it after `manager.close()` instead of before. → The unsubscribe targets xStudio's event system, not the manager; order relative to manager close is irrelevant. Preserve the existing try/except swallow.
- **[Conflict with in-flight changes]** `session-roles` artifacts reference some of these fields by their plugin-resident names. → This change lands first (it is the named prerequisite); `session-roles` tasks should be written against controller-resident names.

## Migration Plan

Single PR, one commit per controller domain (playback, annotation, structure, display/plugin cleanup), each independently green:

1. Move playback-owned fields + comments; convert `getattr` reads; run suite.
2. Move annotation- and structure-owned fields (cross-domain writers updated to `self.plugin.<owner>.<attr>`); run suite.
3. Add `reset()` to the four controllers; collapse `disconnect()`; delete the three dead fields and orphaned comments; run suite.
4. Final sweep: `grep -rn "self\.plugin\._" xstudio_plugin/ori_sync/` residue must be only `_cmd_queue`, `_sync_playlists`, `_poll_stop`, `_pending_create_check` (and `manager`, unprefixed).

Rollback: revert the PR; no protocol, preference, or on-disk format is touched.

## Open Questions

Both resolved by reference scan before tasks were written; no open questions remain.

- ~~Does `TimelineBuildController` hold any session state needing `reset()`?~~ **Resolved: yes, one field.** `_last_timeline_defer_log_time` (a "viewport not ready" log throttle) is its only instance state. It gets a one-line `reset()` — a real one, so the "every controller exposes `reset()`" rule holds without exception.
- ~~Is `_ann_ui_plugin` better owned by annotation or display?~~ **Resolved: display.** `display_sync.py` is the sole reader (`apply_display_state` and one other site, both `getattr(self.plugin, "_ann_ui_plugin", None)`); `annotation_sync.py` never touches it. The D1 table is corrected accordingly. The handle is *acquired* in the plugin's connect path (`self.get_plugin("AnnotationsUI")`), which is session lifecycle and stays there — but it populates the field through a small `DisplaySyncController` method rather than assigning a controller private from outside. This is not a violation of D2: D2 rejects accessor methods for soon-to-die cross-domain *guards*, whereas this is an acquisition step whose try/except belongs with the field.
