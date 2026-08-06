## Why

Both peers are equal authorities over everything, and the cost of that has become the dominant source of sync bugs. Echo suppression alone runs to ~15 mechanisms across the two plugins (see `session-roles`). In one debugging session on 2026-08-05, three more were added to the xStudio plugin and each fix uncovered another broadcast path that did not consult the guard:

1. the guard was armed only where a seek was applied, so every declined message left it unarmed;
2. the throttled scrub flush released a position captured before a peer began driving;
3. `broadcast_view_state` consulted no guard at all;
4. and `_new_source_clip` then overrode the withheld position 2 ms later.

Three of the underlying faults were not echoes but **inferences**: code deciding whether a local state transition had been caused by a user or by a peer, and acting on the guess. A `Pinned Source Mode` transition was read as a double-click and broadcast `playing=true`; a new source-clip isolation forced frame 0 over a seek that had just landed. Both inferences are correct for a local action and wrong for a peer-driven one, and the code cannot reliably tell them apart.

Underneath that sits a deeper assumption: that RV must independently *derive* a view equivalent to xStudio's, rather than simply *show what xStudio is showing*. That is what makes the inference necessary at all. In the same session OpenRV — as the driver — isolated itself into source view on the wrong clip while xStudio held the correct one, with both reporting the same timeline name so nothing detected it.

**A candidate second instance did not hold up, and is recorded here so it is not re-adopted as evidence.** `otio_xstudio_timeline_changes` (two xStudio instances) appeared to show the same divergence — its observed line carried `media='graphic…' view=source` against `media='car…' view=sequence`. On investigation the failing assertion was only the timeline *name*, and the cause was a harness reporting flaw rather than a sync fault: `get_xstudio_state` reported the focused container's name, which is the timeline when a timeline is focused and the bin when a bin is, so two synced peers reported different levels of the same hierarchy. Resolving the name through the shared sync GUID fixed it and the test now passes. The `media`/`view` difference was visible in the log but never asserted on that path, so it remains **unestablished** — it may have been a genuine divergence or a sampling artefact during a rebuild.

The distinction matters for how much weight the remaining evidence carries. In the `xstudio_selects_script_rv` case above, the differing media was *asserted and failed*; here it was merely displayed. Only the former is evidence.

Symmetric authority is not required by the product. In a review session one person drives what everyone looks at; the others scrub, annotate, and play within it.

## What Changes

- Split broadcast authority by category rather than treating all navigation alike:
  - **Visibility** — which clip/sequence is on screen and in which view mode — is owned by the session **host**. Only the host may broadcast it.
  - **Position** — scrub, play, stop, zoom, brightness, channel switch — remains open to every peer.
  - **Annotation** — unchanged, multi-writer.
- Elect the host by peer capability rather than fixing it to one application: xStudio is preferred when present; an RV peer hosts an RV-only session. "xStudio drives" is the common case, not the rule.
- Establish a follower rule: a peer that does not own a category **never infers local user intent from a state transition** in that category. It applies what it is told and broadcasts nothing.
- Make a follower *mirror* the host's visibility rather than derive its own equivalent — removing the autonomous view-selection logic that produced the wrong-clip divergence.
- Retire `xstudio_selects_script_rv`, whose premise (RV driving selection) contradicts this model. `xstudio_selects_script_xstudio` already covers the intended topology.

## Capabilities

### New Capabilities
- `session-visibility-authority`: broadcast categories and their authority model; host election by capability; the follower rule that forbids inferring local intent; visibility mirroring.

### Modified Capabilities
- `otio-sync-core`: category-aware enforcement inside `broadcast_*`, so authority is checked at one point rather than replicated per plugin.
- `openrv-sync-plugin`: as a follower, stop deriving view state independently and stop broadcasting visibility.

## Impact

- `python/otio_sync_core/manager.py`: category table and the enforcement check in `broadcast_*`.
- `rvplugin/ori_sync/playback_sync.py`: `on_view_changed` no longer broadcasts visibility when not host; follower mirrors incoming view state.
- `xstudio_plugin/ori_sync/playback_sync.py`: the intent inferences (`playing_override`, `_new_source_clip` frame reset, PSM handling) become host-only paths, so the peer-driven branches they guess about cannot arise.
- `sync_test/sync_tests.yaml`: retire `xstudio_selects_script_rv`.
- Deliberately **not** a prerequisite for `session-roles`, and not blocked by it: this uses the same enforcement point (`SyncManager.broadcast_*`) and the same vocabulary, so the lease mechanism can be added later underneath a policy that is static today.
