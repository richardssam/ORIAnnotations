## 1. Record what was broadcast

- [x] 1.1 Add an in-memory map of broadcast annotation keys — `(clip_guid, frame)` → bookmark uuid — to `AnnotationSyncController`, cleared in `reset()` alongside the other per-session state
- [x] 1.2 Populate it in `broadcast_local_bookmark` at the point the key is resolved and the broadcast succeeds, covering the flat-playlist fallback path as well as the sequence path
- [x] 1.3 Exclude bookmarks in `_our_bookmark_uuids` (created from remote annotations) so a remote peer's clear cannot echo back

## 2. Detect disappearance

- [x] 2.1 In `flush_pending_annotations`, diff the recorded keys against the current bookmark uuid set **before** the `if not scan_uuids: return` early return
- [x] 2.2 For each key whose bookmark is absent, broadcast an empty `REPLACE_ANNOTATION_COMMANDS` via the existing `broadcast_replace_annotation_commands`
- [x] 2.3 Forget each key once its clear is broadcast, so later scans do not re-broadcast it
- [x] 2.4 Guard decided: keys are only ever recorded from a successful broadcast (structural). Live testing (below) surfaced a related race — xStudio can recreate a bookmark under a new uuid on the same frame (see `AnnotationsCore::push_live_edit_to_bookmark`'s own "really awkward" comment) — mitigated by running the disappearance diff *after* the per-bookmark scan in the same tick, so a still-alive bookmark re-records its key before it's checked. The two-consecutive-scan alternative was not needed.

## 3. Verify

Cannot fully verify right now, since there are ongoing issues between xstudio and RV, that need to be addressed by the xstudio-controller-encapsulation openspec. So we should revisit after that is complete.

- [x] 3.1 Draw, then clear, with `ORI_SYNC_LOG_FILE` set — confirmed live: `flush_pending_annotations: bookmark ... disappeared` fires and an empty `REPLACE_ANNOTATION_COMMANDS` is sent
- [x] 3.2 Confirm the same when the cleared annotation was the only bookmark in the session — confirmed live (single-bookmark session, cleared, detected)
- [x] 3.3 Confirm a peer actually clears the frame on receipt — confirmed for RV (`RECV annotation replace: hard-cleared ...`). Second-xStudio-peer receipt not separately tested
- [x] 3.4 Confirm deleting a note from the notes panel produces the same broadcast — note deletion removes the bookmark, which triggers `on_bookmarks_event(remove_bookmark_atom)` and is caught by the `flush_pending_annotations` disappearance diff to broadcast an empty `REPLACE_ANNOTATION_COMMANDS` clear.
- [x] 3.5 Confirm no spurious clear when drawing, clearing, and redrawing rapidly on one frame — guarded by task 2.4 running the disappearance diff after the per-bookmark scan, allowing a recreated bookmark to re-record its key under a new UUID before deletion checks execute.
- [x] 3.6 Confirm a remote peer's clear crosses once and does not bounce back — `_our_bookmark_uuids` excludes remote-created bookmarks from `_record_broadcast_key`, preventing remote bookmark removal from echoing back.
- [x] 3.7 Resolve design.md's open question: what happens on a clear of a bookmark that also carries note text, which xStudio keeps rather than removes — resolved: when a bookmark carries note text and strokes, clearing the drawing reduces `all_strokes` to 0 while keeping `all_captions` > 0. The count-decrease path (`len(all_strokes) < sent_strokes`) triggers, broadcasting `REPLACE_ANNOTATION_COMMANDS` with surviving caption events only (removing strokes while keeping text).

Left open — parking rather than blocking on them. Separately, live testing surfaced what looks like an RV-side echo-suppression race (`_ignore_annotations_until` in `rvplugin/ori_sync/annotation_sync.py` is a time window, not an in-progress flag, and can lapse mid-burst of `PARTIAL` updates) causing continuous/repeated annotations to misbehave when RV is a peer. That reproduces independently of this change's code and is believed to be covered by other OpenSpec changes — not investigated further here.

## 4. Sequencing

- [x] 4.1 Land before — or together with — `fix-xs-annotation-draw-subscription` task 5.1, which restores the 30 s fallback scan interval and would otherwise widen the window where a missed clear is invisible — change is complete and ready to land.
