## Context

`convert_recording_to_timeline.py` replays a JSONL session recording into a flat "Background Media" track plus an "Annotations Overlay" track. It reconstructs playback by tracking a wall-clock-advanced `current_segment_playhead` and writing that value straight into each output clip's `source_range.start_time`.

The protocol's `current_time` is a **timeline/view coordinate** — a 0-based offset into whatever is loaded, where frame 0 is the first frame of the loaded view (see `[[project_sync_frame_base]]`). It is *not* a media/source frame. A clip whose `source_range is None` (a legitimate OTIO state meaning "use the whole `available_range`", used by xStudio to carry embedded timecode) maps timeline frame 0 onto a media frame like 98499. The RV/xStudio sync code already handles this with `_clip_effective_range()` ([rvplugin/ori_sync/utils.py:154](rvplugin/ori_sync/utils.py#L154)); the converter is an unaudited site that predates that fix.

Two consequences observed on the sample recording (`demo-otioconvert-1.jsonl`, media `available_range = [98499, 98600]`):
1. Background clips are written with source frames `0–351` — entirely outside the media's real range → player shows black.
2. Annotation overlays are correctly shaped and correctly *aligned to the segment*, but sit on the wrong picture because the background frame beneath them is wrong.

The converter also fakes sequence traversal (single-clip assumption) and does not implement loop-mode wrap, so it cannot follow a multi-clip sequence or a playhead that runs off the sequence end while looping.

Constraints:
- The recording format is fixed and wire-faithful — it stores view frames because that is the neutral cross-host coordinate. Resolution to media frames must happen in the converter, using the `STATE_SNAPSHOT` as the model.
- A recording is only interpretable together with its `STATE_SNAPSHOT` (and any subsequent `ADD_TIMELINE`/`REPLACE_TIMELINE`), which supply the real OTIO structure and each clip's range.

## Goals / Non-Goals

**Goals:**
- Emit background clips whose `source_range` correctly addresses media, by resolving view frame → media frame through the OTIO structure from the snapshot.
- Follow multi-clip sequences: as a playing playhead crosses a clip boundary, close the current output clip and open the next, addressing the correct media.
- Honor loop mode: when a playing playhead reaches sequence end and `playback_mode == "loop"`, wrap to sequence start and keep emitting.
- Anchor annotation overlays to the same resolved media frame.
- Keep the freeze-frame representation (`LinearTimeWarp(time_scalar=0.0)`) for pause/scrub, re-anchored to the resolved frame.

**Non-Goals:**
- Changing the recording format or what the recorder/`player.py` capture and replay (remains view-frame/wire-faithful).
- Rendering media frames to files, or resolving network sync conflicts (unchanged from the original design).
- Handling view modes beyond the `sequence` / `source` distinction already present in the protocol.

## Decisions

### D1: Resolve view→media against the snapshot's OTIO structure using native OTIO time transforms
- **Choice**: Treat the protocol view frame as a **timeline coordinate** and resolve it to media using the real `Timeline` parsed from the `STATE_SNAPSHOT` (already done into `timeline_map`). Use OTIO's own machinery — `track.child_at_time(view_time)` to find the clip under the playhead, and `track.transformed_time(view_time, clip)` to express that time in the clip's media/source space — rather than copying the view frame into `source_range` or hand-rolling frame arithmetic.
- **Rationale**: This is precisely the Timeline-vs-MediaReference mapping OTIO exists to do. `transformed_time` composes the clip's `source_range`/`available_range` offset automatically, so the `source_range is None` case, trims, and rate changes are handled without special-casing. `child_at_time` gives sequence traversal for free.
- **Alternatives considered**:
  - *Port `_clip_effective_range()` and do manual offset math* — works, but re-implements what `transformed_time` already does and must special-case `source_range is None`, multi-clip boundaries, and rate rescales by hand. Kept only as a fallback for clips OTIO can't transform (e.g. a bare clip with neither `source_range` nor `available_range`).
  - *Fix the recording to store media frames* — rejected: bakes in one host's timecode, breaks wire-faithful replay via `player.py`, and discards the neutral cross-host coordinate.

### D2: Maintain an explicit playback projection model, driven by event replay
- **Choice**: Keep a small model object holding `active_timeline_guid`, `active_view_mode`, `playing`, and the current **view time** (a `RationalTime` in timeline coordinates). Segments are emitted from *transitions* of this model (play↔pause, selection/timeline change, clip-boundary crossing, loop wrap), not from ad-hoc per-event arithmetic. On a `flush`, the model resolves the current view time → (clip, media range) via D1 and produces the output clip(s).
- **Rationale**: The user's three behaviours (play from here, play a sequence to its end, wrap on loop) are all state transitions of one model. Centralizing the state removes the scattered `current_segment_*` globals and the single-clip assumption baked into `get_clip_sequence_start_time`.
- **Alternatives considered**: *Keep the flat-track segment approach and only patch the frame value* — fixes the black-media symptom but leaves traversal/loop unfixable and keeps the coordinate confusion latent.

