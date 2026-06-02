## Context
We want to synchronize OpenTimelineIO (OTIO) timelines across multiple instances of OpenRV in real-time. The specification outlines an `OTIO-Delta` protocol (JSON patch-like) for communicating mutations. The main challenge is that the modern `opentimelineio` library is a C++ library exposed to Python via `pybind11`, making it extremely difficult to monkey-patch property setters natively in Python to create an automatic observer pattern (i.e. "dirty tracking").

## Goals / Non-Goals

**Goals:**
- Prove that the OTIO-Delta JSON payload structure can successfully mutate a remote timeline.
- Implement an OpenRV plugin that acts as a sender and receiver of these sync events.
- Demonstrate updating properties (e.g. metadata, name) and hierarchy (adding/removing clips).

**Non-Goals:**
- Complex conflict resolution (e.g., Vector clocks or strict Last-Write-Wins timestamps). For the PoC, the last applied payload wins.
- A production-grade relay server. We will use a basic peer-to-peer or local broadcast socket.
- Full coverage of every possible OTIO mutation. We will focus on `set_property` and `insert_child` first.

## Decisions

1. **OTIO State Tracking (The Observer Problem)**
   - *Decision*: We will implement an Explicit Sync Wrapper (`SyncManager`).
   - *Rationale*: Because Python cannot easily intercept C++ bound property setters (e.g. `clip.name = "new"`), we will enforce that mutations go through the `SyncManager`. For example: `SyncManager.set_property(target_uuid, "name", "New Name")`. This explicitly updates the local OTIO object and guarantees the delta payload is generated and broadcasted.
   - *Alternative Considered*: Monkey-patching `otio.core.SerializableObject.__setattr__`. *Rejected* because pybind11 objects do not support arbitrary `__setattr__` overriding predictably.

2. **Networking Layer**
   - *Decision*: A lightweight UDP broadcast socket or a simple TCP socket thread running inside the OpenRV plugin.
   - *Rationale*: For a PoC, we do not want to set up an external database or Redis server. A local UDP broadcast allows multiple OpenRV instances on the same local network to discover and send JSON payloads to each other without configuration.

3. **OpenRV Integration & Echo Loop Prevention**
   - *Decision*: The plugin will hook into OpenRV's native event system.
   - *Rationale*: When a user interacts with RV, the plugin translates it to an OTIO mutation via `SyncManager`, which broadcasts it. When receiving a payload from the network, we must apply the mutation silently. We will use a flag `_is_syncing = True` while applying incoming patches to ignore OpenRV callbacks and prevent infinite echo loops.
   - *Asynchronous Polling*: OpenRV's embedded Python doesn't play perfectly with standard `asyncio`. We will use `rv.commands.addTimer` to periodically poll the network socket for incoming payloads to ensure the RV UI does not freeze.

## Risks / Trade-offs
- **[Risk] Threading/Event Loop Freezes:** Running a blocking socket `recv()` in OpenRV will freeze the UI. 
  - *Mitigation*: The socket must be non-blocking, and polled via RV's internal timer events, or run in a background `threading.Thread` that pushes payloads into a thread-safe Queue, which the main RV thread reads from.
- **[Risk] Missing GUIDs:** Standard OTIO files do not have `sync_guid` metadata by default.
  - *Mitigation*: The `SyncManager` will perform an initial pass over the loaded timeline and automatically assign a `metadata["sync"]["guid"]` to every object that lacks one before starting the session.
