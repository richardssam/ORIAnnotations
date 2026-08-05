## Context

The sync protocol describes "what a peer is viewing" with two independent
messages: `SELECTION_1.0` (`clip_guid`, `view_mode`) and `PLAYBACK_SETTINGS_1.0`
(`timeline_guid`, `current_time`, `playing`, `looping`). Both the RV and xStudio
plugins broadcast and consume both, on separate code paths.

These two channels can describe contradictory state, and each is broadcast and
applied independently, so applying one peer's message mutates local state and
re-broadcasts it. Observed failures during development:

- **Selection swap loop** — peers starting on different clips endlessly exchange
  selections (each applies the other's, re-broadcasts its own).
- **Bin-vs-sequence guid mismatch** — a clip in the bin and the same media in the
  sequence are distinct OTIO objects with distinct sync guids; only the sequence
  guid is shared, so a bin selection cannot be matched by a sequence-only peer.
- **Clip-start scrub jumps** — scrubbing across a clip boundary fires a selection
  that seeks the follower to the clip start, fighting the playhead position.
- **timeline_guid ambiguity** — a transient per-clip-timeline guid is broadcast
  instead of the sequence guid, so the follower ignores the position update.

Mitigations accreted so far (per-clip echo guards, guid normalization, rolling
suppression windows, skip-during-playback) are point patches over the underlying
two-source-of-truth design.

Constraint: the code is not deployed to anyone else, so we can hard-cut the
protocol without versioning or backward compatibility.

## Goals / Non-Goals

**Goals**
- One authoritative message describing a peer's view state.
- Make the active clip a *derived* property of the playhead position in sequence
  mode, so per-peer clip-guid mismatches cannot cause divergence.
- One broadcast path, one apply path, one echo-suppression window per plugin.
- Remove `SELECTION_1.0` and the subsumed selection guards/normalization.
- Keep RV and xStudio behavior identical under the new model.

**Non-Goals**
- Backward compatibility / mixed old-new sessions (explicitly unsupported).
- Reworking structural sync (ADD/REMOVE/REPLACE timeline, INSERT_CHILD), color,
  or display sync.
- Solving the broader "single poll thread blocks on xStudio I/O" performance
  theme (tracked separately); this change only removes the selection-channel
  contribution to it.

## Decisions

### D1: Extend `PLAYBACK_SETTINGS_1.0` rather than introduce a new schema
The playback message already carries `timeline_guid`, `current_time`, `playing`,
`looping`. Adding `view_mode` and `clip_guid` makes it the complete view-state
with minimal churn and no parallel channel.
- *Alternative considered:* a brand-new `VIEW_STATE_1.0` schema. Rejected — more
  surface area, and the playback message is already the natural carrier of
  position+timeline.

### D2: Position is authoritative in sequence mode; clip is derived
In sequence mode the receiver seeks to `current_time` and computes the active
clip by walking the track's `source_range` durations. `clip_guid` is sent for
confirmation/highlight only and never drives a seek.
- *Why:* a frame in a sequence unambiguously identifies a clip, and it is the
  same on every peer regardless of per-peer clip guids. This structurally
  eliminates the guid mismatch and the clip-start jump (no selection-driven
  seek), and lets two peers converge on a frame instead of swapping clips.
- *Alternative considered:* trust the sender's `clip_guid` to select. Rejected —
  reintroduces dependence on guids matching across peers.

### D3: Clip is authoritative in source mode
Single-clip/source view isolates one clip; position alone does not say which, so
`clip_guid` is authoritative and `current_time` is the in-clip offset.

### D4: One compute/broadcast path, one apply path, one echo window
Every view-affecting xStudio event (playhead `attribute_changed`, show_atom /
on-screen change, Pinned-Source-Mode transition) funnels into a single
`compute_view_state()` that builds the message and broadcasts if changed
(debounced). A single `apply_view_state()` sets mode + source + position
atomically. A single suppression window, set on apply, makes `compute_view_state`
skip broadcasting the local events the apply triggers.
- *Why:* the swap/echo loop and the clip/frame disagreement are only possible
  because there are multiple broadcast/apply paths and partial echo guards.
  Collapsing to one of each with one window removes the failure mode by
  construction.

### D5: Hard cutover, no compatibility
Delete `SelectionSet`, its registry entry, `broadcast_selection`, and the
`selection_changed` action. Update RV and xStudio plugins together.

## Risks / Trade-offs

- **Frame→clip derivation correctness** (off-by-one, gaps, mixed rates) → derive
  by summing integer `source_range` durations on the video track, consistent
  with the existing 0-based protocol frame convention ([sync_frame_base]); unit
  scenarios in specs cover boundary crossings.
- **Source-mode vs sequence-mode frame base divergence** → define explicitly:
  sequence-mode `current_time` is sequence-relative; source-mode `current_time`
  is clip-relative. Keep both plugins consistent.
- **Echo-window tuning** (too short → residual echo; too long → suppresses a
  genuine fast local change) → reuse the ~0.4s rolling-window approach already
  validated for scrub-position echo suppression; refreshed on each apply.
- **Both plugins must land together** (hard cutover) → implement and verify
  xStudio↔xStudio first, then RV, then RV↔xStudio, before considering done.
- **Loss of an explicit "selection without playhead move" case** → in practice
  xStudio moves the playhead to a selected clip, so position tracks selection;
  if a pure-highlight-without-seek case is ever needed, `clip_guid` already
  rides along and can drive highlight-only.

## Migration Plan

1. Extend `PlaybackSettingsSet`; delete `SelectionSet` + registry entry +
   `broadcast_selection` / `selection_changed`.
2. xStudio plugin: add `compute_view_state` (broadcast) and `apply_view_state`
   (apply) with frame→clip derivation; remove `apply_selection`,
   `resolve_and_broadcast_selection`, and the subsumed echo guards/normalization.
3. Verify xStudio↔xStudio: sequence select, double-click to source, scrub across
   clips, bin vs sequence selection — all converge, no swap/jump.
4. Mirror in the RV plugin; verify RV↔xStudio.
5. Cleanup pass: remove `[POLL-SLOW]`, `[2F-DIAG]`, rebuild-timing, and normalize
   diagnostic logging.

No rollback path beyond reverting the change set (hard cutover by design).

## Open Questions

- Does xStudio expose a cheap "frame → clip at playhead" query we should prefer
  over summing OTIO `source_range` durations? Default to the OTIO-derived
  computation unless a direct API is clearly cheaper and reliable.

## Resolved

- **`looping` field:** keep it in the unified view-state message. It is carried
  today and the playback/view-state model is expected to be extended in the near
  future, so the field stays as part of the message contract.