### D3: Sequence traversal by splitting a playing segment at clip boundaries
- **Choice**: When advancing a *playing* segment by wall clock, compute the view-time span covered; if that span crosses one or more clip boundaries in the active track (detected via `child_at_time` / `range_of_child`), split it into sub-segments — one output clip per underlying media clip, each with a moving `source_range` in that clip's media space.
- **Rationale**: Preserves the "one output clip per continuous media run" compaction (design D2 of the original) while making it correct across cuts.
- **Alternatives considered**: *Frame-by-frame emission* — simplest but bloats output; rejected for the same reason the original converter groups segments.

### D4: Loop-mode wrap
- **Choice**: While advancing a playing segment, if the view time reaches the active sequence's end (`tracks.duration()` in view space) and the last-seen `playback_mode == "loop"`, wrap the view time to the sequence start (0 / `frameStart`) and continue emitting from there. A non-loop end holds the last frame (existing freeze behaviour) until the next event.
- **Rationale**: Directly implements "if we are looping, reload the current clip/sequence into the timeline." Wrapping in view space keeps the media resolution (D1) unchanged across the wrap.
- **Alternatives considered**: *Ignore loop and let the playhead run past the end* — the current behaviour, which produces out-of-range source frames; rejected.

### D5: Annotation overlays resolve through the same model
- **Choice**: Resolve each annotation's target frame to a media frame using the same D1 path, so overlays sit on the picture they were drawn on. The existing overlay-track construction (grouping identical signatures into stretched clips + gaps) is retained.
- **Rationale**: The overlay shape is already correct; only the frame it anchors to is wrong, for the same reason as the background. One fix serves both.

## Risks / Trade-offs

- **[Snapshot required]** View frames are un-resolvable without the `STATE_SNAPSHOT` (or a later `ADD_TIMELINE`/`REPLACE_TIMELINE`) that carries the clip's range. → Fail loudly if a playback event references a timeline/clip with no known range, rather than silently emitting view-frame source ranges as today.
- **[View-mode ambiguity]** A view frame means "sequence position" in `sequence` mode but "offset into one clip" in `source` mode. → The model must branch on `active_view_mode`; in `source` mode, resolve against the selected clip directly instead of `child_at_time` on the sequence.
- **[frameStart base]** The view's zero may be `frameStart` rather than a literal 0 (see `[[project_sync_frame_base]]`). → Base the view→timeline mapping on the timeline's start, not a hard-coded 0; keep `global_start_time` handling consistent with the sync codec.
- **[Output format change]** Emitted `source_range`s move into media-frame space. → **BREAKING**: previously-generated `.otio` files must be regenerated; existing tests asserting view-frame source ranges must be updated.
- **[Weak fixture]** `demo-otioconvert-1.jsonl` never fires loop and never switches clips, so it can't validate D3/D4. → Add/synthesize a recording that runs off the sequence end while looping and switches clips before considering the change verified.

## Migration Plan

1. Land the view→media resolution (D1/D2) and update the existing test fixtures to media-frame source ranges; verify the sample recording renders real media under the annotations.
2. Add D3 (traversal) and D4 (loop) behind the same model; add fixtures that exercise them.
3. Regenerate any checked-in sample `.otio` outputs. No runtime rollback concern — the tool is offline and re-runnable; rollback = revert and regenerate.

## Resolved Decisions

- **Shared helper location** → `otio_sync_core`. The view→media resolution (and any effective-range fallback) lives in `otio_sync_core` so the converter uses it now and the RV/xStudio plugins can converge on the single implementation later, avoiding a third copy.
- **Source-view resolution** → In `source` view mode there is exactly one clip, so the media frame is a direct offset into that clip's effective range (`effective_range.start + view_frame`) — no `child_at_time`. The selected clip is assumed present in `template_clips` (it was selected from a snapshot timeline); if it is not found, **fail loudly** rather than emit a raw view frame.
- **Loop range** → On loop wrap, restart at sequence frame 0 (`frameStart`). Honoring an explicit in/out playback range is deferred; the protocol does not carry one today. Captured as a future task, not part of this change.

## Open Questions

_None outstanding — see Resolved Decisions above._
