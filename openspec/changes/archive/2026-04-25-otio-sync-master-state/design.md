# Design: Master-Client State Replication

## State Machine

### Client States
- `NONE`: Not connected.
- `DISCOVERING`: Connected to exchange, asking `WHO_IS_MASTER`.
- `JOINING`: Master found, `STATE_REQUEST` sent, buffering all incoming `OTIO_SESSION` and `PLAYBACK_SETTINGS` events.
- `SYNCED`: Snapshot applied, buffer replayed, normal operation.

## Handshake Sequence

1. **Discovery**:
   - New client broadcasts `SESSION WHO_IS_MASTER`.
   - Master (if exists) responds with `SESSION I_AM_MASTER`.
   - If no response after 500ms, New client becomes Master.

2. **Snapshot**:
   - Master generates `STATE_SNAPSHOT` containing:
     - `otio_json`: Full timeline serialization.
     - `playback`: Current frame, play state, fps.
     - `selection`: Active node list.
     - `last_message_guid`: GUID of the last processed event.

3. **Reconciliation**:
   - Client applies `otio_json` (rebuilds RV session).
   - Client replays `temp_buffer` for all messages with `timestamp > snapshot_timestamp` (or GUID index).

## Data Integrity
- Use `zlib` compression for the `otio_json` field if size > 1MB.
- Ensure `SyncManager` is in `_is_syncing` mode during the entire snapshot application to prevent echoing the initial load.
