## Context

RV represents each paint annotation (pen stroke, text caption, or shape) as a *component* — a group of properties under a shared prefix on the source group node, e.g. `defaultSequence_p_sourceGroup000000.pen:3:96899:remote.width`. There is no single "delete this component" RV command; OpenRV's own native annotate tool (`annotate_mode.mu::deleteStroke`) removes a stroke by calling `deleteProperty` on each of its known sub-property paths individually, wrapped in `beginCompoundStateChange`/`endCompoundStateChange`, absorbing errors for properties already gone. `deleteProperty` is a real RV command, confirmed present in `rv_commands_setup.py` and exposed to Python as `rv.commands.deleteProperty`.

Two places in this codebase remove a paint item from the frame's `order` string property (making it stop rendering/being enumerated) without ever calling `deleteProperty` on the item's own properties:

1. `python/otio_sync_core/rv_paint_applier.py::_apply_reconcile` — its prune step (lines ~205-217) drops an item from the rebuilt `order` list when the item's uuid isn't in the incoming reconcile batch. Used for text/shape/pen reconcile-mode updates (e.g. `annotation_sync._apply_annotation_replace`).
2. `rvplugin/ori_sync/annotation_sync.py::_cleanup_partial_debris` — sweeps a superseded mid-gesture partial-tick pen node out of `order` once a longer tick supersedes it (RV can't mutate an existing pen node's properties after creation, so every partial tick mints a fresh one; this was meant to clean up the previous tick's fresh one).

Both leave the underlying properties allocated on the node forever. Confirmed as the cause of a 13.51s RV main-thread stall applying a second annotation to an already-annotated clip+frame (vs. 34ms for the first) — traced via `rv_client.log`: the raw MQ message logged in 4ms, `apply_patch` didn't run for another 13.51s, total log silence in between (main-thread stall, not network).

## Goals / Non-Goals

**Goals:**
- Stop leaking RV properties at both prune sites, using RV's own established pattern (`deleteProperty` per named sub-property, errors absorbed).
- Single source of truth for "what properties does a `pen`/`text`/`ellipse`/`rect`/`arrow` item have" so the two call sites can't drift out of sync with each other or with `rv_annotation_codec.py`'s spec-writing functions.
- Confirm the fix actually closes the stall (re-run the two-stroke same-clip-frame test that originally measured 13.51s).

**Non-Goals:**
- Retroactively cleaning up properties already leaked in a long-running RV session before this fix lands — the fix only stops new leaks from this point forward. A full clean requires restarting RV (which resets the property graph).
- Changing wire protocol, OTIO schema, or any xStudio-side code — this is RV-local.
- General RV paint-node lifecycle refactor — scoped strictly to the two prune sites identified.

## Decisions

- **Add one shared helper, not two separate implementations.** `rv_paint_applier.py` already states its own charter: "the property-writing logic ... lives in exactly one place." A `_delete_item_properties(commands, rv_node, item)` helper belongs there for the same reason — it's the deletion counterpart of `_write_spec_props`. `_cleanup_partial_debris` in `rvplugin/ori_sync/annotation_sync.py` imports and calls it rather than maintaining its own property-name list, so the two sites can't drift.
- **Property-name lists keyed by item-name prefix (`pen`, `text`, `ellipse`, `rect`, `arrow`), derived from what `rv_annotation_codec.py`'s spec functions actually write** (`_pen_spec`, `_text_spec`, `_box_shape_spec` for both ellipse/rect, `_arrow_spec`), plus the pen-only extras `annotation_sync._apply_annotation` sets outside the codec (`uuid`, `hold`, `ghost`, `ghostBefore`, `ghostAfter`). Superset lists are safe: `deleteProperty` on a property that was never set for a given item is a no-op (wrapped in try/except, matching `annotate_mode.mu`'s try/catch-absorb pattern), so listing a slightly wider set than any one item actually has does not error.
- **Delete before removing from `order`, not after.** Matches `annotate_mode.mu::deleteStroke`'s ordering and keeps `order` as the single flag for "is this item still live" at every point in the sequence — no window where `order` already excludes an item but its properties still exist (or vice versa).
- **No `beginCompoundStateChange`/`endCompoundStateChange` equivalent.** That's a Mu/UI-undo-grouping concern for RV's interactive undo stack; nothing here has established an equivalent grouping construct in the Python `commands` API used elsewhere in this codebase, and undo grouping isn't necessary for this — a remotely-driven prune isn't a user-undoable action already tracked in this way. Not doing it introduces no behavioral gap.

## Risks / Trade-offs

- [Property-name list drifts from `rv_annotation_codec.py`'s spec functions if a future change adds a new property without updating the deletion list → the leak reopens for that one property] → Mitigation: keep the deletion lists directly adjacent to (or generated from) the codec's spec-building functions' property tuples where practical; call this out in a code comment at the deletion-list definition site pointing back at `_pen_spec`/`_text_spec`/`_box_shape_spec`/`_arrow_spec`.
- [Deleting properties on the same item concurrently with something else reading/writing them, e.g. a live-partial update racing a prune] → Existing code already assumes single-threaded RV-side annotation handling (Qt main-thread timer poll, no concurrent mutation); no new race is introduced beyond what already exists for `order` mutation.
- [Fix doesn't actually close the stall — the 13.5s cause turns out to be something else entirely] → Falsifiable directly: re-run the same two-stroke test that measured 13.51s before, in the same long-running RV process, after the fix lands.

## Migration Plan

No data migration. This only changes RV-local, in-memory graph state going forward. Existing long-running RV sessions still carry whatever was already leaked before this fix is deployed — restarting RV is the only way to reclaim that (call this out to the user, don't attempt an automated sweep of pre-existing leaked components; scope is capped to stopping new leaks, see Non-Goals).

## Open Questions

- None blocking. If the fix lands and the stall persists, the next step is re-reading `rv_client.log` with the same before/after mindset used in the pen-pressure investigation, since that would mean the stall's cause isn't (only) this leak.
