# OTIO Sync Protocol — v1.0 Proposal

## Summary

This document proposes a revised wire protocol for the OTIO Sync session, drawing from
the LiveReview (Autodesk) and ORIAnnotations (POC) implementations. The goals are:

- Create SyncManager per client, which stores sync status on each client, and provides a Observer model to allow local app to track changes. The sync status is stored in a OTIO structure, where changes are communicated to other clients via OTIO deltas.
- Replace the two-field `command`/`event` dispatch with a single `event` string
- Add protocol version negotiation at handshake time only
- Promote `timestamp` to the envelope (currently inconsistently placed in payloads)
- Add a graceful `session.leave` message (currently missing from both implementations)
- Keep the master election, delta buffering, and self-filtering from ORIAnnotations
- Keep the schema-versioning intent from LiveReview, scoped to the handshake
- Define a OTIO Patch framework inspired by the inspired by RFC 6902 (JSON Patch) standard. Allowing an OTIO structure to have incremental changes, rather than requiring the entire structure to be communicated regularly. This would initially be in python, but a C++ version could be developed.
- Express annotations as standard `insert_child` patches (Using the OTIO Patch framework) rather than RV-specific paint data.
- OTIO is removed from playback_settings, and instead we support multiple OTIO timelines, each with a guid, the active timelines in the `playback.set` event as `timeline_guid`. 

---

## Architecture

Three layers collaborate to handle messaging:

- **`RabbitMQNetwork`** — Manages the pika connection and background consumer thread.
  All session messages are broadcast over a fanout exchange keyed by `session_id`.
  Self-filtering is applied in the consumer callback: messages whose `source_guid`
  matches the local peer are silently discarded before being enqueued.

- **`SyncManager`** — Routes incoming messages to application-level handlers and drives
  outgoing delta generation. Acts as the single source of truth for session state on the
  master peer. Owns the `_object_map` (GUID → OTIO object) and the delta buffer used
  during `STATE_JOINING`.

- **`OTIOPatcher`** — A `Observer Mechanism` that serves as both patch generator
  and patch applicator. Registered on the root timeline; emits structured patch objects
  on every observed mutation. Incoming patches from remote peers are applied through
  `OTIOPatcher.apply()`, which sets a re-entrancy guard to suppress echo.

---

## Envelope

Every message on the wire shares a common envelope:

```json
{
  "event": "<namespace>.<action>",
  "session_id": "my-review-session",
  "source_guid": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": 1747123456.789,
  "payload": {}
}
```

| Field | Required | Notes |
|---|---|---|
| `event` | ✅ | Dot-namespaced. Replaces `command` + `event`. |
| `session_id` | ✅ | Scopes messages to a session. Peers ignore messages for other session IDs. |
| `source_guid` | ✅ | UUID of the sender. Peers discard messages where `source_guid == self_guid`. |
| `timestamp` | ✅ | Unix epoch float. Used for delta-buffer replay ordering. Moved to envelope; was previously buried inside some payloads and absent from others. |
| `payload` | ✅ | Message-specific content. May be `{}`. |

---

## Message event Catalogue

### Session Handshake

#### `session.who_is_master`

Broadcast by a new peer on join. Repeated every poll tick until a master responds or
the discovery timeout elapses (500 ms on LAN, 2 s on WAN), at which point the peer
self-elects.

`protocol_version` appears **only here** — it allows the master (or any other peer) to
reject an incompatible joiner immediately rather than silently misparsing data messages
later. Peers that see a mismatched version should respond with `session.error` and stop
forwarding messages to that peer.

```json
{
  "event": "session.who_is_master",
  "session_id": "my-review-session",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "protocol_version": "OTIO_SYNC_1.0",
    "requester_guid": "..."
  }
}
```

#### `session.i_am_master`

Sent by the current master in response to `session.who_is_master`. Carries
`protocol_version` so the joiner can detect incompatibility before requesting state —
symmetric with the joiner advertising its version in `session.who_is_master`.

