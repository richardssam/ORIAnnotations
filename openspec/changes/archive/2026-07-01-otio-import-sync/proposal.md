## Why

When a `.otio` file is imported into OpenRV (File → Import → OTIO), RV's native `otio_reader` expands it into a Stack → Sequence → EDL node graph (a `tracks` RVStackGroup containing a `Video` RVSequenceGroup whose `Video_sequence.edl` holds the cut points), plus one RVSourceGroup per clip occurrence. The current sync plugin only understands the flat `defaultSequence → RVSourceGroup` model: it scans for RVSequenceGroups, lands on the empty `defaultSequence` (which holds only the blank `otioFile` movieproc placeholder), finds no EDL, and registers an empty timeline. The imported cuts never reach peers, so an OTIO timeline that xStudio renders correctly (xStudio shares the raw OTIO internally) appears empty or unstructured in RV-driven sync.

We want OTIO-imported timelines to sync with full structural fidelity — and to get future effect support (CDL, retimes) "for free" — by leveraging RV's own OTIO reader/writer rather than re-deriving structure by hand.

## What Changes

- **Detect OTIO-origin timelines.** A timeline whose root is the `tracks` RVStackGroup (produced by movieproc `otioFile` expansion) carrying `.otio.metadata` is OTIO-origin; record this as `metadata.sync.origin` so every peer routes it identically. Filter `defaultSequence`/`defaultStack` out of all sequence scans.
- **Route sync by edit type for OTIO-origin timelines:**
  - **Topology changes** (insert/remove/reorder clips, large re-edits) → whole-OTIO snapshot. Export via `otio_writer.create_timeline_from_node(<tracks stack>)`; apply on peers via `otio_reader.create_rv_node_from_otio(timeline)`. Reordering within an OTIO timeline is not separately tracked — it rides the snapshot push.
  - **Attribute changes on an existing clip** → incremental patch via the existing patch model, keyed by clip guid:
    - media swap → `clip.media_reference.target_url`
    - cut trim (in/out length) → `clip.source_range`, detected by a **new EDL-diff watcher** on the RVSequence node
    - CDL / color → existing color property channel, reading the CDL via RV's `cdlHook`
- **Unify identity across both models.** Inject `metadata.sync.guid` into the OTIO; it round-trips through RV's reader/writer (persisted as `.otio.metadata` on the node), so attribute patches target the correct clip on every peer and live annotations stay bound to clips.
- **Handle the async expansion race.** OTIO import is deferred (`after_progressive_loading` → `expand_sources`); the plugin must wait for the `tracks` RVStackGroup to materialize before snapshotting, replacing the crude retry-on-empty heuristic.
- **Leave the existing model unchanged** for native (non-OTIO) clip-list timelines — fine-grained `insert/move/remove_child` and first-class reordering — and keep **live annotation deltas** on the existing per-stroke path for both timeline classes.

## Capabilities

### New Capabilities
- `otio-import-sync`: Detection and sync of OTIO-imported timelines in the OpenRV plugin via RV's native `otio_reader`/`otio_writer`, including origin detection, the topology-vs-attribute routing rule, the whole-OTIO snapshot path, the EDL-diff cut-trim watcher, CDL-as-patch via RV's `cdlHook`, guid round-trip identity, and the expansion-race wait.

### Modified Capabilities
- `openrv-sync-plugin`: Sequence initialization and detection must recognize the RVStackGroup→RVSequenceGroup hierarchy, filter `defaultSequence`/`defaultStack`, and route OTIO-origin timelines to the snapshot model instead of the flat-init/EDL-reorder logic.
- `otio-sync-core`: Add a timeline origin marker (`metadata.sync.origin`) and a whole-OTIO "brute-force push" message/path for topology changes, distinct from the existing per-child patch messages.

## Impact

- **Code:** `rvplugin/ori_sync/sequence_sync.py` (detection, init, EDL-diff watcher, snapshot export/apply routing), `rvplugin/ori_sync/plugin.py` (dispatch for the new push message, expansion-race wait), `rvplugin/ori_sync/color_sync.py` (CDL read via `cdlHook`); `otio_sync_core` protocol messages (origin marker, whole-OTIO push).
- **RV dependency:** Relies on the `otio_reader` RV package's public entry points — `otio_writer.create_timeline_from_node`, `otio_reader.create_rv_node_from_otio`/`read_otio_file` — and its registered hooks (`annotation_hook`, `cdlHook`, `retimeExportHook`). Behavior is coupled to that package shipping with the target RV build.
- **Identity/round-trip:** Depends on RV's reader/writer preserving arbitrary `metadata` (`.otio.metadata`) on clips, not just on stacks/tracks — to be verified during design/implementation.
- **Out of scope:** No change to native-timeline sync, the live annotation delta path, or xStudio. Color output-space behavior (`color-pipeline-openrv`) is reused for CDL read but its requirements are unchanged.
