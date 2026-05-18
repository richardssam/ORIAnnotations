# OTIO Sync Protocol — v1.0 Proposal

## Summary

This document proposes a revised wire protocol for the OTIO Sync session, drawing from
the LiveReview (ASWF) and ORIAnnotations (POC) implementations. The goals are:

- Replace the two-field `command`/`event` dispatch with a single `type` string
- Add protocol version negotiation at handshake time only
- Promote `timestamp` to the envelope (currently inconsistently placed in payloads)
- Add a graceful `session.leave` message (currently missing from both implementations)
- Keep the master election, delta buffering, and self-filtering from ORIAnnotations
- Keep the schema-versioning intent from LiveReview, scoped to the handshake

---

## Envelope

Every message on the wire shares a common envelope:

```json
{
  "type": "<namespace>.<action>",
  "session_id": "my-review-session",
  "source_guid": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": 1747123456.789,
  "payload": {}
}
```

| Field | Required | Notes |
|---|---|---|
| `type` | ✅ | Dot-namespaced. Replaces `command` + `event`. |
| `session_id` | ✅ | Scopes messages to a session. Peers ignore messages for other session IDs. |
| `source_guid` | ✅ | UUID of the sender. Peers discard messages where `source_guid == self_guid`. |
| `timestamp` | ✅ | Unix epoch float. Used for delta-buffer replay ordering. Moved to envelope; was previously buried inside some payloads and absent from others. |
| `payload` | ✅ | Message-specific content. May be `{}`. |

---

## Message Type Catalogue

### Session Handshake

#### `session.who_is_master`

Broadcast by a new peer on join. Repeated every poll tick until a master responds or
the discovery timeout elapses (2 s), at which point the peer self-elects.

`protocol_version` appears **only here** — it allows the master (or any other peer) to
reject an incompatible joiner immediately rather than silently misparsing data messages
later. Peers that see a mismatched version should respond with `session.error` and stop
forwarding messages to that peer.

