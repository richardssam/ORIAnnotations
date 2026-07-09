# recording-to-timeline Specification

## Purpose
TBD - created by archiving change convert-recording-to-timeline. Update Purpose after archive.
## Requirements
### Requirement: Convert JSONL session recording to OTIO timeline structure
The tool MUST parse a JSONL session recording containing `STATE_SNAPSHOT`, `SelectionSet`, and `PlaybackSettingsSet` events and output a valid `.otio` timeline. The tool MUST maintain a playback projection model over the OTIO structure carried by the snapshot (and any subsequent `ADD_TIMELINE`/`REPLACE_TIMELINE`), and derive output segments from transitions of that model. All emitted background-clip source ranges MUST be expressed in the referenced media's own frame space (see "Resolve view frames to media frames"), not in timeline/view frames.

#### Scenario: Parse snapshot and write background track
- **WHEN** the CLI tool is executed with a valid session recording containing a `STATE_SNAPSHOT`
- **THEN** the output OTIO timeline contains a "Background Media" track with clips populated from the snapshot's timelines

#### Scenario: Map playback changes to OTIO timeline cuts
- **WHEN** the user plays or pauses during the recorded session
- **THEN** the output timeline splits the background track at the wall-clock boundaries, mapping normal playback segments to source ranges that advance through the referenced media's frame space, and pause/scrub segments to freeze-frame clips anchored to the resolved media frame

### Requirement: Freeze-frame representation
The tool MUST represent pause and scrub segments using 1-frame source clips stretched to the segment's wall-clock duration with a `LinearTimeWarp(time_scalar=0.0)` effect. The single held frame MUST be the media frame produced by resolving the segment's view frame through the active clip (see "Resolve view frames to media frames"), not the raw view frame. Annotation overlay clips MUST be anchored to the same resolved media frame so overlays sit on the picture they were drawn on.

#### Scenario: Reconstruct pause segment on a clip with embedded timecode
- **WHEN** a 5-second pause at view frame 100 is processed at target 24fps, on a clip whose media `available_range` starts at frame 98499 (with `source_range == None`)
- **THEN** the output track contains a clip of 120 output frames pointing to the media, whose `source_range` starts at the resolved media frame 98599 (98499 + 100), with a `LinearTimeWarp` effect of `time_scalar = 0.0` holding that frame — not the raw view frame 100

#### Scenario: Annotation overlay sits on the drawn frame
- **WHEN** an annotation was drawn while paused at a view frame that resolves to media frame F
- **THEN** the corresponding annotation overlay clip is aligned over the background segment showing media frame F

### Requirement: Resolve view frames to media frames
The tool MUST treat each playback event's `current_time` as a timeline/view coordinate and resolve it to a media frame using the OTIO structure from the snapshot, rather than writing the view frame directly into `source_range`. Resolution MUST honor a clip whose `source_range` is `None` by falling back to its `media_reference.available_range`. When the active timeline (or, in `source` mode, the selected clip) is **not yet loaded** — e.g. a playback event that names a timeline before its snapshot arrives — the tool MUST skip that interval rather than emit anything. When the active clip **is** loaded but has neither `source_range` nor `available_range`, the tool MUST fail with a clear, identifying error rather than emit a raw view frame.

#### Scenario: Clip with embedded timecode and no source_range
- **WHEN** the active clip has `source_range == None` and `media_reference.available_range` starting at frame 98499, and the playback view frame is 31
- **THEN** the emitted background clip's `source_range.start_time` is media frame 98530 (98499 + 31), which lies within the media's available range

#### Scenario: Clip with an explicit trimmed source_range
- **WHEN** the active clip has an explicit `source_range` and the playback view frame is N
- **THEN** the emitted `source_range.start_time` equals the clip's `source_range.start_time` plus N, expressed at the clip's rate

#### Scenario: Playback before its snapshot is skipped
- **WHEN** a playback event names a timeline (or `source`-mode clip) that has not yet been loaded from a snapshot or `ADD_TIMELINE`
- **THEN** the tool emits no segment for that interval and continues, resolving normally once the defining structure arrives

#### Scenario: Loaded clip with no range fails loudly
- **WHEN** a playback event references a clip that is loaded but has neither `source_range` nor `available_range`
- **THEN** the tool exits with a non-zero status and an error identifying the unresolved clip, and does not emit a clip addressed by a raw view frame

### Requirement: Sequence traversal across clip boundaries
While reconstructing a playing segment, the tool MUST follow the active sequence: when the advancing playhead crosses a clip boundary in the active track, the tool MUST close the current output clip and open a new one referencing the next underlying media clip, each addressed in its own media frame space. In `source` view mode the tool MUST resolve the view frame as a direct offset into the single selected clip's effective range without sequence traversal. The `source`-mode selected clip MAY be identified by a `clip_guid` carried on the playback event itself, not only by a separate `SelectionSet` event.

#### Scenario: Playback crosses a cut in sequence mode
- **WHEN** a single play segment in `sequence` view mode spans two clips A then B in the active track
- **THEN** the output contains one background clip referencing A's media over the portion before the cut and a second referencing B's media over the portion after, each with source ranges in its own media's frame space

#### Scenario: Source view resolves against the selected clip
- **WHEN** the session is in `source` view mode with a selected clip present in the parsed structures
- **THEN** the view frame is resolved as `effective_range.start + view_frame` of that clip, with no sequence `child_at_time` lookup

### Requirement: Loop-mode wrap
When an advancing playing playhead reaches the end of the active sequence and the last-seen `playback_mode` is `loop`, the tool MUST wrap the playhead to the sequence start (frame 0) and continue emitting segments. When the mode is not `loop`, the tool MUST hold the final frame until the next event.

#### Scenario: Playhead wraps at sequence end while looping
- **WHEN** a play segment in `loop` mode advances past the last frame of the active sequence
- **THEN** the output continues from the sequence's first frame, emitting the wrapped remainder as additional background clip(s) in the correct media frame space

#### Scenario: Non-loop playback holds the last frame
- **WHEN** a play segment reaches the sequence end with `playback_mode` not equal to `loop`
- **THEN** the output holds the final frame (freeze) until the next recorded event, without wrapping
