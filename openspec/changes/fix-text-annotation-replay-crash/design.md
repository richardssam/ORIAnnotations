## Context

`sequence_sync.py`'s "Pass 4: replay annotations" step of `_rebuild_rv_session` (run for every peer that just joined, inside the `on_synced` callback chain) walks each annotation clip's `annotation_commands` and, for a `TextAnnotation`, does:

```python
paint_node = self.plugin.annotation._find_paint_node_for_media(media_path, frame) if media_path else None
if paint_node and self.plugin.annotation._text_uuid_exists_in_rv(paint_node, frame, uuid_val):
    _log(f"  _rebuild_rv_session: skip dup text uuid={uuid_val[:8]!r}")
    continue
```

`_text_uuid_exists_in_rv` does not exist. Live-tested via the `sync_test` harness's `draw_annotation(kind="text")` action followed by a second RV instance joining: the join log shows

```
on_synced callback error: 'AnnotationSyncController' object has no attribute '_text_uuid_exists_in_rv'
```

`SyncManager._set_status` catches this — it wraps every registered `on_synced` callback in a bare `try/except Exception` purely so one broken callback can't wedge the state machine — but that means the exception is absorbed several call frames above where it's actually thrown, and everything the callback would otherwise have done after the failure point (remaining annotation clips, then `plugin.py::_on_synced`'s playback-state/display-state/color-sync application, which all run *after* `rebuild_rv_session()` returns) never runs for that join. A session with no text annotations never reaches this line, which is presumably why it went unnoticed until this change's verification step exercised it directly.

## Goals / Non-Goals

**Goals:**
- The duplicate-guard this line was clearly meant to perform (skip re-applying a text annotation that a live `INSERT_CHILD` already painted before the snapshot fully landed) actually works, by implementing the missing method.
- The call site correctly consumes `_find_paint_node_for_media`'s `(node, native_frame)` return (changed by `fix-annotation-render-on-join`).
- A failure replaying any single annotation clip during a join no longer prevents the rest of that join's setup from completing.

**Non-Goals:**
- No change to the duplicate-guard's *purpose* or when it fires — only making the existing, already-designed check actually execute.
- No broader audit of every `except Exception` in this codebase; scoped to the one blast-radius problem this bug exposed (per-event isolation in this specific replay loop).

## Decisions

**Implement `_text_uuid_exists_in_rv` by scanning `order`, not by adding new tracking state.** The paint node already exposes exactly what's needed: `<node>.frame:<frame>.order` lists every item at that frame, and each `text:` item has its own `.uuid` property (written by `_text_spec`). No new bookkeeping is needed — this mirrors how `_apply_annotation`'s pen path already reads `order` directly (`pen_items = [i for i in rv.commands.getStringProperty(order_prop) if i.startswith(("pen:",))]`) rather than maintaining a parallel uuid index.

**Wrap each event's replay individually, not the whole loop once.** A single `try/except` around the entire `for event in child.metadata["annotation_commands"]:` loop would stop at the first bad event and skip every *sibling* event in that same clip too (e.g. a clip with three text captions, one malformed, would silently lose the other two). Per-event isolation keeps a single bad event from taking out anything beyond itself, matching the granularity the surrounding code already reasons about (one event → one `_apply_*` call).

**Log and continue, don't re-raise.** This is a best-effort replay of potentially-stale historical annotation data on join; a malformed or unexpected event here should be visible in the log (for someone investigating a "missing annotation" report) but must not be allowed to block playback/display/color sync, which are unconditionally required for the joining peer to be usable at all.

## Risks / Trade-offs

- **[Risk]** Swallowing exceptions per-event could hide a real bug the same way the outer catch already did. → **Mitigation**: log at the same visibility level (`_log`) the rest of this file already uses for its diagnostic trail — this is a change in blast radius, not a change in whether the failure is observable; the log message names the event kind and clip so it's greppable the same way the crash that motivated this change was found.
- **[Trade-off]** `_text_uuid_exists_in_rv` re-reads `order` on every text event rather than caching it once per clip. Accepted: replay only runs once per join, not per frame of playback, so this isn't a hot path.

## Migration Plan

1. Land after `fix-annotation-render-on-join` (this depends on its `_find_paint_node_for_media` return-shape change).
2. Rebuild/reinstall the RV plugin package, same as the other change.
3. Re-run the text-annotation join scenario that surfaced this (`sync_test` harness: draw a text annotation, join a second RV, confirm no `on_synced callback error` in the joiner's log and that playback/display state applied normally).

## Open Questions

None — root cause and fix are both fully determined from the log evidence and code reading.
