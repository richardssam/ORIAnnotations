## Context

The `otio-sync-poc` established the transport layer (`UDPNetwork`), the `SyncManager` with `OTIOSyncProxy`, and the `set_property` delta action. This demo builds the interactive layer on top — an OpenRV plugin with a menu that lets a user add media to a shared OTIO timeline and observe it appear live in a second RV window.

Both instances run on the same machine, sharing the local subnet broadcast. Media paths are accessible to both instances. There is no server; the protocol is peer-to-peer broadcast.

## Goals / Non-Goals

**Goals:**
- Add an "OTIO Sync" menu to the OpenRV plugin
- Add `insert_child` action to `SyncManager` (structural mutation support)
- Bootstrap both instances with a shared well-known Track UUID at startup so receivers can resolve the insertion target
- On the sender: open a file dialog, create an `otio.schema.Clip`, insert it into the tracked timeline, and also call `rv.commands.addSource()` locally
- On the receiver: apply the `insert_child` patch and call `rv.commands.addSource()` so the clip is visible in RV

**Non-Goals:**
- Multi-machine networking
- Conflict resolution or ordering guarantees
- Playback synchronisation
- Persistence of the OTIO timeline across restarts

## Decisions

### D1: Shared Well-Known Track UUID
Both instances stamp Track[0] with the hardcoded constant `SYNC_DEMO_TRACK_UUID = "otio-sync-demo-track-0"` at plugin startup. This avoids needing a "hello/join" handshake to exchange the initial structure.

**Alternatives considered**: Broadcasting the full initial timeline on startup. Rejected because it introduces a race condition if the second instance starts late.

### D2: `insert_child` Payload Format
The delta payload for `insert_child` carries:
- `parent_uuid`: the UUID of the parent container (the Track)
- `index`: integer position to insert at (-1 = append)
- `child_json`: the full OTIO-JSON serialisation of the new child object

This is self-contained — the receiver does not need to request any additional data.

### D3: RV Source Loading on Both Sides
After inserting the clip into the OTIO timeline, both sender and receiver call `rv.commands.addSource(path)` independently. This keeps the RV session and the OTIO data structure in sync on both ends.

## Risks / Trade-offs

- **Race on startup**: If Instance A sends an `insert_child` before Instance B has registered its Track with the same UUID, the patch will be silently dropped. Mitigation: document the startup sequence (launch B before A, or add a 2-second delay before the menu becomes active).
- **OTIO JSON round-trip**: `otio.adapters.write_to_string` includes metadata including the sync GUID. The receiver must call `_ensure_guid_and_map` on the deserialised clip after inserting it to keep the object map current.
- **`rv.commands.addSource` availability**: Only available inside an RV process. The plugin already guards against missing `rv` module on import.
