## Context

See proposal.md — Why. The mechanism is established by instrumentation, not
inferred. The implementation-relevant shape of the current code:

- OpenRV's flat sequence builder re-initialises a timeline's tracks
  (`init tracks for defaultSequence: ['Media', 'Annotations']`) after the first
  media add. The rebuilt Media track is a **new OTIO object** and is assigned a
  **fresh sync GUID** by `ensure_guid_and_map`, replacing the one peers were
  given at join.
- `plugin.py` then calls `sync_manager.insert_child(self._active_media_track_guid, clip)`
  per added clip. That GUID is read from the *current* local track, so every
  insertion after the re-init addresses the new GUID.
- `SyncManager.insert_child` broadcasts whatever the patcher produced; nothing
  checks that the parent has ever been published.
- `OTIOPatcher.apply_patch` requires `parent_uuid in self.object_map` for
  `InsertChild` and returns `None` otherwise. The drop is not reported, not
  counted, and not visible to the plugin.
- `check_otio_snapshots` — the path that *would* have republished the structure,
  via `broadcast_replace_timeline` — is gated on `_otio_dirty` and on
  `is_master`. It did not fire between the re-init and the insertions.
- The `STATE_JOINING` delta-buffer replay compares
  `payload["payload"].get("sync_timestamp", 0)` against the snapshot timestamp.
  `sync_timestamp` lives one level deeper, in `command.payload`, so the
  comparison is always `0 > timestamp` — false. Latent here (nothing was
  buffered in the failing run) but wrong.

## Goals / Non-Goals

**Goals:**

- A patch can never name an object the receiving peers were not given.
- An unresolvable patch is visible without reading logs.
- Media added after a peer joins reach that peer and become viewable.

**Non-Goals:**

- Changing the wire protocol. Every message needed already exists; the fault is
  in which of them get sent, and in what order.
- Reworking `is_master` structural gating, or the master/host co-location
  question recorded in `host-owned-visibility`. Out of scope here.
- A general patch-replay or retry queue. See D3.
- Media that genuinely do not exist on the peer's filesystem — that is the
  `media_exists` path.

## Decisions

### D1: Fix the identity, not the announcement

Two shapes of fix are available. Re-announce the structure after re-initialising
it (broadcast the timeline again, then insert), or **stop the GUID changing** —
carry the existing track's sync GUID across re-initialisation, so the object
peers already hold stays valid.

Prefer the second. A sync GUID is the session's name for an object; if it can
change under a live session then every peer's references to it are invalidated
at once, and re-announcing only narrows the window in which that is true. The
first insertion after a re-init is broadcast within milliseconds of it, so the
window is not theoretical.

The codebase has decided this way before: `_replace_timeline_local` preserves
GUIDs carried in `metadata.sync.guid` precisely so that "annotations keyed by
clip GUID survive" a wholesale structure replacement, and
`get_or_create_clip_timeline` derives GUIDs deterministically so peers agree
without coordination. Track re-initialisation is the same problem and should
have the same answer.

*Alternative — re-announce after re-init:* rejected as the primary fix for the
reasons above, but it is the correct **safety net** (D2).

### D2: Refuse to broadcast a patch whose parent was never published

Independently of D1, `SyncManager` should not emit a patch addressing an object
it has not published. This is a guard, not the fix — with D1 in place it should
never trigger — and its value is that it converts a silent, permanent divergence
into a loud local failure at the sending peer, which is where the information
is.

What "published" means needs care: a peer's own `object_map` contains objects it
created locally and has never sent. The cheap, honest test is whether the parent
was part of a timeline this peer has broadcast (`ADD_TIMELINE` / `REPLACE_TIMELINE`)
or was itself inserted via a broadcast patch. If that bookkeeping proves
awkward, fall back to reporting rather than refusing — the goal is that nobody
can lose eight messages without knowing.

### D3: Report unresolvable patches; do not queue them

A patch whose parent is missing is reported and dropped, not buffered for replay.
Under D1 the condition stops arising, so a retry queue would be machinery for a
case that should no longer occur — and a queue that silently succeeds later is
the same "looks fine, is not" behaviour this change exists to remove.

Reporting reuses the channel `host-owned-visibility` added
(`PlaybackSyncController.mirror_failure`, surfaced as `view_mirror_error` in the
inspector and failed by the runner as `view_mirror_failed`) rather than
inventing a second one.

### D4: Fix the delta-buffer comparison separately

The replay comparison reads `sync_timestamp` from the wrong envelope level and
is always `0`. It did not cause this defect — nothing was buffered in the
failing run — so it lands as its own commit with its own test, and is not
allowed to ride along unexamined with a fix for something else. It is on the
join path, where an incorrect fix is expensive to diagnose.

## Risks / Trade-offs

- **[Risk]** Carrying the track GUID across re-initialisation (D1) means the
  rebuilt track inherits identity from an object that may differ structurally.
  → **Mitigation**: this is what `_replace_timeline_local` already does for
  whole timelines, including purging GUIDs the new structure lacks; reuse that
  reasoning rather than inventing a second rule.
- **[Risk]** D2's "was this published?" bookkeeping could be wrong in the
  conservative direction and suppress legitimate patches. → **Mitigation**:
  report-only first; escalate to refusal only once the suite is green with the
  reporting in place.
- **[Risk]** The diagnostic instrumentation from §1 is debug-level and cheap,
  but it is on hot paths (`apply_patch`). → **Mitigation**: keep the drop
  reporting, which is the point of D3; remove the buffer/discard tracing once
  §1 is closed.
- **[Trade-off]** `openrv_hosts_selection` stays `known_broken` until this
  lands, so `host-owned-visibility`'s RV-only fallback has unit coverage but no
  live coverage in the interim.

## Migration Plan

1. Rename the change to match what it does (proposal.md — Naming).
2. D1: stable track GUID across re-initialisation. This alone should turn
   `openrv_hosts_selection` green; confirm by re-running it repeatedly.
3. D3: report unresolvable patches, keeping the §1 drop instrumentation and
   promoting it from debug logging to an observable failure.
4. D2: the send-side guard, report-only.
5. D4: the delta-buffer comparison, as its own commit.

Each step is independently revertible. No persisted state and no wire change, so
rollback is reverting the files.

## Open Questions

- Does xStudio have the equivalent of the re-init GUID change? Its structure is
  built by a different path, so this needs checking rather than assuming — but
  the answer does not change any decision above, only whether a second fix
  follows.
