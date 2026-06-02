## Context

Annotations drawn in OpenRV during a live review session currently only exist dynamically in each client's RV state as deep hierarchical property graphs. They are not captured as a persistent entity in the underlying session data structure. As a result, when a user joins the live review late, they receive the current playback state and loaded clips but do not receive the annotations drawn earlier.

The goal is to leverage the OpenTimelineIO (OTIO) synchronization framework to include annotations as part of the persistent session state tree. Furthermore, we intend to store annotations in OTIO using a "flat view" structure, aligning with the schema used in the `ori_annotations_plugin.py` for exporting, rather than mirroring OpenRV's complex node-based properties representation over the wire.

## Goals / Non-Goals

**Goals:**
- Translate OpenRV drawing events into the `SyncEvent.PaintStart`, `SyncEvent.PaintPoints`, and related OTIO schema objects.
- Store these annotations within the OTIO session state so that joining clients receive the full annotation history upon syncing.
- Extract and apply annotations using a flat data model (as seen in `ori_annotations_plugin.py`), instead of propagating deep property updates directly over the wire.
- Integrate the syncing into the `openrv-sync-plugin` to intercept and emit annotation events.

**Non-Goals:**
- Syncing of `playbackSettings` inside the OTIO state.
- A complete rewrite of the OpenRV drawing engine. We only adapt the data for sync.

## Decisions

**1. OTIO Flat View Model for Annotations**
- **Decision**: Annotations will be translated into a series of OTIO SyncEvents (`PaintStart`, `PaintPoints`, `PaintEnd`, `TextAnnotation`) and stored in the OTIO state tree.
- **Rationale**: `ori_annotations_plugin.py` has already successfully prototyped exporting annotations via the flat view model (`SyncEvent`s). By keeping annotations as a list of discrete drawing events rather than deep property updates (`node.pen:N:F:user.points`), the data remains clean, standardized, and easily re-applied on client machines upon joining.

**2. State Sync vs Action Broadcasting**
- **Decision**: Drawing actions will continue to be broadcast as live events (e.g., `STROKE_RELEASE`) but they will ALSO be appended to the master OTIO state so latecomers can download the full snapshot.
- **Rationale**: The `openrv_sync_plugin` already broadcasts annotation payloads via the network. We need to expand this so the Master also applies these events to its internal OTIO timeline structure.

**3. Integration in `openrv_sync_plugin`**
- **Decision**: Hook into the `_apply_annotation` method to apply incoming flat view annotations, and into `_broadcast_annotation` to emit the flat view format instead of raw RV properties.
- **Rationale**: This is the natural entry point for sync. We'll update the payload format to map cleanly to the flat structure and handle mapping them back to RV's nested structure on the receiving end.

## Risks / Trade-offs

- **[Risk] State Bloat**: A highly active session could accumulate thousands of paint point objects in the OTIO timeline, leading to large snapshot payloads for latecomers.
  - **Mitigation**: Standardize the payload format and avoid redundant updates. The current debounce mechanism in the sync plugin already helps.
- **[Risk] Frame Association**: OpenRV maps annotations strictly to nodes and frames. 
  - **Mitigation**: Ensure that the payloads securely reference the media UUID, frame, and original source node so that annotations can be correctly mapped during `_apply_annotation`.
