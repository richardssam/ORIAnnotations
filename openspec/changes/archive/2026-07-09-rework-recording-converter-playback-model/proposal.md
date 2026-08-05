## Why

`convert_recording_to_timeline.py` reconstructs playback by writing the protocol's `current_time` (a 0-based **view** frame) directly into each output clip's `source_range`. When a clip's `source_range is None` — a legitimate OTIO state meaning "use the whole `available_range`", which is how hosts like xStudio carry embedded timecode — the raw view frame does not address the media at all. In the sample recording the media's real frames are `98499–98600`, but the converter emits source frames `0–351`, so every background clip is out of range: the player shows black and the (correctly-shaped) annotation overlays land on the wrong picture. The converter also fakes sequence traversal with a wall-clock-advanced playhead, so it cannot follow a multi-clip sequence or wrap when playback reaches the end in loop mode.

## What Changes

- Introduce an explicit **playback projection model** that the converter maintains while replaying events: it owns the active timeline/sequence, the view→(clip, media-frame) resolution, and the current playhead so segments are derived from model-state transitions rather than ad-hoc wall-clock arithmetic.
- **Map view frames to media source frames** through the clip's *effective range* — `source_range` when present, else `media_reference.available_range` — mirroring `_clip_effective_range()` already used by the RV/xStudio sync code. This fixes both the black-media and wrong-frame-annotation symptoms. **BREAKING** for existing output `.otio` files (source ranges change coordinate space).
- **Sequence traversal**: while a playing segment advances by wall clock, when the playhead crosses the current clip's effective-range boundary, close the current output clip and open the next clip in the sequence, addressing the correct media each time.
- **Loop-mode wrap**: when an advancing playhead reaches sequence end and `playback_mode == "loop"`, wrap the model back to sequence start and continue emitting segments (the "reload the current clip/sequence" behaviour).
- Align annotation-overlay frame resolution to the same model, so overlays sit on the media frame they were drawn on.

## Capabilities

### New Capabilities
_None — this reworks an existing capability._

### Modified Capabilities
- `recording-to-timeline`: the playback-mapping requirement changes from "moving source ranges" over a single background clip to a projection-model-driven mapping that (a) resolves view frames to media frames via effective range, (b) traverses multi-clip sequences, and (c) wraps on loop-mode sequence end. Freeze-frame representation is retained but re-anchored to the resolved media frame.

## Impact

- **Code**: `sync_recorder/convert_recording_to_timeline.py` (core rewrite of the segment/playhead logic); likely a small shared/effective-range helper so the converter and the RV/xStudio plugins agree on view→media mapping.
- **Tests**: `tests/otio_sync/test_convert_recording.py` — add cases for `source_range=None` effective-range mapping, multi-clip sequence traversal, and loop-mode wrap. Existing fixtures asserting the old view-frame source ranges will need updating.
- **Fixtures**: current sample recording (`demo-otioconvert-1.jsonl`) is a weak test (single clip, loop never fires, no selection switch); a recording that runs off the sequence end while looping and switches clips is needed to exercise the new behaviour.
- **Output format**: emitted `.otio` source ranges move into media-frame space — downstream consumers of previously-generated timelines must regenerate.
