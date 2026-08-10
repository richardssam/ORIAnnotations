## Why

OpenRV labels every playback broadcast with `_rv_node_to_timeline_guid.get(view) or sync_manager.active_timeline_guid`. That map only holds *sequence* groups, so whenever OpenRV is isolated on a clip the fallback labels a source-view position with the **sequence's** guid.

The frame in that message is view-relative — logs show `base=1` in sequence view against `base=96899`/`89899`/`100` in source view — so `timeline_guid` is the only thing telling a peer which coordinate space a position belongs to. xStudio already guards on it ("mismatched timeline_guid — ignoring"), but a mislabelled message passes the guard, and a clip-local frame gets applied to the sequence.

Observed: with an xStudio host showing a sequence, isolating a clip in OpenRV that is *in* that sequence jumps xStudio's sequence playhead. Isolating a clip that is *not* in it is harmless only by accident — OpenRV cannot resolve a clip guid, so no view-state broadcast fires at all.

## What Changes

- Resolve the broadcast `timeline_guid` through `_displayed_view()` — the same reader the apply path already uses — instead of the node-map-or-active-timeline fallback.
- A source view is therefore labelled with the isolated clip's own timeline guid, and with `None` when that clip is not shared with the session, so peers can tell "position in a view you don't have" from "position in your sequence".
- Sequence views keep their existing resolution (node map, then the OTIO stack's inner sequence group), so sequence-following is unaffected.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `openrv-sync-plugin`: "Synchronized Playback" gains the rule that the broadcast timeline guid identifies the **displayed** view, and that a position from an unshared view is not attributed to a shared timeline.

## Impact

- **Code**: `rvplugin/ori_sync/playback_sync.py` (`_broadcast_playback`). One resolution site; `_displayed_view()` already exists and is already used by the apply path.
- **Peers**: xStudio's existing mismatch guard starts rejecting OpenRV's source-view positions — the intended outcome. No xStudio change required.
- **Prior art**: an earlier attempt deleted the fallback outright, which sent `timeline_guid=None` from *every* view. That suppressed this symptom but broke sequence adoption on peers, and was reverted. This change fixes the label rather than removing it.
