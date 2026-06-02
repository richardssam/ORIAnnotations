## Why

Testing annotation sync across RV, xStudio, and other tools requires representative media that all contributors can reproduce — real image sequences with known colorimetry plus ProRes/H264 QuickTimes encoded through a controlled pipeline. Currently there is no automated way to create this, making it hard to onboard contributors or reproduce test failures reliably.

## What Changes

- New `test_media/` directory in the repo with a shell script (`create_test_media.sh`) that downloads, converts, and encodes all test media from scratch
- Two ffmpeg-dailies YAML configs (OCIO-enabled for EXR sources, OCIO-disabled for display-ready PNG sources)
- Five clips, 100 frames each, from free Netflix open-content sources (no credentials needed)
- Source frame sequences preserved alongside encoded QuickTimes so that frame-offset edge cases can be tested
- `sync_tests.yaml` updated to reference the new canonical media paths

## Capabilities

### New Capabilities

- `test-media-pipeline`: Script and configs to reproducibly download, convert, and encode test media for the annotation sync test suite

### Modified Capabilities

- `otio-sync-core`: `sync_tests.yaml` path references will change to point at `test_media/` — no spec-level behavior change, paths only

## Impact

- New dependency on `aws` CLI (public S3, no credentials), `oiiotool`, `python -m ffmpeg_dailies`, and `ffmpeg` with OCIO support
- New directory `test_media/` added to repo; `source/frames/` and `source/encoded/` are gitignored (generated content)
- `sync_tests.yaml` media paths updated
- No changes to Python source, OTIO plugin, or RV/xStudio plugin code
