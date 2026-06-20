## Context

`xstudio_plugin/ori_sync/ori_sync_plugin.py` is a 5232-line single-file implementation of `ORISyncPlugin(PluginBase)`. It handles seven distinct responsibilities: session/menu lifecycle, OTIO timeline construction (master side), playback/selection synchronisation, display-state synchronisation, structural synchronisation, annotation broadcast/apply, and sync-GUID ↔ xStudio-media mapping. These concerns are interwoven in one class whose `__init__` is ~290 lines declaring ~60 mutable instance attributes accessed freely across all concerns.

This mirrors the situation we just resolved for the OpenRV plugin in `2026-06-07-rv-plugin-splitting`, which split a 3063-line `OpenRVSyncPlugin` into 6 files using a delegated controller pattern. We adopt the same pattern here, with three xStudio-specific adaptations driven by explore-mode decisions.

Key constraints, from the module docstring and the runtime model:
- **Threading.** xStudio calls plugin event handlers (`_on_global_playhead_event`, `_on_selection_event`, `_on_position_event`, `_on_annotation_event`, etc.) on its own message-dispatch thread. The RabbitMQ send path uses a `BlockingConnection` and must not run on that thread. **Only the poll thread (`_poll_loop`) touches the SyncManager after startup.** xStudio handlers therefore only mutate cheap local state or enqueue onto `_cmd_queue`; the poll thread drains the queue (`_drain_cmd_queue` → `_execute_command`) and processes manager events (`_handle_manager_event`).
- **Echo / suppression guards.** A set of cross-domain, cross-thread fields suppress self-induced events: `_reload_suppress_until`, `_selection_broadcast_suppress_until`, `_structural_mutation_suppress_until`, `_applying_pinned_mode`, and the frame echo-guard pair `_last_polled_frame` / `_last_applied_frame`. These are the xStudio analog of RV's single `_rv_updating` flag, but there are several of them and they span domains.
- **Package loading.** xStudio loads `xstudio_plugin/ori_sync/` as a real Python package via `__init__.py`, which re-exports `create_plugin_instance` and `ORISyncPlugin`. This permits intra-package relative imports — a freedom the RV mode-loader did not have.
- **The SyncManager** instance is the single source of truth for OTIO state and is accessed by every concern (only from the poll thread).

## Goals / Non-Goals

**Goals:**
- Split `ori_sync_plugin.py` into 8 modules with clear single responsibilities.
- Maintain identical runtime behaviour — no protocol, API, threading, or user-visible changes.
- Move each domain's instance state onto its owning controller, leaving only cross-cutting cross-thread state on the plugin.
- Reduce inter-concern coupling so each module can be understood independently.
- Preserve the threading invariant exactly: methods moved into controllers must not change which thread they run on.

**Non-Goals:**
- Changing the public entry-point surface (`create_plugin_instance`, `ORISyncPlugin`, menu callbacks, xStudio event-handler signatures).
- Refactoring `otio_sync_core` or any code outside `xstudio_plugin/ori_sync/`.
- Altering the threading model, the command-queue marshalling, or the suppression-guard semantics.
- Adding new tests beyond fixing any that break on import-path changes.
- Performance optimisation or behavioural improvement of any kind.

## Decisions

### Decision 1: Delegated controller pattern, not inheritance or mixins

Each domain gets its own controller class. `ORISyncPlugin.__init__` instantiates them and delegates from event handlers and the two dispatch tables. This matches the proven RV refactor.

**Alternative — mixins:** rejected. Mixins share `self` and all state indiscriminately, defeating the goal of clear ownership; debugging name collisions across 7 concerns is painful.

**Alternative — standalone functions:** rejected. Most domains carry significant local state (annotation hot-scan/stroke caches, structural playlist maps, media mappings) that belongs together with its methods.

### Decision 2: Controllers receive a back-reference to the plugin

Each controller's `__init__` takes a single `plugin` argument stored as `self.plugin`, giving access to `self.plugin.manager`, the suppression guards, the command queue, and sibling controllers (`self.plugin.media`, etc.).

**Rationale:** mirrors the RV decision; avoids a verbose constructor signature that changes whenever a new shared field appears. Controllers exist solely to serve this one plugin and will never be reused elsewhere, so the coupling is acceptable.

### Decision 3: Cross-cutting cross-thread state stays on the plugin; domain state moves to controllers

This is the main divergence from RV (where most state stayed on the plugin). Here, domain state migrates onto its owning controller:

| Stays on `ORISyncPlugin` (cross-thread / cross-domain) | Moves to a controller |
|---|---|
| `manager`, `active_playhead` | `_hot_scan_*`, `_stroke_uuid_cache`, `_annotation_*`, `_*bookmark*`, `_last_sent_captions` → annotation |
| `_cmd_queue`, `_poll_stop`, `_poll_thread` | `_xs_flat_playlists`, `_xs_sequence_*`, `_timeline_item_*`, `_xs_media_order` → structure |
| `_reload_suppress_until`, `_selection_broadcast_suppress_until`, `_structural_mutation_suppress_until`, `_applying_pinned_mode` | `_xs_base_scale`, `_viewport`, `_last_display_state`, `_last_pinned_source_mode` → display |
| `_last_polled_frame`, `_last_applied_frame`, `_last_polled_playing` (frame/play echo guard) | `_pending_seek_*`, `_current_selection_*`, `_last_viewed_clip_guid` → playback |
| `_sync_playlists` (the canonical timeline registry, read by several controllers) | `_sync_guid_to_xs_media`, `_xs_uuid_to_sync_guid`, `_flat_clip_to_media` → media |

**Rationale:** the user chose this in explore. The suppression guards and frame echo-guards are genuinely read/written from both the xStudio thread and the poll thread across multiple domains; duplicating them per-controller would introduce synchronisation bugs, exactly as RV kept `_rv_updating` on the plugin. `_sync_playlists` is the shared timeline registry consulted by playback, structure, display, and annotation, so it also stays on the plugin.

**Trade-off:** this is the largest verification surface — every moved attribute requires catching all `self.x` references and rewriting them to `self.plugin.x` (cross-thread fields) or `self.<sibling>.x` / `self.x` (domain fields). Validated against `sync_test/` integration tests.

### Decision 4: media_map is a first-instantiated controller

The sync-GUID ↔ xStudio-media mapping is consumed by playback, structure, and annotation. It becomes `MediaMapController`, accessed via `self.plugin.media`, and is **instantiated first** in `__init__` so the other controllers can reference it during their own construction if needed.

**Alternative — make it a `utils`-level stateless module:** rejected by the user in explore; it carries dict state (`_sync_guid_to_xs_media`, `_xs_uuid_to_sync_guid`, `_flat_clip_to_media`) and behaves like the other controllers, so treating it as one keeps the model uniform.

### Decision 5: Relative imports within the package

Modules use intra-package relative imports (`from .utils import _log`, `from .media_map import MediaMapController`). The package's `__init__.py` continues to expose `create_plugin_instance` and `ORISyncPlugin`.

**Divergence from RV (documented intentionally):** the RV refactor forbade `__init__.py`/sub-packages because RV's mode-loader cannot import them. xStudio already loads this directory as a package via `__init__.py`, so relative imports are safe and idiomatic here. Risk noted below.

### Decision 6: Both dispatch tables stay in the entry-point

The threading model splits RV's single `_handle_action` into two routing tables, and both stay in `ori_sync_plugin.py`:
- `_handle_manager_event(action, data)` — routes remote sync events (poll thread) to controller apply-methods.
- `_execute_command(cmd, payload)` + `_execute_sync_container` — routes drained `_cmd_queue` items (poll thread) to controller broadcast/apply-methods.

This keeps all routing visible in one place rather than scattered across controllers, mirroring RV decision 7.

### Decision 7: Event handlers stay thin on the plugin

xStudio event subscriptions (`subscribe_to_global_playhead_events`, per-container/selection/position/timeline-item subscriptions) require bound methods on the plugin instance and run on the xStudio thread. The `_on_*` handlers stay on `ORISyncPlugin` as thin delegators that enqueue or call the relevant controller, preserving the "xStudio thread only enqueues" invariant. The controller methods they delegate to carry the real logic.

### Decision 8: Module layout

```
xstudio_plugin/ori_sync/
├── __init__.py          (unchanged: re-exports create_plugin_instance, ORISyncPlugin)
├── ori_sync_plugin.py   ~750  ORISyncPlugin(PluginBase): __init__, menus, session
│                              lifecycle, _poll_loop, _drain_cmd_queue,
│                              _execute_command, _execute_sync_container,
│                              _handle_manager_event, _on_synced, thin _on_* handlers,
│                              create_plugin_instance
├── utils.py             ~100  _make_logger/_log/_log_exc, _uri_to_posix_path,
│                              _parse_ori_session, module constants
├── timeline_build.py    ~500  TimelineBuildController: _build_otio_timelines,
│                              _build_otio_from_{viewed_container,playlist_media},
│                              _build_single_sequence_otio, _do_load_timelines,
│                              _prepare_otio_for_load, _fill_source_ranges
├── playback_sync.py     ~900  PlaybackSyncController: playhead/position/selection
│                              handlers + apply, _resolve_*, _apply_selection,
│                              _apply_playback_state, _pending_seek
├── display_sync.py      ~200  DisplaySyncController: _get_viewport,
│                              _read_xs_display_state, _apply_display_state,
│                              _poll_and_broadcast_display
├── structure_sync.py    ~1230 StructureSyncController: _poll_* (reorders/new media/
│                              deletions/new playlists/renames), _apply_flat_playlist_*,
│                              _apply_sequence_insert, _apply_remote_{move,remove}_child
├── annotation_sync.py   ~1050 AnnotationSyncController: _on_annotation_event,
│                              _hot_scan_active_annotation, _broadcast_*,
│                              _flush_pending_annotations, _apply_remote_annotation,
│                              _refresh_annotation_bookmark, caption helpers
└── media_map.py         ~300  MediaMapController: _register/_evict/_media_for_sync_guid,
                               _bootstrap_media_mapping, _sync_guid_for_xs_uuid,
                               _clip_guid_for_media_name, _find_media_for_clip_guid
```