```json
{
  "event": "session.i_am_master",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "master_guid": "...",
    "protocol_version": "OTIO_SYNC_1.0"
  }
}
```

#### `session.state_request`

Sent by a joining peer once it has identified the master. The peer enters
`STATE_JOINING` and buffers all non-`session.*` messages until the snapshot arrives.

```json
{
  "event": "session.state_request",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "target_guid": "<master_guid>",
    "requester_guid": "..."
  }
}
```

#### `session.state_snapshot`

Sent by the master in response to `session.state_request`. Contains the full serialised
OTIO timeline set. The joining peer applies this snapshot, replays any buffered deltas
with `event_guid` later than `last_event_guid`, then transitions to `STATE_SYNCED`.

If the serialized `timelines` blob exceeds 1 MB it is compressed with `zlib` and the
`compressed` flag is set to `true`. The joining peer decompresses before parsing.

```json
{
  "event": "session.state_snapshot",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "target_guid": "<requester_guid>",
    "snapshot_timestamp": 1747123456.789,
    "last_event_guid": "<guid_of_last_event_master_processed>",
    "compressed": false,
    "timelines": {
      "<timeline_guid>": { "OTIO_SCHEMA": "Timeline.1", "...": "..." }
    },
    "active_timeline_guid": "<guid>",
    "playback_state": {}
  }
}
```

`last_event_guid` anchors buffer replay: the joining peer discards buffered events up to
and including `last_event_guid`, then applies the remainder in order. This prevents
double-applying mutations that were already captured in the snapshot.

#### `session.leave`

Broadcast by a peer before it disconnects. Receivers should update their participant
list. If the departing peer is the master, remaining peers restart discovery.

Currently absent from both implementations — this is a new addition.

```json
{
  "event": "session.leave",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {}
}
```

#### `session.error`

Sent when a peer cannot join due to an incompatible protocol version or other
session-level rejection. The receiver should surface this to the user.

```json
{
  "event": "session.error",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "code": "INCOMPATIBLE_VERSION",
    "detail": "Master is running OTIO_SYNC_1.0; peer requested OTIO_SYNC_2.0"
  }
}
```

---

### Playback

#### `playback.set`

Broadcast by any peer on play, stop, scrub, or view change. High frequency.

```json
{
  "event": "playback.set",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "playing": false,
    "current_time": { "OTIO_SCHEMA": "RationalTime.1", "value": 42.0, "rate": 24.0 },
    "looping": true,
    "timeline_guid": "<guid>"
  }
}
```

---

### Selection

#### `selection.set`

References a single clip by its OTIO GUID — not an RV node name, which is local to
each instance and meaningless to other tools. A list was used in the POC but a single
focused clip is the meaningful concept here; multi-selection can be added later if a
clear use case emerges.

```json
{
  "event": "selection.set",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "clip_guid": "<otio_clip_guid>"
  }
}
```
---

### OTIO Patch

Rather than regularly sending multiple OTIO timelines between clients, we are proposing using a delta-based approach inspired by RFC 6902 (JSON Patch) but optimized for the OTIO object model.

A key part of the patch design is that each object has a GUID that can be referred to by the patching process. So deleting a object into a structure can say "Delete object with GUID<GUID> that is in the container with GUID<GUIDPARENT>". Or "Set property XXX to YYY on object guid <GUID>". 

The patches are generated by registering the `OTIOPatcher` as an observer on the root timeline, and receives two callbacks:
* `on_property_changed(obj, property_name)` — fires after any OTIO property is mutated, e.g. `clip.name = "New Name"`. The patcher captures the new value immediately and emits a `SetPropertyPatch`.
* `on_children_changed(composition, action, index, child)` — fires on insert, remove, or clear. Emits `InsertChildPatch`, `RemoveChildPatch`, or `ClearChildrenPatch`.

