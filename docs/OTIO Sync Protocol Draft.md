# **OpenTimelineIO Sync Protocol (OTIO-Delta)**

## **1. Overview**

This protocol defines a method for real-time synchronization of OTIO timelines between multiple clients. It uses a delta-based approach inspired by RFC 6902 (JSON Patch) but optimized for the OTIO object model.

## **2. Message Structure**

The protocol adopts the **ASWF PRWG Synchronized Review Messaging** standard. Messages are encapsulated in OTIO `SyncEvent` schemas and routed via a message broker (RabbitMQ).

### **Payload Format: SyncEvent**

All mutations are represented as subclasses of `otio.schemadef.SyncEvent`.

* **Property Changes**: Uses `SyncPlayback` or specialized attribute schemas.
* **Structural Changes**: Uses `OTIO_SESSION SET` or a generic `HierarchyEvent`.

### **Command Structure**

Following the `live_review.py` convention, messages are categorized by command and event:
* `OTIO_SESSION SET`: Broadcasts a full or partial timeline state.
* `PLAYBACK_SETTINGS SET`: Broadcasts playhead and playstate changes.
* `ANNOTATION PAINT_START`: Broadcasts the beginning of a stroke.

## **3. Core Actions**

### **set_property**

Used for modifying primitive values (strings, numbers, bools) or updating specific keys in metadata.

* **Path**: A slash-separated string. metadata/vendor/key.  
* **Value**: The new literal value.

### **insert_child (Adding Clips/Tracks)**

When adding a new OTIO object, the payload should contain the full serialized OTIO JSON of that object.

* **Path**: The index within the children array of a Stack or Track.  
* **Value**: A serialized OTIO object.

### **remove_child**

Removes an object from a collection.

* **Path**: children/index or target_uuid.

### **move_child**

Moves an object from one parent/index to another. Requires source_path and destination_path.

## **4. The Sync-Aware Engine (Procedural Layer)**

To implement this effectively, a specialized version of the OTIO library (or a high-level wrapper) should manage the lifecycle of these messages.

### **A. Auto-GUID Generation**

To avoid collisions and ensure objects can be tracked across clients:

* **Creation Hook**: Whenever a SerializableObject is instantiated within the sync-aware context, the library should automatically inject a unique identifier into metadata["sync"]["guid"] if one does not exist.  
* **Persistence**: This GUID must survive round-trips through standard OTIO adapters.

### **B. The Observer Pattern (Generating Patches)**

The library should implement an observer pattern on the OTIO C++ / Python objects:

* **Dirty Tracking (Transparent Proxy)**: Because native Python observer patterns (e.g., `__setattr__` monkey-patching) are highly restrictive on `pybind11` C++ bound objects, we use a **Transparent Proxy Wrapper** (`OTIOSyncProxy`). This proxy dynamically intercepts attribute assignments (like `clip.name = "New Name"`), mutates the underlying C++ object, and automatically generates an `otio_delta` message. This keeps the developer experience identical to native OTIO.  
* **Batching**: Support "Transaction" blocks to group multiple changes (like moving five clips) into a single sync message to reduce network overhead.

### **C. Ingestion & Callbacks (Applying Patches)**

When a remote patch arrives, the engine must apply it to the local model without triggering a loop (echo).

* **Silent Updates**: The engine needs a way to update the internal OTIO state "silently" (without triggering the local observers).  
* **App Callbacks**: Provide a registry where the host application (e.g., a Video Editor or Review Tool) can listen for specific changes:  
  * on_property_changed(target_uuid, path, new_value)  
  * on_hierarchy_changed(parent_uuid, action, child_uuid)

## **5. Addressing Challenges**

### **The UUID Requirement**

Relying solely on indices (like /children/5) is dangerous.

* **Recommendation**: Always use target_uuid as the primary anchor.

### **Conflict Resolution**

* **Last-Write-Wins (LWW)**: Use sync_timestamp for simple property conflicts.  
* **Causal Ordering**: Use a sequence number (vector clock) to ensure patches are applied in the correct order, even if they arrive out of sequence over the network.

## **6. Session Management & Late Joining**

In a decentralized peer-to-peer or broker-based system, new clients must be able to synchronize their state with the existing session.

### **A. Master Election (Eldest Peer)**
To provide a "Source of Truth," the first client to join a session promotes itself to **MASTER**. Subsequent clients identify the Master via a handshake:
1. **Discovery**: New Client broadcasts `SESSION WHO_IS_MASTER`.
2. **Response**: Master responds with `SESSION I_AM_MASTER`.
3. **Promotion**: If no response is received within a timeout (e.g., 2s), the New Client becomes the Master.

### **B. Full State Snapshot**
When a New Client identifies a Master, it requests the full current state:
*   **Request**: `SESSION STATE_REQUEST`.
*   **Snapshot**: Master sends a `SESSION STATE_SNAPSHOT` containing the full OTIO-JSON of the current timeline, current playback state (frame, play/pause), and active selection.

### **C. Lossless Join (Buffering)**
To prevent data loss during the transfer of large snapshots, New Clients utilize a **Buffering Strategy**:
1.  **Buffer**: Immediately upon joining, the client begins queuing all incoming `OTIO_SESSION` and `PLAYBACK_SETTINGS` events.
2.  **Apply**: The client applies the full `STATE_SNAPSHOT` when it arrives.
3.  **Replay**: The client replays the buffered events that were broadcast *after* the snapshot was captured, ensuring the late-joiner is perfectly in sync with the live stream.

## **7. Proposed OTIO C++ Core Enhancements**

While the current Python Proxy implementation works for a POC, moving synchronization logic into the OTIO C++ core would provide significant performance and stability benefits.

### **A. Native GUID Support**
Moving the `sync:guid` property into the base `SerializableObject` class (C++) would ensure that every object has a globally unique, immutable ID from the moment of instantiation.
*   **Benefit**: Eliminates the need for manual GUID injection and ensures consistency across all language bindings (Python, C++, Swift).

### **B. Native Observer/Dirty Flag API**
Implementing a native C++ observer pattern would allow the core to track mutations at the point of assignment.
*   **Feature**: `SerializableObject::on_changed(callback)` or a `dirty` bitmask.
*   **Benefit**: Removes the need for expensive Python-level `OTIOSyncProxy` wrappers and allows the library to generate deltas directly from C++ mutations.

### **C. Partial/Delta Serialization**
Extend the OTIO serialization API to support partial payloads.
*   **Feature**: `obj.to_json_delta(path, value)` and `obj.apply_delta(json_delta)`.
*   **Benefit**: Standardizes the "Patch" format within the library, making it easier for third-party integrations to support synchronization.

### **D. Mutation Transactions**
Add a Transaction API to the C++ core to group related mutations.
*   **Feature**: `Timeline.begin_transaction()` / `Timeline.end_transaction()`.
*   **Benefit**: Allows complex operations (like a "ripple edit" or "move clips") to be broadcast as a single atomic network event, preventing intermediate (broken) states from being synced.

## **8. Implementation Strategy**

1. **Instrument the OTIO Map**: Use the Transparent Python Proxy (`OTIOSyncProxy`) wrapper.
2. **Networking (Production)**: Use **RabbitMQ** with a fanout exchange per session. 
3. **The "Master" Model**: Designation of an eldest peer as the state authority.
4. **Serialization**: Use `otio.adapters.write_to_string(obj, 'otio_json')` for all payloads.
