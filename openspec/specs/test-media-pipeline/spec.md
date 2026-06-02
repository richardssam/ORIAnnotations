# test-media-pipeline Specification

## Purpose
TBD - created by archiving change test-media-setup. Update Purpose after archive.
## Requirements
### Requirement: Script downloads source media from public S3
The `create_test_media.sh` script SHALL download all source material from Netflix open-content public S3 buckets without requiring AWS credentials. It SHALL check for required tools (`aws`, `oiiotool`, `ffmpeg`, `python`) at startup and exit with a clear error message if any are missing.

#### Scenario: Prerequisite tools missing
- **WHEN** the script is run and `oiiotool` is not found on PATH
- **THEN** the script exits with a non-zero code and prints which tool is missing before downloading anything

#### Scenario: Successful download of sparks EXR frames
- **WHEN** `create_test_media.sh` is run and `source/frames/sparks/` does not exist
- **THEN** exactly 100 EXR frames (6100–6199) are downloaded to `test_media/source/frames/sparks/`

#### Scenario: Successful download of chimera clips
- **WHEN** `create_test_media.sh` is run and chimera source frames do not exist
- **THEN** 100 frames each for chimera_wind, chimera_cars, chimera_dancers, chimera_fountains are downloaded as TIF to `test_media/source/tif_tmp/<clip>/`

#### Scenario: Idempotent re-run
- **WHEN** `create_test_media.sh` is run a second time and source frames already exist
- **THEN** the download phase is skipped (no S3 requests made for existing clips)

---

### Requirement: Script converts chimera TIFs to display-ready PNG sequences
The script SHALL convert downloaded chimera TIF sequences to 1080p sRGB PNG using oiiotool with `--resize 1920x1080 --powc 2`, placing results in `test_media/source/frames/<clip>/`.

#### Scenario: Conversion produces correct frame count
- **WHEN** chimera TIF files are downloaded
- **THEN** exactly 100 PNG files are produced per clip in `test_media/source/frames/<clip>/`

#### Scenario: Converted PNGs are 1920x1080
- **WHEN** oiiotool conversion completes for any chimera clip
- **THEN** each output PNG has dimensions 1920×1080

#### Scenario: Raw TIFs cleaned up by default
- **WHEN** PNG conversion completes and the `--keep-raw` flag was not passed
- **THEN** `test_media/source/tif_tmp/` is removed

---

### Requirement: Script encodes H264 QuickTimes with slate, burn-ins, and embedded timecode
The script SHALL encode one H264 QuickTime per clip into `test_media/source/encoded/` using `python -m ffmpeg_dailies`. Each QT SHALL include a slate frame, burn-in overlays, and an embedded start timecode that corresponds to the source frame number at the clip's native frame rate.

#### Scenario: Encoded files exist after script completes
- **WHEN** `create_test_media.sh` completes successfully
- **THEN** five `.mov` files exist in `test_media/source/encoded/`: `sparks.mov`, `chimera_wind.mov`, `chimera_cars.mov`, `chimera_dancers.mov`, `chimera_fountains.mov`

#### Scenario: Correct frame rate per clip
- **WHEN** a chimera_cars or chimera_fountains QuickTime is probed with ffprobe
- **THEN** the reported frame rate is 23.98fps (24000/1001)

#### Scenario: Correct frame rate for 59.94fps clips
- **WHEN** sparks, chimera_wind, or chimera_dancers QuickTime is probed with ffprobe
- **THEN** the reported frame rate is 59.94fps (60000/1001)

#### Scenario: Timecode matches source frame numbers
- **WHEN** `sparks.mov` is probed with ffprobe
- **THEN** the embedded start timecode is `00:01:41;14` (frame 6100 at 59.94fps drop-frame)

#### Scenario: OCIO applied for sparks EXR
- **WHEN** `sparks.mov` is encoded via `config_ocio.yaml`
- **THEN** the ffmpeg-dailies OCIO filter is applied (ACEScg → sRGB - Display, ACES 1.0 - SDR Video)

#### Scenario: OCIO not applied for chimera clips
- **WHEN** any chimera clip is encoded via `config_srgb.yaml`
- **THEN** no OCIO filter is applied (display gamma already baked by oiiotool)

---

### Requirement: ffmpeg-dailies configs are stored in test_media/
The `test_media/` directory SHALL contain two ffmpeg-dailies YAML config files: `config_ocio.yaml` (OCIO enabled, for sparks EXR input) and `config_srgb.yaml` (OCIO disabled, for chimera PNG input). Both SHALL share identical slate, burn-in, and codec settings, differing only in the `ocio` section.

#### Scenario: Both configs produce slated output
- **WHEN** either config is used with ffmpeg-dailies
- **THEN** the encoded QuickTime has a slate frame prepended with clip metadata

#### Scenario: Codec is H264
- **WHEN** either config is used
- **THEN** the output codec is `libx264` with `yuv420p10le` pixel format and CRF 18

---

### Requirement: Generated files are gitignored
The `test_media/source/` directory (frames and encoded QTs) SHALL be excluded from git tracking. Only the script and config files SHALL be committed.

#### Scenario: git status clean after media generation
- **WHEN** `create_test_media.sh` completes
- **THEN** `git status` shows no untracked files under `test_media/source/`

---

### Requirement: Script is configurable via environment variables
The script SHALL respect the following environment variables to support non-standard setups:
- `FFMPEG_DAILIES_DIR`: path to the ffmpeg-dailies repo (default: `../../ffmpeg-dailies` relative to script)
- `OCIO_CONFIG`: path to the OCIO config file (default: `ocio://studio-config-v1.0.0_aces-v1.3_ocio-v2.1`)

#### Scenario: Custom ffmpeg-dailies path
- **WHEN** `FFMPEG_DAILIES_DIR=/opt/tools/ffmpeg-dailies create_test_media.sh` is run
- **THEN** the script uses that path for `python -m ffmpeg_dailies` invocations without error

