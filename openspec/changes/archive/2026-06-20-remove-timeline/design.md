## Context

The `TIMELINE_1.0` family has `AddTimeline` and `RenameTimeline` but no removal
verb. The only existing removal is `SyncManager.reset_timelines()`, which calls
`.clear()` on `_timelines`, `_object_map`, and `_clip_timelines` simultaneously —
a wholesale local reset used during master re-init, not a per-timeline,
networked delete.

A single-timeline delete is harder than `reset_timelines` because a timeline is
not a leaf. Registering one (`_h_add_timeline` → `_traverse_and_map`) seeds four
pieces of cross-cutting state:

```
 register sequence T (guid=G)
   _timelines[G]            = T
   _object_map[...]         += every object in T's subtree   (shared flat index)
   _clip_timelines{clip→ct} += created lazily per annotated clip
   active_timeline_guid     = G  (only if none was set)
```

`_object_map` is a single flat `{guid: object}` index **shared across all
timelines**. `reset_timelines` can `.clear()` it because it nukes every timeline
at once; a targeted delete must remove only the subtree it owns or it corrupts
lookups for survivors.

Layered on top: clip-annotation timelines built with
`_traverse_and_map_preserve`, which deliberately keeps the *sequence* clip
canonical in `_object_map`. Deleting the sequence removes that canonical clip, so
its clip-annotation timeline must die with it.

The active timeline is **not** authoritative protocol state. There is no
"set active" message; `_h_playback_set` re-derives `active_timeline_guid` from the
`timeline_guid` on every `PlaybackSettingsSet` (filtering clip timelines). Active
tracking is a projection of the playback stream.

## Goals / Non-Goals

**Goals:**
- A `RemoveTimeline` message that propagates a single sequence/playlist deletion
  to all peers, mirroring `AddTimeline`/`RenameTimeline`.
- Reference-aware teardown: remove exactly the deleted timeline's state, leaving
  surviving timelines' object-map entries intact.
- Cascade-delete the deleted sequence's clip-annotation timelines.
- A defined active-timeline-on-delete behavior with no per-peer divergence.
- A host viewer-container teardown hook, symmetric to `add_timeline`.

**Non-Goals:**
- Deleting individual clips/tracks within a timeline — that is the existing
  `RemoveChild` path, unchanged.
- A transactional/batch delete of multiple timelines.
- Changing `reset_timelines` semantics.
- Garbage-collecting orphaned clip-annotation timelines after `RemoveChild`
  (separate concern; noted in Open Questions).

## Decisions

### D1 — New message `RemoveTimeline` (`TIMELINE_1.0` / `REMOVE_TIMELINE`)
Fields: `timeline_guid`, `sync_timestamp`. No OTIO payload — peers already hold
the timeline; the GUID is sufficient. Symmetric to `RenameTimeline`.
*Alternative considered:* reuse a generic `RemoveChild` at the session root —
rejected: timelines are not children of a composition in `_object_map`, and the
teardown semantics (clip-timeline cascade, viewer container) are timeline-specific.

### D2 — Reference-aware teardown, not clear-all
`_h_remove_timeline` walks the deleted timeline's subtree and removes only those
GUIDs from `_object_map`, then deletes `_timelines[guid]`.
*Alternative considered:* rebuild `_object_map` from the surviving `_timelines`
after each delete — simpler to reason about but O(total objects) per delete and
loses the `preserve` canonical-clip nuance. Rejected for hot-path sessions;
revisit only if subtree removal proves error-prone.

### D3 — Cascade clip-annotation timelines, no cross-sequence sharing
Clip timelines are keyed by `uuid5("clip_timeline:<seq_clip_guid>")` — a
*per-clip-instance* GUID. The same media in two playlists yields two distinct
seq-clip GUIDs → two distinct clip timelines. Therefore no clip timeline is shared
between sequences, and deleting a sequence can unconditionally drop every
`_clip_timelines` entry (and its `_timelines` entry) whose clip belongs to that
sequence's subtree. No reference counting needed.

### D4 — Active-timeline on delete: clear to `None`, never name a successor
If `deleted_guid == active_timeline_guid`, set it to `None`. Do not pick a
replacement and do not carry one in the message.
Rationale: active is a projection of the playback stream (`_h_playback_set`).
Naming a successor introduces a second writer that can contradict playback,
causing a one-frame flicker/divergence. Clearing to `None` leaves a single writer.
Reads stay safe: the `active_timeline` property already falls back to
`next(iter(_timelines.values()), None)` when the stored GUID is unset, and the
`_h_playback_set` guard (`tl_guid in self._timelines`) prevents a stray late frame
from resurrecting a dead pointer.
*Alternative considered:* include `new_active_guid` in `RemoveTimeline` — rejected
per the divergence/redundancy argument above.

### D5 — Host ordering contract: switch on-screen source, then remove
The host moves its on-screen source to a surviving sequence *before* calling
`broadcast_remove_timeline`. The switch fires a `PlaybackSettingsSet` carrying the
new active GUID, so by the time peers process `REMOVE_TIMELINE` the deleted
timeline is no longer active. `active = None` then occurs only when the *last*
timeline is closed — exactly when `None` is the truthful answer.

### D6 — Idempotent handler, `("remove_timeline", tl)` return
Unknown/already-removed GUID → silent no-op (both peers may observe the same
close; the guard mirrors `_h_add_timeline`'s `guid not in _timelines` check). On a
real removal, return `("remove_timeline", tl)` so the host tears down its viewer
container, symmetric to the existing `("add_timeline", tl)` return.

### D7 — Host delete detection differs per host
RV is poll-driven: add a deleted-sequence counterpart alongside
`poll_new_sequences` in the sequence/structural controller. xStudio is
event-driven: `StructureSyncController` (which already handles deletions) emits
the broadcast. Both converge on `broadcast_remove_timeline` + the inbound
`remove_timeline` event handler.

## Risks / Trade-offs

- **Incomplete object-map subtree removal leaves dangling GUIDs** → derive the
  removal set by traversing the timeline object itself before deleting it; cover
  with a test asserting `_object_map` contains no GUID from the removed subtree
  and still contains every survivor's GUIDs.
- **Clip-annotation timeline missed in cascade leaks state / dangling viewer** →
  enumerate cascade targets via `_clip_timelines` reverse lookup against the
  deleted subtree's clip GUIDs; test a sequence carrying ≥1 annotated clip.
- **Out-of-order delivery (REMOVE before ADD, or duplicate REMOVE)** → idempotent
  no-op handles both; registry dispatch already drops unknown messages on older
  peers (backward compatible).
- **Host deletes the active timeline without switching first (violates D5)** →
  `active = None` + property fallback keeps reads valid; the next playback frame
  repopulates. Degraded, not broken.

## Migration Plan

Additive protocol change; no data migration. Peers without the handler ignore
`REMOVE_TIMELINE` (registry drops unknown events) — they keep the stale timeline,
which is exactly today's behavior, so mixed-version sessions are no worse than now.
Rollback: remove the message/handler/host hooks; `reset_timelines` remains the
fallback removal.

## Open Questions

- Should an orphaned clip-annotation timeline be cleaned up when its clip is
  removed via `RemoveChild` (independent of sequence deletion)? Out of scope here,
  but the cascade helper built for D3 is the natural reuse point.
- Does any host need an explicit "removed timeline was active, please re-select"
  signal beyond the playback stream, or is D4 + D5 sufficient in practice? Resolve
  during host wiring.
