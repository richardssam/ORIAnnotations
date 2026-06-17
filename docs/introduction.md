# OTIO Sync Protocol

This project synchronises live review sessions (playback, selection, and
annotations) between hosts such as OpenRV and xStudio. It is built on two
layers:

1. **The transport protocol** — typed *protocol messages* that are wrapped in a
   small envelope and fanned out to every peer over RabbitMQ.
2. **OTIO add-ons** — custom OpenTimelineIO schemas that let a timeline carry
   review/annotation data and discrete sync events.

Auto-generated API reference (built with `make html` in `docs/`):

- [`otio_sync_core` API](html/otio_sync_core.html) — the core sync library
  (manager, protocol messages, network backends, proxy, patcher, colour, and
  annotation codec).
- [`SyncEvent` / OTIO add-ons API](html/ori_event_plugin.html) — the custom
  OpenTimelineIO schemas (`SyncEvent` and its subclasses).


---

## Protocol messages

The transport layer is defined by typed
[`ProtocolMessage`](../python/otio_sync_core/protocol_messages.py) classes. Each
class is the single source of truth for one message: its `SCHEMA`, its `EVENT`,
and the shape of its `payload`. Messages are pure data — they implement
`to_payload()` / `from_payload()` and register themselves on
`(SCHEMA, EVENT)` so the receive-side dispatcher cannot drift from the
definitions.

Examples grouped by family:

| Family (`SCHEMA`) | Messages (`EVENT`) |
| --- | --- |
| `LiveSession.1` | `WHO_IS_MASTER`, `I_AM_MASTER`, `STATE_REQUEST`, `STATE_SNAPSHOT`, `NEW_PRESENTER`, `NEW_PARTICIPANT`, `SHARED_KEY_REQUEST`, `SHARED_KEY_RESPONSE` |
| `TIMELINE_1.0` | `ADD_TIMELINE`, `RENAME_TIMELINE` |
| `PLAYBACK_SETTINGS_1.0` / `DISPLAY_SETTINGS_1.0` | `SET` |
| `SELECTION_1.0` | `SET` |
| `Annotation.1` | `PARTIAL` |
| `OTIO_SESSION_1.0` | `SET_PROPERTY`, `INSERT_CHILD`, `MOVE_CHILD`, `REMOVE_CHILD`, `REPLACE_ANNOTATION_COMMANDS` |

---

## How a message is wrapped and sent

When the manager broadcasts a message it calls `_send_message()`
([`manager.py`](../python/otio_sync_core/manager.py)), which wraps the typed
message in an envelope. The envelope's `command_schema`, `command.event`, and
`command.payload` come straight from the message class:

```json
{
  "session": "default_session",
  "source_guid": "9bf2-4cd6-...-786d",
  "payload": {
    "command_schema": "SELECTION_1.0",
    "command": {
      "event": "SET",
      "payload": {
        "clip_guid": "abc123",
        "view_mode": "source",
        "sync_timestamp": 1747123456.789
      }
    }
  }
}
```

- `session` — scopes the message to one review session.
- `source_guid` — the sending peer; receivers discard their own messages.
- `payload.command_schema` + `payload.command.event` — the dispatch key
  `(SCHEMA, EVENT)` that maps back to a `ProtocolMessage` class.
- `payload.command.payload` — the result of `msg.to_payload()`.

One message (`I_AM_MASTER`) also sets a legacy top-level `"schema"` key
(`SYNC_REVIEW_1.0`) via its `ENVELOPE_SCHEMA`, for compatibility with older
peers.

### Onto RabbitMQ

The envelope is handed to the network backend
([`rabbitmq_network.py`](../python/otio_sync_core/rabbitmq_network.py)), which:

1. JSON-encodes it: `json.dumps(envelope).encode("utf-8")`.
2. Publishes it to a **fanout exchange** named `sync_session_<session_id>` with
   an empty routing key.

Because the exchange is a fanout, every peer that has bound an (exclusive,
auto-named) queue to that exchange receives every message. On receipt a peer
decodes the JSON, ignores anything from its own `source_guid`, looks up the
`(command_schema, event)` pair, reconstructs the message via
`from_payload()`, and dispatches it to the registered handler.

So the full path is:

```text
ProtocolMessage  ──to_payload()──▶  envelope dict  ──json.dumps──▶
    fanout exchange "sync_session_<id>"  ──▶  every peer's queue  ──▶
        from_payload()  ──▶  handler
```


---

## OTIO add-ons

OpenTimelineIO is extended through a **plugin manifest**
([`otio_event_plugin/plugin_manifest.json`](../otio_event_plugin/plugin_manifest.json)),
which registers a `SchemaDef` pointing at
[`schemadefs/SyncEvent.py`](../otio_event_plugin/schemadefs/SyncEvent.py). When
this plugin is on `OTIO_PLUGIN_MANIFEST_PATH`, OTIO can read and write these
types natively — they round-trip through `otio_json` like any built-in schema.

There are two kinds of add-on:

### `AnnotationEffect`

An `otio.schema.Effect` subclass (`AnnotationEffect.1`) attached to a clip. It
holds the persisted annotation layers/commands for that clip — i.e. the
durable record of what was drawn, stored *inside* the timeline so it survives
export and re-import.

### `SyncEvent` and its subclasses

`SyncEvent` is the base `SerializableObject` for discrete, timestamped events.
Subclasses describe a single thing that happened in a review, for example:

| Schema | Purpose |
| --- | --- |
| `PaintStart.1` | Opens a stroke (brush, colour, width, uuid) |
| `PaintPoint.1` | Appends a batch of points to the active stroke |
| `PaintEnd.1` | Closes the active stroke |
| `TextAnnotation.1` | A positioned text label with font metadata |
| `Play.1`, `SetCurrentFrame.1` | Playback state changes |

These are the OTIO-native representation of annotation content. They are stored
in the timeline (under an `AnnotationEffect`) and are also embedded inside some
transport messages when annotation data needs to travel between peers.

> Note: session/handshake concerns (presenter, participant, shared key) are
> **not** OTIO add-ons — they live on the transport layer as protocol messages
> (see below).
