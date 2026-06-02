# Proposal: OTIO Sync ASWF Alignment

## Goal
Align the OTIO synchronization protocol with the ASWF PRWG standard.

## Scope
- Replace UDP broadcasting with RabbitMQ.
- Use native OTIO `SyncEvent` schemas for all payloads.
- Implement command-based routing (`OTIO_SESSION`, `PLAYBACK_SETTINGS`, etc.).
- Support real-time playhead, selection, and annotation synchronization in OpenRV.
