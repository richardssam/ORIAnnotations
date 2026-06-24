## Context

A sync session recording (produced by `SyncRecorder`) is a JSONL log of RabbitMQ messages exchanged during a review session. It contains information about playback status (`playing`, `current_time`), selections (`clip_guid`, `view_mode`), and drawing events. To review this session offline as a continuous visual experience, we need to convert this recording into a single, continuous OTIO timeline.

## Goals / Non-Goals

**Goals:**
- Implement a command-line tool `sync_recorder/convert_recording_to_timeline.py` that outputs a continuous OTIO timeline.
- Reconstruct the presenter's play, pause, scrub, and clip-switching actions over wall-clock time.
- Represent pause/scrub periods as freeze frames using standard OTIO `LinearTimeWarp(time_scalar=0.0)` effects.
- Parse `STATE_SNAPSHOT` events to retrieve the template timelines, tracks, and media reference metadata.
- Ensure a modular design that facilitates rendering drawing overlays in Phase 2.

**Non-Goals:**
- Rendering drawing overlays to transparent PNG files (deferred to Phase 2).
- Resolving network synchronization conflicts (the JSONL logs the resolved master actions, which we assume is the source of truth).
- Live-playing the session (handled by `SyncPlayer`).

## Decisions

### D1: Freeze-frame representation using `LinearTimeWarp`
- **Choice**: Use `LinearTimeWarp(time_scalar=0.0)` on a clip referencing the original media with a 1-frame source duration, stretched to the wall-clock duration of the pause.
- **Rationale**: Keeps the timeline lightweight and media-agnostic. We don't need access to local media files or external image extraction tools (like ffmpeg/OIIO) during conversion.
- **Alternatives Considered**: Extracting and rendering media frames to static temp files. Rejected because it is slow and requires local availability of original media.

### D2: Chronological Segment Merging
- **Choice**: Aggregate consecutive wall-clock ticks with identical playback and active clip properties into a single visual segment.
- **Rationale**: Reduces the output OTIO structure size. If a user plays a clip for 10 seconds, this is represented by one OTIO clip rather than 240 individual 1-frame clips.
- **Alternatives Considered**: Frame-by-frame clip generation. Rejected due to poor performance and bloated OTIO sizes.

### D3: Data structure readiness for Phase 2 (drawing overlays)
- **Choice**: The event parser outputs `VisualSegment` objects, which track the start/end wall-clock time, active media, and a chronological sub-list of drawing events (`PaintStart`, `TextAnnotation`, etc.) that occurred during that segment.
- **Rationale**: Enables Phase 2 overlay generation to easily scan each segment, group drawing events by state, and build the overlay track without altering the core parser logic.

## Risks / Trade-offs

- **Risk**: Some OTIO target applications may not fully support `LinearTimeWarp` or may treat `time_scalar=0.0` as an invalid value.
  - *Mitigation*: This is the standard OTIO method for representing freeze frames. If an application fails to parse it, it will typically fall back to playing the clip at normal speed (graceful degradation).
- **Risk**: Timing drift between the recording's wall-clock time and the media's playback speed.
  - *Mitigation*: Playback segments will map directly to the `current_time` range reported by the playback settings. Wall-clock duration will define the segment's length in the output track.
