# **OpenTimelineIO Sync Protocol (OTIO-Delta)**

## **1. Overview**

This protocol defines a method for real-time synchronization of OTIO timelines between multiple clients. It uses a delta-based approach inspired by RFC 6902 (JSON Patch) but optimized for the OTIO object model.

## **2. Message Structure**

The protocol adopts the **ASWF PRWG Synchronized Review Messaging** standard. Messages are encapsulated in OTIO `SyncEvent` schemas and routed via a message broker (RabbitMQ).

### **Architecture Layers**

Three roles collaborate to handle messaging:

* **`RabbitMQNetwork`**: Manages the pika connection and background consumer thread. All session messages are broadcast over a fanout exchange keyed by session name.
* **`SyncManager`**: Routes incoming commands to application-level handlers and drives outgoing delta generation. Acts as the single point of truth for session state on the Master peer.
* **`SyncEvent` Payloads**: All mutations are represented as OTIO schema objects that subclass `otio.schemadef.SyncEvent` and are serialized as OTIO JSON.

### **Command Structure**

Following the ASWF PRWG convention, messages are categorized by command and event:

* `OTIO_SESSION SET`: Broadcasts a full or partial timeline state.
* `PLAYBACK_SETTINGS SET`: Broadcasts playhead and playstate changes.
* `SELECTION SET`: Broadcasts the active node/clip selection.
* `ANNOTATION STROKE_RELEASE`: Broadcasts a completed paint stroke in flat `SyncEvent` format.
* `SESSION WHO_IS_MASTER` / `SESSION I_AM_MASTER`: Master election handshake.
* `SESSION STATE_REQUEST` / `SESSION STATE_SNAPSHOT`: Late-join snapshot protocol.

## **3. Annotation Sync**

### **3.1 The Flat View Model**

Annotations are represented as a sequence of discrete `SyncEvent` objects rather than mirroring OpenRV's deep node-based property graph. The four event types that make up a stroke are:

* **`SyncEvent.PaintStart`**: Declares the beginning of a stroke, including pen color, width, and the media UUID + frame it belongs to.
* **`SyncEvent.PaintPoints`**: Carries a batch of `(x, y, size)` triples appended to the active stroke.
* **`SyncEvent.PaintEnd`**: Closes the stroke.
* **`SyncEvent.TextAnnotation`**: Carries a positioned text label with font metadata.

This flat schema matches the format already used by `ori_annotations_plugin.py` for export, so the same read/write logic serves both live sync and OTIO file persistence.

### **3.2 Broadcasting Strokes**

When a user completes a paint stroke in OpenRV, the `openrv_sync_plugin` intercepts the draw event, extracts stroke properties from RV's property graph, translates them into the flat `SyncEvent` sequence, and broadcasts an `ANNOTATION STROKE_RELEASE` message.

### **3.3 Applying Received Strokes**

When a peer receives an `ANNOTATION STROKE_RELEASE` message, it translates the flat `SyncEvent` data back into OpenRV's node-based property graph and triggers a redraw. The mapping uses the `media_uuid` and `frame` fields carried in `PaintStart` to anchor the stroke to the correct source clip.

### **3.4 Annotation Persistence in State**

Drawing events are broadcast live *and* appended to the Master's internal OTIO timeline so that late-joining peers receive the full annotation history. When the Master receives any annotation event it applies that event to its internal OTIO state tree before acknowledging. All subsequent `STATE_SNAPSHOT` payloads therefore include the complete annotation log.

## **4. Core Actions**

### **set_property**

Used for modifying primitive OTIO properties (strings, numbers, bools, `RationalTime`, `TimeRange`, etc.).

* **Target**: `sync_id` of the object — stable through reordering, unlike index-based paths.
* **Property name**: The OTIO property name (e.g. `"name"`, `"source_range"`).
* **Value**: The new value, serialized as OTIO JSON.

### **set_metadata**

A separate patch type for metadata sub-key mutations. Direct writes to `obj.metadata["x"] = y` are **not observed** by the patch system (see §8 — `AnyDictionary` gap). Metadata changes must be routed through an explicit helper to generate patches with sub-key granularity.

* **Target**: `sync_id` of the object.
* **Path**: A slash-separated sub-key path within the metadata dict, e.g. `"annotations/frame_1/strokes"`. Inspired by USD's property path addressing, which gives per-property granularity rather than replacing the whole metadata blob.
* **Value**: The new value at that path.

### **insert_child (Adding Clips/Tracks)**

When adding a new OTIO object, the payload should contain the full serialized OTIO JSON of that object.

* **Path**: The index within the children array of a Stack or Track.  
* **Value**: A serialized OTIO object.

In the OpenRV integration, `insert_child` is also paired with a local `rv.commands.addSource(path)` call so the media is loaded into the receiver's RV viewer immediately after the delta is applied.

### **remove_child**

Removes an object from a collection.

