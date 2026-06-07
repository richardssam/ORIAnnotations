## Context

The OpenRV sync plugin (`rvplugin/ori_sync/plugin.py`) is a 3063-line single-file implementation of `rv.rvtypes.MinorMode`. It handles five distinct responsibilities: sequence/timeline structure management, playback synchronisation, display state synchronisation, annotation rendering, and session/menu lifecycle. These concerns are currently interwoven in one class (`OpenRVSyncPlugin`) with shared mutable state accessed freely across all concerns.

The file is packaged into an `.rvpkg` zip by `makepackage.csh`. OpenRV loads the single entry-point `plugin.py` declared in `PACKAGE`'s `modes:` section. Any sibling `.py` files in the same directory are importable via standard Python `import` because the package install directory is on `sys.path`.

Key constraints from [openrv_constraints.md](file:///Users/sam/git/ORIAnnotations/docs/openrv_constraints.md):
- All RV API calls must happen on the main thread (Qt event loop).
- The `_rv_updating` flag is a reentrancy guard used across all controllers — when True, outgoing broadcasts are suppressed to prevent echo loops from graph-state-change events fired by our own property writes.
- The `sync_manager` instance is the single source of truth for OTIO state and is accessed by every concern.

## Goals / Non-Goals

**Goals:**
- Split `plugin.py` into 6 files with clear single responsibilities.
- Maintain identical runtime behaviour — no protocol, API, or user-visible changes.
- Reduce inter-concern coupling so that each module can be understood and tested independently.
- Keep all files flat in `rvplugin/ori_sync/` (no sub-packages) for `.rvpkg` compatibility.
- Update `makepackage.csh` to include new files.
- Fix the broken `test_openrv_annotations.py` test suite.

**Non-Goals:**
- Introducing an `__init__.py` or Python package structure (RV's package loader does not support sub-packages in mode plugins).
- Changing the public API surface of `OpenRVSyncPlugin` (menu callbacks, `createMode()`, event handler signatures).
- Refactoring `otio_sync_core` or any code outside `rvplugin/ori_sync/`.
- Adding new unit tests beyond fixing the existing broken ones.
- Performance optimisation.

## Decisions

### Decision 1: Delegated controller pattern, not inheritance or mixins

Each domain (sequence, playback, display, annotation) gets its own controller class. The main `OpenRVSyncPlugin` instantiates them in `__init__` and delegates to them from event handlers and `_handle_action`.

**Alternative considered: Mixins** — Multiple inheritance with `SequenceSyncMixin`, `PlaybackSyncMixin`, etc. Rejected because mixin-based classes share `self` and all state indiscriminately, which defeats the goal of clear ownership. Debugging name collisions in a 5-way diamond is also painful.

**Alternative considered: Standalone functions** — Pure functions receiving plugin state as arguments. Rejected because the annotation and sequence controllers maintain significant local state (pending strokes, partial pen nodes, settle timers) that belongs together.

### Decision 2: Controllers receive a back-reference to the plugin, not individual attributes

Each controller's `__init__` takes a single `plugin` argument and stores it as `self.plugin`. This provides access to `self.plugin.sync_manager`, `self.plugin._rv_updating`, and cross-controller calls like `self.plugin.sequence_sync.path_to_source_group_map()`.

**Rationale**: The alternative — passing individual attributes or accessor functions — would create a verbose constructor signature that changes every time a new shared field is added. The back-reference is simple, explicit, and mirrors how Qt child widgets reference their parent.

**Trade-off**: Controllers are not decoupled from the plugin type at the interface level. This is acceptable because they exist solely to serve this one plugin and will never be reused elsewhere.

### Decision 3: `_rv_updating` and `sync_manager` stay on the plugin

The `_rv_updating` reentrancy guard and `sync_manager` reference are cross-cutting concerns accessed by every controller. Duplicating them per-controller would introduce synchronisation bugs. They remain as attributes of `OpenRVSyncPlugin`, accessed via `self.plugin._rv_updating` and `self.plugin.sync_manager`.

Controllers that need to set `_rv_updating` (annotation and sequence controllers do this frequently) access it directly. No getter/setter abstraction is added — it would add boilerplate without safety benefit, since all code runs on the same thread.

### Decision 4: Module layout

```
rvplugin/ori_sync/
├── plugin.py            ~350 lines  (MinorMode, menus, poll loop, session lifecycle)
├── utils.py             ~120 lines  (logger, warnings, path utils, static helpers)
├── sequence_sync.py     ~550 lines  (SequenceSyncController)
├── playback_sync.py     ~350 lines  (PlaybackSyncController)
├── display_sync.py      ~250 lines  (DisplaySyncController)
└── annotation_sync.py   ~1400 lines (AnnotationSyncController)
```

`annotation_sync.py` is the largest because the annotation domain is inherently complex (partial strokes, text vs pen, replace vs insert, UUID deduplication). Further splitting it (e.g. `annotation_render.py` + `annotation_broadcast.py`) was considered but rejected: the send and receive paths share substantial helper code (`_construct_annotation_events`, `_find_paint_node_for_media`, `_resolve_media_path_for_paint_node`), and splitting would force those helpers into a third file or duplicate them.

### Decision 5: `utils.py` contains module-level functions, not a class

The logger (`_log`, `_log_exc`), warning popup helpers, `_parse_ori_session`, `_media_path`, and `_is_media_track` are stateless utilities. They are defined as module-level functions in `utils.py` and imported by the other modules.

`_media_path` and `_is_media_track` are currently `@staticmethod`s on the plugin class. They move to `utils.py` as plain functions. All call sites change from `self._media_path(...)` to `_media_path(...)` (or `from utils import _media_path`).

### Decision 6: Event handler registration stays in `plugin.py`

The `init()` call in `__init__` registers RV event handlers like `("play-start", self.on_rv_play_start, ...)`. These thin methods stay on `OpenRVSyncPlugin` and delegate immediately:

```python
def on_rv_play_start(self, event):
    self.playback.broadcast_playback()
    event.reject()
```

**Rationale**: RV's `init()` requires bound methods on the MinorMode instance. Passing controller methods directly (e.g. `self.playback.on_play_start`) would work but makes the event registration table harder to read and would require controllers to handle `event.reject()` — an RV-specific concern that belongs in the entry-point.

### Decision 7: `_handle_action` dispatcher stays in `plugin.py`

The `_handle_action` method is a routing table that maps sync action strings to apply methods. It stays in `plugin.py` and dispatches to the appropriate controller:

```python
def _handle_action(self, action, data):
    if action == "playback_settings":
        self.playback.apply_playback(data)
    elif action == "display_settings":
        self.display.apply_display_state(data)
    elif action == "annotation_commands_added":
        _merged, delta = data
        self.annotation.apply_annotation_render(delta)
    # ...
```

This keeps the routing logic in one visible place rather than scattering it across controllers.

### Decision 8: Test file fix strategy

`test_openrv_annotations.py` currently patches `OpenRVSyncPlugin._setup_sync` which no longer exists (it was refactored into inline `connect_to_session` logic during the session-management change). The fix:

1. Remove the `patch.object(OpenRVSyncPlugin, '_setup_sync')` context managers.
2. Mock `os.environ.get` to suppress `ORI_SESSION` auto-connect in tests.
3. Update import paths if any tested methods move to controller modules (e.g. `_apply_annotation` → `AnnotationSyncController.apply_annotation`).

### Decision 9: Packaging — only `makepackage.csh` changes

Add the 5 new `.py` files to the `zip` command in `makepackage.csh`. No changes to `PACKAGE` (the `modes:` section only lists entry-points, not helper modules) or `reinstall.csh`.

## Risks / Trade-offs

**Circular imports** → All controllers import from `utils.py` but not from each other. `plugin.py` imports all controllers. This forms a strict DAG (`utils` ← `{controllers}` ← `plugin`) with no cycles. Cross-controller calls go through `self.plugin.other_controller`, which is resolved at runtime, not import time.

**Annotation controller is still large (~1400 lines)** → Accepted trade-off. The annotation domain is inherently complex and tightly coupled internally. Splitting it further would create more files than it saves in cognitive load, and would force shared helpers into a utility grab-bag.

**Back-reference creates tight coupling** → Controllers cannot be instantiated without a plugin instance, which makes isolated unit testing require a mock plugin. Mitigated: the mock only needs `sync_manager`, `_rv_updating`, and sibling controller references — all easily mockable. The existing tests already mock the entire `rv.commands` module, so this is not a new burden.

**`_rv_updating` accessed from multiple controllers without synchronisation** → Safe because all code runs on the Qt main thread (single-threaded event loop). No mutex needed. This constraint is documented in `openrv_constraints.md`.

## Open Questions

None — this is a mechanical refactoring with well-understood boundaries. All decisions are made.
