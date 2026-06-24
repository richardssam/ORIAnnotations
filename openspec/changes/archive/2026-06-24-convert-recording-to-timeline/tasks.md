## 1. Setup & Script Structure

- [x] 1.1 Create `sync_recorder/convert_recording_to_timeline.py` with `argparse` layout and main entrypoint.
- [x] 1.2 Add logic to load python paths and import `opentimelineio` and `otio_sync_core` correctly.

## 2. JSONL Parser & State Projection

- [x] 2.1 Implement chronological parser loop for `.jsonl` lines.
- [x] 2.2 Reconstruct active timeline templates and clip metadata from `STATE_SNAPSHOT` events.
- [x] 2.3 Implement state machine tracking play/pause/scrub transitions (`PlaybackSettingsSet`) and active clip selection (`SelectionSet`).

## 3. Visual Segment Extraction

- [x] 3.1 Extract segments of continuous visual state (start/end wall-clock time, active clip, start frame, playing status).
- [x] 3.2 Add validation checks for missing snapshots or unknown timeline/clip GUIDs.

## 4. OTIO Timeline Reassembly

- [x] 4.1 Construct output OTIO `Timeline` containing a `Stack` and the "Background Media" track.
- [x] 4.2 Reconstruct normal playback segments as OTIO Clips with correct source ranges.
- [x] 4.3 Reconstruct pause/scrub segments as 1-frame source clips retimed with `LinearTimeWarp` effects.
- [x] 4.4 Write the generated timeline to the specified output file.

## 5. Verification & Tests

- [x] 5.1 Create unit or integration tests under `tests/otio_sync/test_convert_recording.py`.
- [x] 5.2 Verify that the generated OTIO files can be successfully read and validated using `otio.adapters.read_from_file`.
