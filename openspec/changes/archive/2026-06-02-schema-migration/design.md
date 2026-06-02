## Context

The OTIO sync core was previously sending raw JSON dictionaries formatted as `{"command": "...", "event": "...", "payload": {...}}`. While this worked for a simple proof-of-concept, the standard OpenSpec/ASWF definition requires a multi-layered envelope structure. This change refactors the core and supporting networking tools to properly adhere to the standard envelope schema.

## Goals / Non-Goals

**Goals:**
- Shift network communication in `otio_sync_core` and `sync_recorder` to use the standard envelope schema.
- Allow seamless migration of all pre-existing recorded `.jsonl` files to ensure debugging tools still work.
- Keep the internal application-level protocol logic intact (we still use the same logic, we just change the wrapper).

**Non-Goals:**
- We are not changing the content or inner functionality of the sync engine, just wrapping network packets in a new standardized envelope.
- We are not refactoring integration plugins (xStudio/OpenRV) as part of this direct change unless their message queues were heavily coupled to the flat structure.

## Decisions

- **Nested Dictionary Helper**: Instead of modifying every single `send_payload` call, we created a helper wrapper inside `SyncManager` that dynamically builds the nested dictionary wrapper. This isolates the change and reduces error surfaces.
- **Conversion Script**: We built a lightweight python conversion script (`convert_format.py`) instead of manually editing the JSONL histories or adding backwards compatibility into the core. Converting the recordings permanently is much safer.
- **Mock Handshake Hardcoding**: The recorder testing infrastructure needed its static UDP payload mocks updated instead of trying to make them format-agnostic, since the tests are meant to confirm strict network byte compliance.

## Risks / Trade-offs

- **Risk: Version Mismatches** -> If a peer is running an older flat-schema client, it will silently drop nested packets, causing the UI sync test to fail. Mitigation: This is acceptable during the migration phase, and all participants will be upgraded simultaneously.