`structure_sync.py` and `annotation_sync.py` are the largest because those domains are inherently complex and tightly coupled internally. Splitting them further would force shared helpers into a grab-bag or duplicate them, so they stay whole — same trade-off accepted in the RV refactor.

### Decision 9: timeline_build stays a separate controller

OTIO construction (`_build_otio_*`, `_do_load_timelines`) is logically distinct from structural diff-polling, even though both touch timelines. Keeping it separate avoids a 1700-line structure module. They communicate via `self.plugin.builder` / `self.plugin.structure` and share the canonical `self.plugin._sync_playlists` registry.

### Decision 10: Import dependency DAG

Imports form a strict DAG: `utils ← {media_map, timeline_build, playback_sync, display_sync, structure_sync, annotation_sync} ← ori_sync_plugin`. No controller imports another controller at module top level; cross-controller calls resolve at runtime via `self.plugin.<sibling>`. `media_map` may be imported by `ori_sync_plugin` for instantiation but is referenced by siblings only through the back-reference, keeping the graph acyclic.

## Risks / Trade-offs

- **Threading invariant silently broken** → A method moved into a controller could end up invoked from the wrong thread (e.g. an `_on_*` handler that previously inlined work now calling a controller method that touches the manager directly). Mitigation: keep `_on_*` handlers as thin enqueue/delegate shims on the plugin; audit every moved method for whether it ran on the xStudio thread or poll thread pre-split and preserve that; validate with `sync_test/` two-client integration runs.
- **Large state-migration call-site sweep** → ~60 attributes move; missed `self.x` → `self.plugin.x` rewrites cause `AttributeError` at runtime, not import time. Mitigation: migrate one domain at a time, grep each moved attribute name to zero remaining bare `self.` references in the wrong class, run integration tests after each domain.
- **Relative-import / package-load divergence from RV** → If the installed xStudio plugin path loads `ori_sync_plugin.py` as a top-level module rather than as part of the `ori_sync` package, relative imports would fail. Mitigation: confirm the install/load path treats the directory as a package (the existing `__init__.py` and its relative `from .ori_sync_plugin import ...` already imply this); smoke-test plugin load in xStudio before considering the change done.
- **Suppression-guard races during partial migration** → Moving annotation/structure state while the suppression guards stay on the plugin must keep read/write ordering identical. Mitigation: do not change any guard read/write logic — only relocate the fields it does not own; guards remain `self.plugin.*` everywhere.
- **Circular imports** → media_map referenced during other controllers' construction. Mitigation: instantiate media_map first; siblings store only `self.plugin` and dereference `self.plugin.media` lazily at call time, never at import or construction time.

## Migration Plan

1. Create `utils.py`; move stateless helpers; switch the entry-point to relative imports of them. Smoke-test plugin load.
2. Create `media_map.py` (`MediaMapController`); instantiate first; migrate mapping state and methods; sweep call-sites.
3. Create `timeline_build.py`, then `display_sync.py`, `playback_sync.py`, `structure_sync.py`, `annotation_sync.py` one at a time — each: move methods + domain state, instantiate in `__init__`, rewrite `self.` references, run `sync_test/` after each.
4. Slim `ori_sync_plugin.py` to entry-point + dispatch tables + thin handlers; confirm `__init__.py` re-exports still resolve.
5. Full two-client `sync_test/` regression; xStudio interactive smoke test.

**Rollback:** the change is confined to one package directory; reverting the commit restores the single file. No data or protocol migration is involved.

## Open Questions

None blocking. The one item to confirm during implementation (not a design decision) is that the installed xStudio plugin path loads the directory as a package so relative imports resolve — covered by the smoke test in step 1 of the migration plan.
