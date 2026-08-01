## 1. Record what was broadcast

- [ ] 1.1 Add an in-memory map of broadcast annotation keys — `(clip_guid, frame)` → bookmark uuid — to `AnnotationSyncController`, cleared in `reset()` alongside the other per-session state
- [ ] 1.2 Populate it in `broadcast_local_bookmark` at the point the key is resolved and the broadcast succeeds, covering the flat-playlist fallback path as well as the sequence path
- [ ] 1.3 Exclude bookmarks in `_our_bookmark_uuids` (created from remote annotations) so a remote peer's clear cannot echo back

## 2. Detect disappearance

- [ ] 2.1 In `flush_pending_annotations`, diff the recorded keys against the current bookmark uuid set **before** the `if not scan_uuids: return` early return
- [ ] 2.2 For each key whose bookmark is absent, broadcast an empty `REPLACE_ANNOTATION_COMMANDS` via the existing `broadcast_replace_annotation_commands`
- [ ] 2.3 Forget each key once its clear is broadcast, so later scans do not re-broadcast it
- [ ] 2.4 Decide and implement the guard against premature declaration — recorded-from-successful-broadcast only, plus a second consecutive absent scan if testing shows it is needed (see design.md Risks)

## 3. Verify

- [ ] 3.1 Draw, then clear, with `ORI_SYNC_LOG_FILE` set — confirm an empty `REPLACE_ANNOTATION_COMMANDS` is sent (`xstudio/scratch/annotation_clear_probe.py` drives draw-then-clear and reports the bookmark list at each step)
- [ ] 3.2 Confirm the same when the cleared annotation was the only bookmark in the session, i.e. the bookmark list is empty at scan time
- [ ] 3.3 Confirm a peer (RV and a second xStudio) actually clears the frame on receipt
- [ ] 3.4 Confirm deleting a note from the notes panel produces the same broadcast
- [ ] 3.5 Confirm no spurious clear when drawing, clearing, and redrawing rapidly on one frame
- [ ] 3.6 Confirm a remote peer's clear crosses once and does not bounce back
- [ ] 3.7 Resolve design.md's open question: what happens on a clear of a bookmark that also carries note text, which xStudio keeps rather than removes

## 4. Sequencing

- [ ] 4.1 Land before — or together with — `fix-xs-annotation-draw-subscription` task 5.1, which restores the 30 s fallback scan interval and would otherwise widen the window where a missed clear is invisible
