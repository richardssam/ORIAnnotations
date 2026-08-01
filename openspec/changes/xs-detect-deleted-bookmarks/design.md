## Context

See proposal.md — Why. The implementation-relevant shape of the current code:

- `flush_pending_annotations` (`annotation_sync.py`) reads `session.bookmarks.bookmarks`, returns early via `if not scan_uuids: return`, then calls `broadcast_local_bookmark(bm_uuid)` for each surviving bookmark.
- `broadcast_local_bookmark` resolves the bookmark to a `(clip_guid, frame)` key — including a flat-playlist fallback path — compares against what the OTIO timeline already holds, and broadcasts an add or a replace. All deletion detection lives inside that per-bookmark call, so it can only ever run for bookmarks that still exist.
- `_our_bookmark_uuids` already tracks bookmarks the plugin created from remote annotations.
- The receive side is done: `annotation-lifecycle-sync` requires an empty `REPLACE_ANNOTATION_COMMANDS` to be applied as an authoritative hard clear in both RV and xStudio.

## Goals / Non-Goals

**Goals:**

- Detect a removed bookmark and broadcast the clear peers already know how to apply.
- Cover the empty-bookmark-list case, which is the most common one (clearing the only annotation in a session).
- Detect by disappearance, so note deletion and any other removal route are covered without enumerating actions.

**Non-Goals:**

- Any new wire message, schema, or receive-side change.
- Changing the count-decrease path for bookmarks that survive.
- Reacting synchronously to `PaintClear`. The interaction already schedules the flush; this change is about what the flush then sees.

## Decisions

**Diff a recorded key set against the live bookmark set, rather than reacting to the clear event.**
A disappearance check covers every removal route — clear, note deletion, undo of a bookmark creation — and does not depend on xStudio's action vocabulary. Alternative considered: broadcasting the clear directly from the `PaintClear` interaction. Rejected because the interaction does not identify which bookmark or clip/frame was cleared, it would miss note deletion entirely, and it would race the bookmark actually being removed.

**Record the key at broadcast time, in `broadcast_local_bookmark`.**
That function already resolves the `(clip_guid, frame)` key, including the flat-playlist fallback, so recording there avoids re-deriving it — which is impossible after the fact anyway, since the bookmark is gone by the time we notice.

**Evaluate disappearance before the empty-list early return.**
The single most common case — clearing the only annotation — leaves zero bookmarks. Keeping the early return ahead of the diff would fix everything except the case that matters most.

**Forget a key once its clear is broadcast.**
Keeps the broadcast idempotent without a second "already cleared" set, and lets a subsequent redraw on the same clip/frame record the key afresh.

## Risks / Trade-offs

- **Spurious clears while xStudio is mid-edit or has not yet committed a bookmark.** The existing `stale_any` retry path already shows that annotation data is not always readable when the debounce fires. → Only broadcast for keys that were recorded from a *successful* broadcast, and consider requiring the bookmark to be absent on two consecutive scans before declaring it gone. Verify by drawing, clearing, and redrawing rapidly on the same frame.
- **A remote peer's clear echoing back.** The plugin creates bookmarks for remote annotations and tracks them in `_our_bookmark_uuids`; when the remote peer clears, our copy disappears too. → Exclude keys whose bookmark was in `_our_bookmark_uuids`, and confirm with a two-peer test that a clear crosses once and does not bounce.
- **Restoring the 30 s fallback interval (`fix-xs-annotation-draw-subscription` task 5.1) lengthens the window** in which a missed clear is invisible. → Land this change first, or at least together with it.

## Migration Plan

None. No persisted state and no protocol change; the key record is in-memory and rebuilt as annotations are broadcast. Rollback is reverting the file.

## Open Questions

- Should a bookmark that survives but has lost *all* its strokes (a clear on a bookmark that also carries note text, which xStudio keeps) be handled by the count-decrease path, or does that path also skip an empty annotation? Worth confirming during implementation; it is the same user action with a different xStudio outcome.
