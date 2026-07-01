## Why

The existing test media relies on real-world footage downloaded from S3, which is heavyweight and color-pipeline-oriented. OTIO integration tests need lightweight, synthetic media with predictable, verifiable frame identity — specifically image sequences and matching QuickTimes, with multi-clip alternating timelines that prove frame-accurate OTIO source mapping.

## What Changes

- Add `test_media/create_otio_test_media.py` — a self-contained Python script that generates four synthetic image sequences (A/B/C/D), encodes them as ProRes QuickTimes with embedded timecode, and writes two OTIO timeline files (one referencing QTs, one referencing image sequences)
- The two OTIO files live in `test_media/source/` and use relative paths to the media beneath it

## Capabilities

### New Capabilities
- `otio-test-media`: Synthetic 720p PNG image sequences A/B/C/D (frames 100–119) with large sequence label, absolute frame number, and relative (0-based) frame count burned into each frame; matching ProRes QuickTimes at 24fps with embedded timecode; two OTIO timelines (QuickTime and image-sequence variants) with 1-frame alternating cuts across all four sequences

### Modified Capabilities
<!-- none -->

## Impact

- New file: `test_media/create_otio_test_media.py`
- New outputs (gitignored, under `test_media/source/`): 80 PNG frames, 4 `.mov` files, 2 `.otio` files
- Dependencies: `Pillow` (already in `.venv`), `ffmpeg` binary (already required), `opentimelineio` (already in `.venv`)
- No changes to existing `create_test_media.sh` or any existing specs
