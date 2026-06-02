## Why

The current network syncing protocol uses a flat structure that does not conform to the ASWF OpenSpec standard format. This standard defines a nested message envelope with a top-level `schema` entry (e.g. `SYNC_REVIEW_1.0`) and nested `payload` dict containing a `command_schema`. We are aligning our payload structure with the documented standard to maintain interoperability and adherence to the wider OpenSpec ecosystem.

## What Changes

- Modify `otio_sync_core/manager.py` to dispatch and parse payloads using the nested ASWF format.
- Migrate historical JSON Lines `.jsonl` recordings to the new nested format using a new format converter script.
- Update `sync_recorder/recorder.py` and `sync_recorder/player.py` to parse `LiveSession.1` commands and appropriately dispatch nested payloads.
- Update corresponding unit tests in `test_sync_recorder.py` to mock the correct nested payloads.
- **BREAKING**: Any integrations expecting the old flat payload structure (e.g. `{"command": "SESSION", "event": "WHO_IS_MASTER"}`) will fail unless updated to expect `{"schema": "SYNC_REVIEW_1.0", "payload": {"command_schema": "LiveSession.1", ...}}`.

## Capabilities

### New Capabilities
- `schema-migration-converter`: A tool to convert legacy `.jsonl` files to the ASWF nested protocol structure format.

### Modified Capabilities
- `otio-sync-core`: Update network packet schemas to use nested standard instead of flat `event`/`command` structure.
- `ori-session-management`: Ensure `I_AM_MASTER` handshake correctly injects the session-wide schema descriptor `schema: "SYNC_REVIEW_1.0"`.

## Impact

- `otio_sync_core/manager.py` (Packet dispatching)
- `sync_recorder/recorder.py` and `sync_recorder/player.py`
- `.jsonl` recordings
- Unit tests mocking UDP payloads.
