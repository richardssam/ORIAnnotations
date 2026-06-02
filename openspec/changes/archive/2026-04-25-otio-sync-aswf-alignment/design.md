# Design: ASWF OTIO Sync for RV

## Architecture
- **Network Layer**: `RabbitMQNetwork` using `pika` with a background consumer thread.
- **Protocol**: ASWF PRWG Synchronized Review Messaging.
- **Routing**: `SyncManager` routes commands to application handlers.
- **Payloads**: Serialized `SyncEvent` objects.

## RV Integration
- **Playback**: Syncs via `PLAYBACK_SETTINGS SET`.
- **Selection**: Syncs via `SELECTION SET`.
- **Annotations**: Syncs via `ANNOTATION STOKE_RELEASE`.