We need to explore whether the metadata sub-key changes can be observable in python (C++ may be more problematic), so we may need to require metadata mutations must be routed through `patcher.set_metadata(obj, path, value)`, which applies the change and emits a `SetMetadataPatch` directly.

These messages are buffered during `STATE_JOINING` and replayed after the snapshot.

#### `timeline.insert_child`

```json
{
  "event": "timeline.insert_child",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "parent_uuid": "<track_guid>",
    "index": 2,
    "child_data": { "OTIO_SCHEMA": "Clip.1", "...": "..." }
  }
}
```

#### `timeline.remove_child`

```json
{
  "event": "timeline.remove_child",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "parent_uuid": "<track_guid>",
    "child_uuid": "<clip_guid>"
  }
}
```

#### `timeline.move_child`

```json
{
  "event": "timeline.move_child",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "parent_uuid": "<track_guid>",
    "child_uuid": "<clip_guid>",
    "to_index": 1
  }
}
```

#### `timeline.set_property`

Used for primitive OTIO property mutations (strings, numbers, bools, `RationalTime`,
`TimeRange`, etc.). The `path` is the OTIO property name; `value` is OTIO-serialized.

```json
{
  "event": "timeline.set_property",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "target_uuid": "<object_guid>",
    "path": "name",
    "value": "My Clip"
  }
}
```

#### `timeline.set_metadata`

