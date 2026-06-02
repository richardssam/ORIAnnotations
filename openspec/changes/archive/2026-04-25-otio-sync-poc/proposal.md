## Why
OpenTimelineIO (OTIO) is traditionally used as a static exchange format for timelines. However, its structured serialization makes it an excellent candidate for real-time collaboration. This change aims to prove the viability of the "OTIO-Delta" sync protocol by developing a Python Proof of Concept (PoC) in OpenRV. This will allow two instances of OpenRV to synchronize timeline edits via delta payloads in real-time, relying entirely on the OTIO data structure.

## What Changes
- Develop a Python-based OpenRV plugin capable of sending and receiving OTIO-Delta events.
- Implement a mechanism to intercept or explicitly capture OTIO mutations (e.g., property changes, hierarchy modifications) in Python, working around the limitations of C++ binding observer patterns.
- Translate captured mutations into OTIO-Delta JSON payloads (e.g., `set_property`, `insert_child`) keyed by `target_uuid`.
- Create a lightweight networking layer (e.g., simple socket or local HTTP server) to broadcast payloads between OpenRV instances.
- Develop an ingestion system to receive payloads, apply them silently to the local OTIO graph, and trigger an OpenRV redraw/refresh without creating an echo loop.

## Capabilities

### New Capabilities
- `otio-sync-core`: The core protocol messaging layer and Python OTIO state-management wrapper.
- `openrv-sync-plugin`: The OpenRV plugin integrating the sync protocol with the RV interface and timeline.

### Modified Capabilities
- (None)

## Impact
- **Code**: Adds a new standalone OpenRV plugin directory and Python networking/sync modules.
- **APIs**: Introduces a custom wrapper or explicit API for modifying OTIO objects to emit delta events.
- **Systems**: OpenRV instances running the plugin will be able to synchronize their loaded OTIO timelines over a network connection.
