# xs-detect-deleted-bookmarks

## Why

Clearing a drawing in xStudio never reaches peers. The annotation stays on every other peer indefinitely, and no message is sent at all.

`annotation-lifecycle-sync` specifies that xStudio detects deletion as a stroke/caption count *decrease* observed while scanning bookmarks. That only works while the bookmark survives. It usually does not: `AnnotationsCore::clear_annotation` computes `bookmark_is_empty = !(detail.note_ && !detail.note_->empty())` and `ClearAnnotation::redo` calls `remove_bookmark()` (annotations_core_plugin.cpp:1459-1460, :1514), so clearing a drawing that carries no note text — the ordinary case — deletes the whole bookmark. `flush_pending_annotations` iterates `session.bookmarks.bookmarks` and returns early when that list is empty, so a deleted bookmark is invisible to the scan: there is no surviving record to observe a decrease on.

Confirmed live: across draw → clear the session's bookmark list goes `[] → [25902086…] → []`, and the plugin log shows `Draw interaction (event='PaintClear') — scheduling broadcast scan` followed by no scan output whatsoever. The trigger fires correctly; the detection it hands off to cannot see this case.

This also affects deleting a note from the notes panel, which removes the bookmark by the same route, so the fix should be about disappearance generally rather than about `PaintClear`.

Fixing this becomes more urgent alongside `fix-xs-annotation-draw-subscription`: once the fallback scan interval is restored to 30 s, a missing clear stops being a delay and becomes a visible, permanent divergence between peers.

## What Changes

- Track the (clip, frame) annotation keys the plugin has broadcast, together with the bookmark each came from.
- On each scan, diff that record against the bookmarks that currently exist, and broadcast an empty `REPLACE_ANNOTATION_COMMANDS` for every key whose bookmark has disappeared.
- Run that diff *before* the existing early return for an empty bookmark list — the all-bookmarks-deleted case is exactly the one that must not be skipped.
- No new wire message: the receive side already specifies an empty replace as an authoritative hard clear, in both RV and xStudio.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `annotation-lifecycle-sync`: xStudio's deletion detection currently requires a surviving bookmark; it must also detect a bookmark that has been removed entirely, which is what a clear normally produces.

## Impact

- `xstudio_plugin/ori_sync/annotation_sync.py` — `flush_pending_annotations` (early return, scan bookkeeping) and `broadcast_local_bookmark` (recording what was broadcast for which key).
- Peers receive an empty `REPLACE_ANNOTATION_COMMANDS` where today they receive nothing; no protocol or schema change.
- Interacts with `fix-xs-annotation-draw-subscription` task 5.1 (restoring the 30 s fallback interval) — that change should land after this one, or the window where a clear goes unsynced gets longer.
- Risk to be designed around: a key must not be treated as deleted while xStudio is mid-edit or has not yet committed a bookmark, or the plugin will broadcast a spurious clear.
