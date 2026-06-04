# Architecture & API

## What this project is

A toolkit for sharing review annotations between applications in real time. The primary integration is OpenRV, with an xStudio plugin also present. A web-based debug viewer (`sync_viewer`) can join any live session as a passive observer.

The core protocol is built on top of **OpenTimelineIO (OTIO)**: the shared state is an OTIO `Timeline`, mutations are broadcast as structured JSON messages over **RabbitMQ**, and every peer maintains a local replica of that timeline.

## System architecture

```text
OpenRV (master)
  └─ plugin.py
       └─ SyncManager ──► RabbitMQ fanout exchange
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
    sync_viewer/server.py     other RV instances    xStudio
      └─ SyncManager               └─ SyncManager   └─ ori_sync_plugin.py
         (passive observer)                               └─ SyncManager
```

- **Master election**: on join, peers broadcast `WHO_IS_MASTER`. The first to respond becomes master. If no response within ~2 s, the caller elects itself. Only one master per session.
- **State sync**: a new peer broadcasts `STATE_REQUEST`; master responds with a full `STATE_SNAPSHOT` (serialised OTIO + playback state). The new peer applies it and enters `STATE_SYNCED`.
- **Delta updates**: after sync, mutations (inserts, property changes, annotations) are broadcast as `OTIO_SESSION` / `ANNOTATION` messages. Every peer applies them locally.
- **Self-filtering**: `RabbitMQNetwork` discards any incoming message whose `source_guid` matches the local peer's `self_guid`, preventing echo-loops.

## `SyncManager` — key public API

```python
manager = SyncManager(session_id="my-session", network=RabbitMQNetwork(...))
manager.start_session()          # begin discovery
events = manager.tick()          # call repeatedly; returns list of app-level events
manager.timelines                # {guid: otio.schema.Timeline}
manager.object_map               # {guid: otio_object} flat index
manager.playback_state           # dict with current_time, playing, fps, …
manager.active_timeline_guid
manager.is_master
manager.broadcast_annotation(data)
```

`tick()` drives the receive/apply loop and returns events like `("playback_settings", data)`, `("annotation_stroke_release", data)`. Callers react to these events; the viewer's `_poll_loop` in `server.py` pushes state to WebSocket clients whenever `tick()` returns a non-empty list.