```json
{
  "type": "session.who_is_master",
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
  "type": "session.i_am_master",
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
  "type": "session.state_request",
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
whose `timestamp` is newer than `snapshot_timestamp`, then transitions to `STATE_SYNCED`.

```json
{
  "type": "session.state_snapshot",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "target_guid": "<requester_guid>",
    "snapshot_timestamp": 1747123456.789,
    "timelines": {
      "<timeline_guid>": { "OTIO_SCHEMA": "Timeline.1", "..." : "..." }
    },
    "active_timeline_guid": "<guid>",
    "playback_state": {}
  }
}
```

#### `session.leave`

Broadcast by a peer before it disconnects. Receivers should update their participant
list. If the departing peer is the master, remaining peers restart discovery.

Currently absent from both implementations — this is a new addition.

```json
{
  "type": "session.leave",
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
  "type": "session.error",
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
  "type": "playback.set",
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
  "type": "selection.set",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "clip_guid": "<otio_clip_guid>"
  }
}
```

---

### Annotations

#### `timeline.add_annotation`

In the POC, annotation strokes were broadcast as raw RV paint properties
(`node_name`, `points`, `color`, etc.) with the master separately persisting them
to the OTIO Annotations track. This breaks the design pattern in two ways:

1. **RV-specific data** — `node_name`, `media_path` strings, and raw float arrays are
   not portable to other tools (e.g. xStudio).
2. **Master-only persistence** — all other `timeline.*` mutations are applied by every
   peer symmetrically; annotations should be no different.

The fix is to express annotations as an OTIO-native `timeline.*` mutation. The sender
resolves the stroke to OTIO SyncEvent objects and a clip GUID *before* sending. Every
peer applies the same `timeline.add_annotation` to their local OTIO model and then
renders it using their own tool-native API. No master-only step, no raw paint data on
the wire.

The `clip_guid` references the media clip in the Media track being annotated. The time
is expressed as an OTIO `RationalTime` relative to that clip's source range, making it
portable regardless of sequence position or frame numbering conventions.

```json
{
  "type": "timeline.add_annotation",
  "session_id": "...",
  "source_guid": "...",
  "timestamp": 1747123456.789,
  "payload": {
    "annotation_track_guid": "<annotations_track_guid>",
    "clip_guid": "<media_clip_guid_being_annotated>",
    "time": { "OTIO_SCHEMA": "RationalTime.1", "value": 41.0, "rate": 24.0 },
    "events": [
      { "OTIO_SCHEMA": "PaintStart.1", "brush": "gauss", "rgba": [1.0, 0.0, 0.0, 1.0], "uuid": "..." },
      { "OTIO_SCHEMA": "PaintPoints.1", "uuid": "...", "points": { "...": "..." } }
    ]
  }
}
```

---

### Timeline Structure

These messages are buffered during `STATE_JOINING` and replayed after the snapshot.

#### `timeline.insert_child`

```json
{
  "type": "timeline.insert_child",
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
  "type": "timeline.remove_child",
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
  "type": "timeline.move_child",
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

```json
{
  "type": "timeline.set_property",
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
                     │ timeout (2s)     │ session.i_am_master received
                     ▼                 ▼
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
                             │  → STATE_SYNCED              │
                             └──────────────────────────────┘
```

---

## What Was Kept / Changed / Added

| Area | Decision | Rationale |
|---|---|---|
| `command` + `event` → `type` | **Changed** | Flat, unambiguous, dispatches on one field |
| `protocol_version` on every message | **Dropped** (LiveReview) | Noise on high-frequency messages; handshake is the right place |
| `protocol_version` on `session.who_is_master` | **Added** | Fail fast on incompatible peers, before any data flows |
| `timestamp` in envelope | **Changed** (was in some payloads) | Consistent delta-buffer replay; belongs in transport not content |
| Master election via broadcast + timeout | **Kept** (ORIAnnotations) | More robust than implicit create/join; handles peer crashes |
| Delta buffer during `STATE_JOINING` | **Kept** (ORIAnnotations) | Prevents race between snapshot and concurrent mutations |
| `source_guid` self-filtering | **Kept** (ORIAnnotations) | Peers discard their own echoed messages |
| `session.leave` | **Added** (new) | Graceful disconnect; triggers re-election if master leaves |
| `session.error` | **Added** (new) | Surface version mismatch or rejection to the user |
| `annotation.stroke` → `timeline.add_annotation` | **Changed** | Removes RV-specific wire data and master-only persistence; all peers apply it symmetrically as an OTIO mutation |
| Full OTIO snapshot in `state_snapshot` | **Kept** (ORIAnnotations) | Complete timeline state, not just playback |

---

## Open Questions

1. **Master re-election**: If the master sends `session.leave` (or drops silently), should remaining peers restart the full `DISCOVERING` cycle, or should the master designate a successor in `session.leave`?

2. **`playback.set` throttling**: This fires on every frame scrub. Should the protocol define a recommended minimum send interval, or leave that to the implementation?

3. **Single-clip viewing**: When a peer is viewing a single clip directly (not via a sequence), should that clip be wrapped in its own single-clip timeline, or should the protocol have a distinct concept of "focus this source clip from timeline X" without constructing a full timeline around it? The latter is more flexible — it lets any peer view any piece of source media from any of the shared timelines without requiring a synthetic timeline to be created and synced. This intersects with how `selection.set` and `playback.set` relate: is "I am looking at clip X at frame Y" playback state, selection state, or a third concept?

4. **Annotation as OTIO mutation**: `timeline.add_annotation` resolves the RV-specific data problem and removes the master-only persistence step, but it assumes every receiver can interpret OTIO SyncEvent objects and map them to their own paint API. Is that a reasonable baseline requirement for any conforming peer, or do we need a lower-level fallback (e.g. a parallel `raw_paint` field) for tools that don't have native SyncEvent support yet?
