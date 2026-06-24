## Why

Currently, there is no way to export a sync session recording (.jsonl format) into an OTIO timeline that captures the continuous, real-time experience of the session (showing exactly what was on screen, including play, pause, scrub, and freeze frames over the course of the session). Such a capability is needed to simulate review sessions and share screen-recording-like previews in standard OTIO review players.

## What Changes

- Add a new command line tool/script `sync_recorder/convert_recording_to_timeline.py` to parse session recordings and reconstruct a continuous dual-track OTIO timeline (Background Media track + Annotation Overlay track).
- Support tracking active timelines, active clips, play/pause transitions, and playhead positions from `PlaybackSettingsSet` and `SelectionSet` events.
- Reconstruct pause and scrub periods using 1-frame source ranges stretched with `LinearTimeWarp` freeze-frame effects.
- Implement a modular architecture designed to support transparent PNG drawings rendering in Phase 2.

## Capabilities

### New Capabilities
- `recording-to-timeline`: Reconstruct a continuous screen-recording-like OTIO timeline from a JSONL session recording (Phase 1: temporal mapping, play/pause, and freeze frames; Phase 2: annotation drawings rendering).

### Modified Capabilities
<!-- None -->

## Impact

- `sync_recorder/` package: Adds `convert_recording_to_timeline.py` script.
- Virtual environment dependencies: Pillow is used for future rendering (already in `requirements.txt`).
- `test/` or integration testing suite: Add validation for the conversion script.
