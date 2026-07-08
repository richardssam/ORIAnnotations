## Why

Live verification during `expand-playback-modes` surfaced that clip selection in xStudio only syncs to OpenRV under one specific interaction: double-clicking into an isolated single-clip view (`view_mode: "source"`). Just selecting/highlighting a different clip while remaining in the full sequence view (`view_mode: "sequence"`) — the more common editorial interaction — is silently dropped: the wire message carries the new `clip_guid`, but OpenRV's receive path only reacts to `clip_guid` changes when `view_mode == "source"`, so in sequence mode it just records the value and does nothing with it. There's prior history here — `docs/clip_selection_sync_status.md` documents an extensive, ultimately-abandoned earlier attempt at xStudio→RV clip selection that was disabled due to an echo/oscillation bug — so this needs a fresh, carefully-scoped design rather than re-enabling the old path.

> **Outcome note (post-implementation).** The original premise — that "selecting a clip while remaining in the sequence view" is a distinct, reflectable signal — was **disproved during live testing**: in sequence view xStudio's `clip_guid` tracks the clip under the *playhead* (emitted on media-change), and there is no separate "user selected a clip" signal (the `Timeline.selection` actor stays empty). It changes on scrubbing, playback, and connect, so a receiver can't distinguish selection from playhead motion. That goal was therefore **reverted**; the receiver stays on the sequence. What this change actually delivered is a set of fixes that make the **isolated-clip (source-mode) selection workflow** — double-clicking / "select a clip" in xStudio — reflect reliably in RV and be reviewable. The sections below describe both the original intent (for history) and the delivered scope; `design.md` decisions D1–D3 record the pivot and D4–D8 the delivered work.

## What Changes

**Reverted (not achievable with the current wire data):**
- Sequence-mode clip highlight on the receiver. `PLAYBACK_SETTINGS_1.0`'s sequence-mode `clip_guid` is playhead-derived, so the receiver deliberately does **not** act on it — it stays on the sequence. (D3.)

**Delivered — reliable isolated-clip (source-mode) selection + review playback:**
- **Reliable source-mode broadcast:** xStudio now broadcasts an isolated-clip selection from the (reliably-fired) selection-actor event / single-clip state, not the flaky `show_atom` media-change that previously missed ~1-in-4 double-clicks and lagged 1–2 s. (D4.)
- **Fresh Pinned-Source-Mode read:** the first "select a clip in the timeline" is no longer mislabelled as `mode=sequence` against a stale cached PSM. (D5.)
- **Frame-reset on isolation:** isolating a clip seeks both peers to the clip's first frame, instead of each restoring its own last-viewed position in that clip. (D6.)
- **Loop mode for isolated clips:** short review clips now loop on both peers instead of the engine's per-clip Play Once default flashing them past — with send- and receive-side guards to stop a loop↔play-once oscillation. (D7.)
- **xStudio timeline item-highlight** re-enabled behind a timeline-stability guard against the actor-teardown segfault (retained; now exercised by the source-mode path). (D2.)
- **Freeze fix:** all `active_playhead` reads/writes on the selection path are bounded so a stale actor (torn down at a clip's end) can't wedge the poll thread for ~100 s. (D8.)

No new wire message — all of the above reuse `PLAYBACK_SETTINGS_1.0`'s existing `view_mode`/`clip_guid`/`current_time`/`playback_mode` fields.

## Capabilities

### New Capabilities
- `xstudio-clip-selection-sync`: reliable sync of the **isolated-clip (source-mode) selection** workflow — double-click / "select a clip" in xStudio reflects in RV, seeks to the clip's first frame, and loops for review — plus the deliberate decision that sequence-mode `clip_guid` is *not* actioned by the receiver.

### Modified Capabilities
(none — reuses existing `PLAYBACK_SETTINGS_1.0` fields; only their send/receive handling changes)

## Impact

- `xstudio_plugin/ori_sync/playback_sync.py`: the bulk of the change — `resolve_and_broadcast_selection` (reliable broadcast + fresh PSM), `broadcast_view_state` (frame-reset + loop-force, bounded), `_get_playback_mode`/`apply_playback_state` (loop guards), `_highlight_timeline_item`/`_timeline_recently_rebuilt` (stability-guarded highlight), `current_playback_state` (bounded reads).
- `rvplugin/ori_sync/playback_sync.py::_apply_playback`: sequence-mode `clip_guid` change is deliberately not actioned (reverted to pre-proposal behaviour); source-mode isolation path unchanged.
- `tests/otio_sync/test_playback_view_dispatch.py`: new regression coverage for RV's view dispatch.
- `docs/clip_selection_sync_status.md`: historical context for the abandoned xStudio→RV navigation attempt; can be retired now that the sequence-mode goal is formally not pursued.
- Now **in scope** (was originally deferred): the per-clip `Playhead` "Loop Mode" default — addressed for the isolated-clip case (D7). Known residual: the *first* playthrough of a freshly double-clicked clip still plays once before Loop lands (a race with xStudio's native auto-play); accepted as a minor cosmetic limitation.