* **Path**: children/index or target_uuid.

### **move_child**

Moves an object from one parent/index to another. Requires source_path and destination_path.

## **5. The Sync-Aware Engine (Procedural Layer)**

To implement this effectively, a specialized version of the OTIO library (or a high-level wrapper) should manage the lifecycle of these messages.

### **A. Auto-GUID Generation**

To avoid collisions and ensure objects can be tracked across clients:

* **Creation Hook**: Whenever a SerializableObject is instantiated within the sync-aware context, the library should automatically inject a unique identifier into metadata["sync"]["guid"] if one does not exist.  
* **Persistence**: This GUID must survive round-trips through standard OTIO adapters.

### **B. The OTIOPatcher (Generating and Applying Patches)**

The OTIO C++ core now provides a native `MutationObserver` API, which supersedes the earlier `OTIOSyncProxy` transparent-proxy approach. `OTIOPatcher` is a `MutationObserver` subclass that serves as both the patch generator and patch applicator.

**Generation** — `OTIOPatcher` registers as an observer on the root timeline and receives two callbacks:
* `on_property_changed(obj, property_name)` — fires after any OTIO property is mutated, e.g. `clip.name = "New Name"`. The patcher captures the new value immediately and emits a `SetPropertyPatch`.
* `on_children_changed(composition, action, index, child)` — fires on insert, remove, or clear. Emits `InsertChildPatch`, `RemoveChildPatch`, or `ClearChildrenPatch`.

We need to explore whether the metadata sub-key changes can be observable in python (C++ may be more problematic), so we may need to require metadata mutations must be routed through `patcher.set_metadata(obj, path, value)`, which applies the change and emits a `SetMetadataPatch` directly.

**Transactions** — Multiple patches can be grouped into a `Transaction` using a context manager:
```python
with patcher.transaction("Draw annotation on frame 42"):
    patcher.set_metadata(clip, "annotations/frame_1/strokes", data)
    clip.name = "Annotation_42"
# → emits one Transaction containing both patches
```
A single mutation outside a `with` block auto-wraps into a single-patch transaction. Consumers (`SyncManager`, Raven, etc.) always receive `Transaction` objects — uniform interface regardless of patch count.

**Application** — `OTIOPatcher.apply(transaction)` executes each patch against the local OTIO graph. A re-entrancy guard (`_applying` flag) suppresses outgoing patch generation while applying an incoming transaction, preventing echo loops.

**Patch data model** (plain, JSON-serializable — readable by C++ tools):
* `SetPropertyPatch` — `target_id`, `property_name`, `new_value`, `old_value`
* `SetMetadataPatch` — `target_id`, `path`, `new_value`, `old_value`
* `InsertChildPatch` — `parent_id`, `index`, `child_json`
* `RemoveChildPatch` — `parent_id`, `index`, `child_id`, `child_json`
* `ClearChildrenPatch` — `parent_id`, `child_jsons`

`old_value` / `child_json` fields are included to support future undo/redo without requiring shadow state.

### **C. Ingestion & Callbacks (Applying Patches)**

When a remote patch arrives, the engine must apply it to the local model without triggering a loop (echo).

* **Silent Updates**: The engine needs a way to update the internal OTIO state "silently" (without triggering the local observers).  
* **App Callbacks**: Provide a registry where the host application (e.g., a Video Editor or Review Tool) can listen for specific changes:  
  * on_property_changed(target_uuid, path, new_value)  
  * on_hierarchy_changed(parent_uuid, action, child_uuid)

## **6. Addressing Challenges**

### **The UUID Requirement**

Relying solely on indices (like /children/5) is dangerous.

* **Recommendation**: Always use target_uuid as the primary anchor.

### **Conflict Resolution**

* **Last-Write-Wins (LWW)**: Use sync_timestamp for simple property conflicts.  
* **Causal Ordering**: Use a sequence number (vector clock) to ensure patches are applied in the correct order, even if they arrive out of sequence over the network.

## **7. Session Management & Late Joining**

In a decentralized peer-to-peer or broker-based system, new clients must be able to synchronize their state with the existing session.

### **Client State Machine**

Clients transition through four states:

| State | Description |
| --- | --- |
| `NONE` | Not connected to the session exchange. |
| `DISCOVERING` | Connected; `WHO_IS_MASTER` broadcast sent, awaiting response. |
| `JOINING` | Master identified; `STATE_REQUEST` sent; all incoming events buffered. |
| `SYNCED` | Snapshot applied, buffer replayed; normal delta processing. |

### **A. Master Election (Eldest Peer)**

To provide a "Source of Truth," the first client to join a session promotes itself to **MASTER**. Subsequent clients identify the Master via a handshake:

1. **Discovery**: New Client broadcasts `SESSION WHO_IS_MASTER`.
2. **Response**: Master responds with `SESSION I_AM_MASTER`.
3. **Promotion**: If no response is received within **500 ms**, the New Client becomes the Master.