A **separate** patch event for metadata sub-key mutations. Direct writes to
`obj.metadata["x"] = y` would be observable via python, but not in a potential C++ implementation,
so we recommend setting metadata through an explicit API call, so that they can be tracked.
(see [OTIO C++ Core Notes](#otio-c-core-notes) — AnyDictionary gap). Metadata changes must
be expressed as explicit `timeline.set_metadata` messages with sub-key granularity.

The `path` is a slash-separated sub-key path within the metadata dict, inspired by
USD's property path addressing. This gives per-key granularity rather than replacing
the whole metadata blob.

```json
{
  "event": "timeline.set_metadata",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "target_uuid": "<object_guid>",
    "path": "annotations/frame_1/strokes",
    "value": { "...": "..." }
  }
}
```

---

### Transactions

#### `transaction`

Groups multiple patches into a single atomic broadcast. Used when a single logical
operation (e.g. a ripple edit, an annotated clip insert) generates more than one patch.
Receivers apply all patches in order before triggering any callbacks.

A single mutation outside a transaction context auto-wraps into a single-patch
transaction. Consumers always receive `transaction` objects — uniform interface
regardless of patch count.

```json
{
  "event": "transaction",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "description": "Draw annotation on frame 42",
    "patches": [
      {
        "patch_event": "set_metadata",
        "target_uuid": "<clip_guid>",
        "path": "annotations/frame_42/strokes",
        "new_value": { "...": "..." },
        "old_value": null
      },
      {
        "patch_event": "set_property",
        "target_uuid": "<clip_guid>",
        "path": "name",
        "new_value": "Annotation_42",
        "old_value": "Untitled"
      }
    ]
  }
}
```

**Patch events** (plain JSON-serializable — readable by C++/python tools):

| `patch_event` | Fields |
|---|---|
| `set_property` | `target_uuid`, `path`, `new_value`, `old_value` |
| `set_metadata` | `target_uuid`, `path`, `new_value`, `old_value` |
| `insert_child` | `parent_uuid`, `index`, `child_json` |
| `remove_child` | `parent_uuid`, `index`, `child_uuid`, `child_json` |
| `clear_children` | `parent_uuid`, `child_jsons` |

`old_value` / `child_json` fields are included to support future undo/redo without
requiring shadow state.


---

### Annotations

Annotations are expressed as a standard `transaction` containing a single `insert_child`
patch — no dedicated message type required. The sender resolves the stroke to OTIO
SyncEvent objects and a clip GUID *before* sending, then inserts a fully-formed
annotation `Clip` into the Annotations track.

In the POC, annotation strokes were broadcast as raw RV paint properties (`node_name`,
`points`, `color`, etc.) with the master separately persisting them to the Annotations
track. This approach had two problems:

1. **RV-specific data** — `node_name`, `media_path` strings, and raw float arrays are
   not portable to other tools (e.g. xStudio).
2. **Master-only persistence** — all other timeline mutations are applied by every peer
   symmetrically; annotations should be no different.

Expressing annotations through `insert_child` resolves both: the annotation clip is a
plain OTIO object, and every peer applies the same patch via `OTIOPatcher.apply()`.
The receiver's `on_children_changed` callback fires, detects that the new clip's parent
is an Annotations track, and renders using its own paint API. No master-only step, no
raw paint data on the wire.

The annotation clip encodes everything it needs in standard OTIO fields:

- `source_range.start_time` — clip-local time of the annotated frame (0-indexed,
  matching the `ORIAnnotations.ReviewItemFrame` export convention).
- `metadata["annotation_commands"]` — list of SyncEvent objects for the stroke.
- `metadata["clip_guid"]` — back-reference to the media clip being annotated.

```json
{
  "event": "transaction",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "description": "Add annotation on frame 42",
    "patches": [{
      "patch_event": "insert_child",
      "parent_uuid": "<annotation_track_guid>",
      "index": -1,
      "child_json": {
        "OTIO_SCHEMA": "Clip.1",
        "source_range": {
          "start_time": { "value": 41.0, "rate": 24.0 },
          "duration":   { "value":  1.0, "rate": 24.0 }
        },
        "metadata": {
          "clip_guid": "<media_clip_guid_being_annotated>",
          "annotation_commands": [
            { "OTIO_SCHEMA": "PaintStart.1", "brush": "gauss", "rgba": [1.0, 0.0, 0.0, 1.0], "uuid": "..." },
            { "OTIO_SCHEMA": "PaintPoints.1", "uuid": "...", "points": { "...": "..." } }
          ]
        }
      }
    }]
  }
}
```

#### SyncEvent events in use

| Schema | Purpose |
|---|---|
| `PaintStart.1` | Opens a stroke; carries `brush`, `rgba`, `width`, `uuid` |
| `PaintPoints.1` | Appends point batch to active stroke; carries `uuid`, `points` |
| `PaintEnd.1` | Closes the active stroke |
| `TextAnnotation.1` | Positioned text label with font metadata |

---

---

## Session State Machine

```
            ┌─────────────────────────────────────────────┐
            │              Disconnected                    │
            └──────────────────┬──────────────────────────┘
                               │ start_session()
                               ▼
            ┌─────────────────────────────────────────────┐
            │   DISCOVERING                               │
            │   Broadcasting session.who_is_master        │
            │   every poll tick                           │
            └────────┬──────────────────┬─────────────────┘
                     │ timeout          │ session.i_am_master received
                     │ (500 ms LAN /    │
                     │  2 s WAN)        │
                     ▼                  ▼
   ┌──────────────────────┐  ┌──────────────────────────────┐
   │  Self-elect MASTER   │  │  JOINING                     │
   │  init timelines      │  │  Send session.state_request  │
   │  → STATE_SYNCED      │  │  Buffer non-session.* msgs   │
   └──────────────────────┘  └──────────────┬───────────────┘
                                            │ session.state_snapshot received
                                            ▼
                             ┌──────────────────────────────┐
                             │  Apply snapshot              │
                             │  Replay buffered deltas      │
                             │  after last_event_guid       │
                             │  → STATE_SYNCED              │
                             └──────────────────────────────┘
```

| State | Description |
|---|---|
| `STATE_NONE` | Not connected to the session exchange. |
| `STATE_DISCOVERING` | Connected; `session.who_is_master` broadcast sent, awaiting response. |
| `STATE_JOINING` | Master identified; `session.state_request` sent; all incoming non-session events buffered. |
| `STATE_SYNCED` | Snapshot applied, buffer replayed; normal delta processing. |

---

## Conflict Resolution

- **Last-Write-Wins (LWW)** — The envelope `timestamp` is used for simple property
  conflicts. When two peers concurrently set the same property, the patch with the later
  timestamp wins.

- **Causal ordering** — For operations that must be applied in a defined order (e.g.
  insert then remove), vector clocks provide a causal sequence number. Each peer
  maintains a logical clock incremented on every send; patches carry the sender's clock
  value. Receivers detect out-of-order delivery and hold patches until their causal
  predecessors arrive.

  Vector clocks are an open question for this version — see [Open Questions](#open-questions).


---

## OTIO C++ Core Notes

The short term goal is to implement a python OTIO Patch manager (The SyncManager), but for some implementations it may be desirable to have a C++ implementation. 

Below outlines the changes that may be desired to the core C++ OTIO core.

### `sync_id` (native GUID)

We would want each `SerializableObject` to have a `sync_id` as a first-class property in the C++ core. It is
generated lazily on first access and survives JSON round-trips. This eliminates the need
for the current manual `metadata["sync"]["guid"]` injection used in the python version.

Migration path: once the Python bindings expose `sync_id`, replace all `metadata["sync"]["guid"]`
lookups with `obj.sync_id` and drop the `_ensure_guid_and_map()` helper.

### `MutationObserver`

The C++ core would provide a `MutationObserver` base class with two callbacks:

- `on_property_changed(obj, property_name)` — fires after any OTIO property is mutated.
- `on_children_changed(composition, action, index, child)` — fires on structural changes
  (insert, remove, clear).

Observers are registered per-object via `add_observer()` / `remove_observer()`. This
replaces the earlier `OTIOSyncProxy` Python wrapper approach. `OTIOPatcher` is a
`MutationObserver` subclass that emits structured patch objects on every observed
mutation.

**Recursive registration gap** — `add_observer()` registers on a single object only.
Observing a full timeline requires traversing the entire tree and registering on every
node, then re-registering on newly inserted subtrees via `on_children_changed`.
Recommended enhancement: `add_observer(observer, recursive=true)`.

### AnyDictionary gap

`MutationObserver` fires `on_property_changed(obj, "metadata")` when any metadata key
changes, but provides no sub-key path or value. `AnyDictionary` (the C++ type backing
`obj.metadata`) has no observation mechanism of its own.

The consequence: a patch system cannot generate fine-grained `set_metadata` patches
purely from observation. Metadata mutations must be routed through an explicit
`patcher.set_metadata(obj, path, value)` call. Direct writes to `obj.metadata["x"] = y`
are invisible to any observer.

In Python, a proxy wrapper around `AnyDictionary` can intercept `__setitem__` calls and
generate sub-key patches, but still requires explicit routing through the patcher.

**Recommended C++ enhancement**: `MutationObserver` should receive an optional sub-path
and new value when metadata changes, consistent with how USD's `TfNotice::ObjectsChanged`
surfaces property-path-level granularity.

### Partial / delta serialization

`obj.to_json_delta(path, value)` and `obj.apply_delta(json_delta)` are not yet in the
OTIO core API. Currently the POC serializes full objects for `insert_child` payloads.
Standardizing partial serialization within the library would simplify third-party
integrations.

### Mutation transactions

`Timeline.begin_transaction()` / `Timeline.end_transaction()` are not yet in the core
API. The `transaction` message event in this protocol is a protocol-level approximation
implemented in `OTIOPatcher` via a Python context manager. A C++ core equivalent would
allow complex operations (ripple edit, clip move) to be broadcast atomically without
intermediate broken states reaching peers.

---

## What Was Kept / Changed / Added

| Area | Decision | Rationale |
|---|---|---|
| `command` + `event` → `event` | **Changed** | Flat, unambiguous, dispatches on one field |
| `protocol_version` on every message | **Dropped** (LiveReview) | Noise on high-frequency messages; handshake is the right place |
| `protocol_version` on `session.who_is_master` + `session.i_am_master` | **Added** | Fail fast on incompatible peers; symmetric — both sides declare their version |
| `timestamp` in envelope | **Changed** (was in some payloads) | Consistent delta-buffer replay; belongs in transport not content |
| Master election via broadcast + timeout | **Kept** (ORIAnnotations) | More robust than implicit create/join; handles peer crashes |
| Discovery timeout | **Refined** | 500 ms on LAN, 2 s on WAN — matches Draft §7A |
| Delta buffer during `STATE_JOINING` | **Kept** (ORIAnnotations) | Prevents race between snapshot and concurrent mutations |
| `last_event_guid` in `state_snapshot` | **Added** | Anchors buffer replay; prevents double-applying mutations already in snapshot |
| zlib compression for large snapshots | **Added** | Snapshots >1 MB compressed before transmission |
| `source_guid` self-filtering | **Kept** (ORIAnnotations) | Peers discard their own echoed messages |
| `session.leave` | **Added** (new) | Graceful disconnect; triggers re-election if master leaves |
| `session.error` | **Added** (new) | Surface version mismatch or rejection to the user |
| `annotation.stroke` → `transaction`/`insert_child` | **Changed** | Removes RV-specific wire data and master-only persistence; annotation clip is a plain OTIO object applied symmetrically by all peers via `OTIOPatcher` |
| `timeline.set_metadata` as separate event | **Added** | AnyDictionary gap means metadata sub-key changes cannot be expressed as `set_property` |
| `transaction` message event | **Added** | Groups atomic multi-patch operations; uniform interface for consumers |
| Patch taxonomy (`set_property`, `set_metadata`, `insert_child`, `remove_child`, `clear_children`) | **Added** | Aligns with `MutationObserver` callbacks; `old_value`/`child_json` enable future undo |
| Full OTIO snapshot in `state_snapshot` | **Kept** (ORIAnnotations) | Complete timeline state, not just playback |

---

## Open Questions

1. **Master re-election**: If the master sends `session.leave` (or drops silently), should remaining peers restart the full `DISCOVERING` cycle, or should the master designate a successor in `session.leave`?

2. **`playback.set` throttling**: This fires on every frame scrub. Should the protocol define a recommended minimum send interval, or leave that to the implementation?

3. **Single-clip viewing**: When a peer is viewing a single clip directly (not via a sequence), should that clip be wrapped in its own single-clip timeline, or should the protocol have a distinct concept of "focus this source clip from timeline X" without constructing a full timeline around it? The latter is more flexible — it lets any peer view any piece of source media from any of the shared timelines without requiring a synthetic timeline to be created and synced. This intersects with how `selection.set` and `playback.set` relate: is "I am looking at clip X at frame Y" playback state, selection state, or a third concept?

4. **Annotation conformance**: Expressing annotations as `insert_child` patches assumes every receiver can interpret OTIO SyncEvent objects in `annotation_commands` and map them to its own paint API. Is that a reasonable baseline requirement for a conforming peer, or do we need a lower-level fallback (e.g. a parallel `raw_paint` field in the clip metadata) for tools that don't have native SyncEvent support yet?

5. **Vector clocks**: LWW on `timestamp` is sufficient for independent property edits but breaks for causally-dependent sequences (insert then remove the same clip). Should v1 mandate vector clocks, recommend them as an optional extension, or defer entirely to v2?

6. **`sync_id` migration**: Once the OTIO Python bindings expose `sync_id` natively, the current `metadata["sync"]["guid"]` injection in `SyncManager._ensure_guid_and_map()` should be replaced. What is the transition path for existing `.otio` files that carry GUIDs in `metadata["sync"]["guid"]` only?

7. How to handle global effects, e.g. global scale/pan zoom or global color effects. These could be Effects in a block on the overall state (not individual timelines), or is it better to have a more well defined list of effects.

8. Should we define a list of properties that a play can support, e.g. multi-track OTIO files, or particular effects, or brushes.

9. How to handle encryption of stream, do we encrypt the whole stream, or just part of it?