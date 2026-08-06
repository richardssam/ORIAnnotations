## Why

Investigating a symptom where drawing a second annotation on an already-annotated clip+frame in xStudio, synced to RV, took 13.5 seconds to apply in RV (vs. 34ms for the first annotation on that clip+frame) — confirmed via `rv_client.log`: the raw MQ message arrived in 4ms, but RV's main Qt thread didn't process it for 13.51s, with total log silence in between (a main-thread stall, not a network delay).

Root cause traced to two related leak sites that both prune an RV paint item from the frame's `order` string property without ever deleting the item's own RV properties:

1. `rvplugin/ori_sync/annotation_sync.py::_cleanup_partial_debris` — sweeps a superseded mid-gesture partial-tick pen node out of `order` once a longer tick supersedes it, but never deletes that pen component's own properties (`.color`, `.width`, `.brush`, `.points`, etc.).
2. `python/otio_sync_core/rv_paint_applier.py::_apply_reconcile`'s prune step — removes a managed item's entry from `order` when its uuid is no longer present in the incoming reconcile batch, with the identical gap.

Every partial-annotation tick RV has ever received or reconciled away, for the lifetime of a running RV process (this repo's log files span weeks without an apparent restart), leaves a permanent orphaned property component on the source group node. RV's own native paint tool avoids this: `annotate_mode.mu::deleteStroke` in OpenRV explicitly calls `deleteProperty` on every sub-property of a stroke component when removing it, wrapped in `beginCompoundStateChange`/`endCompoundStateChange`. `rv.commands.deleteProperty` is a real, exposed RV command (confirmed in `rv_commands_setup.py`) — our code just never calls it.

## What Changes

- Add a shared helper that deletes every RV property of a paint item's component (mirroring OpenRV's own `annotate_mode.mu::deleteStroke` pattern: enumerate known sub-property names, call `commands.deleteProperty` on each, absorb "already gone" errors).
- Call that helper from `_apply_reconcile`'s prune step in `rv_paint_applier.py`, so reconcile-mode pruning (text/shape/pen alike) actually frees the pruned item's properties, not just its `order` reference.
- Call the same helper from `_cleanup_partial_debris` in `rvplugin/ori_sync/annotation_sync.py`, so superseded mid-gesture partial-tick pen nodes are actually deleted, not merely hidden.
- No protocol or behavioral change from a peer's point of view — pruned/superseded items already don't render today; this only stops them from silently persisting as orphaned graph state.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `rv-annotation-codec`: the "Reconcile prunes deleted annotations" requirement/scenario is extended — pruning now deletes the pruned item's own RV properties, not just its `order` reference.
- `openrv-sync-plugin`: the "Synchronized Annotations" requirement is extended — mid-gesture partial-tick cleanup (`_cleanup_partial_debris`) now deletes the superseded pen node's own RV properties, not just its `order` reference.

## Impact

- `python/otio_sync_core/rv_paint_applier.py` (shared codec applier, used by every RV call site: testchart batch, plugin import, live sync)
- `rvplugin/ori_sync/annotation_sync.py` (`_cleanup_partial_debris`)
- No change to wire protocol, OTIO schema, or xStudio-side code.
- Primary risk is scope of the property-name list used for deletion — it must match every property name the codec actually writes per item kind (pen/text/ellipse/rect/arrow), or deletion will be incomplete and the leak will only shrink, not close. See design.md.