### **B. Full State Snapshot**

When a New Client identifies a Master, it requests the full current state:

* **Request**: `SESSION STATE_REQUEST`.
* **Snapshot**: Master sends a `SESSION STATE_SNAPSHOT` containing:
  * `otio_json`: Full OTIO JSON of the current timeline, including all persisted annotation `SyncEvent` objects.
  * `playback`: Current frame, play/pause state, and FPS.
  * `selection`: Active node list.
  * `last_message_guid`: GUID of the last event processed by the Master, used to anchor buffer replay.

  If `otio_json` exceeds 1 MB it is compressed with `zlib` before transmission.

### **C. Lossless Join (Buffering)**

To prevent data loss during the transfer of large snapshots, New Clients utilize a **Buffering Strategy**:

1. **Buffer**: Immediately upon joining, the client begins queuing all incoming `OTIO_SESSION` and `PLAYBACK_SETTINGS` events.
2. **Apply**: The client applies the full `STATE_SNAPSHOT` when it arrives.
3. **Replay**: The client replays buffered events with a GUID index after `last_message_guid`, ensuring the late-joiner is perfectly in sync with the live stream.

## **8. OTIO C++ Core Enhancements**

### **A. Native GUID Support** ✓ Implemented

`sync_id` is now a first-class property on `SerializableObject` in the C++ core. It is generated lazily on first access and survives JSON round-trips. This eliminates the need for manual `metadata["sync"]["guid"]` injection.

### **B. Native MutationObserver API** ✓ Implemented

The C++ core now provides a `MutationObserver` base class with two callbacks:

* `on_property_changed(obj, property_name)` — fires after any OTIO property is mutated.
* `on_children_changed(composition, action, index, child)` — fires on structural changes (insert, remove, clear).

Observers are registered per-object via `add_observer()` / `remove_observer()`. This replaces the earlier `OTIOSyncProxy` Python wrapper approach.

### **C. Partial/Delta Serialization**

Extend the OTIO serialization API to support partial payloads.

* **Feature**: `obj.to_json_delta(path, value)` and `obj.apply_delta(json_delta)`.
* **Benefit**: Standardizes the patch format within the library, making it easier for third-party integrations to support synchronization.

### **D. Mutation Transactions**

Add a transaction API to the C++ core to group related mutations.

* **Feature**: `Timeline.begin_transaction()` / `Timeline.end_transaction()`.
* **Benefit**: Allows complex operations (like a ripple edit or clip move) to be broadcast as a single atomic event, preventing intermediate broken states from being synced.

### **E. AnyDictionary Sub-key Observation** ⚠️ Gap

The `MutationObserver` fires `on_property_changed(obj, "metadata")` when any metadata key changes, but provides no sub-key path or value. This is because `AnyDictionary` (the C++ type backing `obj.metadata`) has no observation mechanism of its own — it cannot notify its parent object which key changed or what the new value is.

The consequence is that a patch system cannot generate fine-grained `SetMetadataPatch` records purely from observation. Metadata mutations must be routed through an explicit API (`patcher.set_metadata(obj, path, value)`) to produce useful patches. Direct writes to `obj.metadata["x"] = y` are invisible to any observer.

**Comparison with USD**: USD's `TfNotice::ObjectsChanged` carries exact property paths (e.g. `/World/Cube.xformOp:translate`) including sub-keys, which is what makes efficient delta generation possible. OTIO's equivalent would require `AnyDictionary` to know its parent object and propagate keyed change events upward — a non-trivial structural change.

**In Python**: A proxy wrapper around `AnyDictionary` can intercept `__setitem__` calls and generate sub-key patches, but still requires explicit routing through the patcher rather than direct dict access.

**Recommended C++ enhancement**: `MutationObserver` should receive an optional sub-path and new value when metadata changes, consistent with how USD surfaces property-path-level granularity.

### **F. Recursive Observer Registration**

`add_observer()` registers on a single object only. Observing a full timeline currently requires traversing the entire tree and registering on every node, then re-registering on newly inserted subtrees via `on_children_changed`.

* **Recommended enhancement**: `add_observer(observer, recursive=true)` to register on a root and all descendants automatically, with new children auto-registered on insert.

## **9. Implementation Strategy**

1. **Patch generation**: Use `OTIOPatcher` (a `MutationObserver` subclass) attached to the root timeline. Route metadata mutations through `patcher.set_metadata()` explicitly.
2. **Networking (Production)**: Use **RabbitMQ** with a fanout exchange per session.
3. **The "Master" Model**: Designation of an eldest peer as the state authority.
4. **Serialization**: Use `otio.adapters.write_to_string(obj, 'otio_json')` for all payloads.
5. **C++ tools (e.g. Raven)**: Act as patch consumers — receive `Transaction` objects and apply them via the C++ OTIO API. No observation required on the consumer side.
