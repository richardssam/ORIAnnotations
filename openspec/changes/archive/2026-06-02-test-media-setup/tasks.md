## 1. Repo Structure and Gitignore

- [x] 1.1 Create `test_media/` directory with a `.gitignore` that excludes `source/` (frames and encoded QTs) but tracks `*.sh`, `*.yaml`, and `*.md`
- [x] 1.2 Verify `git status` shows no untracked files under `test_media/source/` after creating the directory

## 2. ffmpeg-dailies Config Files

- [x] 2.1 Create `test_media/config_ocio.yaml` — H264 (CRF 18, yuv420p10le), slate enabled with template and burnins, OCIO enabled (ACEScg → sRGB - Display, ACES 1.0 - SDR Video, `ocio://studio-config-v1.0.0_aces-v1.3_ocio-v2.1`)
- [x] 2.2 Create `test_media/config_srgb.yaml` — identical to config_ocio.yaml but with `ocio.enabled: false`
- [x] 2.3 Validate both configs with a dry-run against a sample input: `python -m ffmpeg_dailies --config test_media/config_ocio.yaml --input <sample> --output /tmp/test.mov --dry-run`

## 3. Create Main Script

- [x] 3.1 Create `test_media/create_test_media.sh` with a prerequisite check block that verifies `aws`, `oiiotool`, `ffmpeg`, and `python -m ffmpeg_dailies` are available; exits with named error on failure
- [x] 3.2 Add env-var configuration block: `FFMPEG_DAILIES_DIR` (default `../../ffmpeg-dailies`) and `FFMPEG_BIN` (defaults to `ffmpeg`); script uses `FFMPEG_BIN` env var which ffmpeg-dailies also reads
- [x] 3.3 Add `--keep-raw` flag parsing (set a variable, checked in cleanup phase)

## 4. Download Phase

- [x] 4.1 Add sparks EXR download block: frames 6100–6199 (`SPARKS_ACES_06100.exr`–`SPARKS_ACES_06199.exr`) from `s3://download.opencontent.netflix.com/sparks/aces_image_sequence_59_94_fps/` to `source/frames/sparks/`; skip if directory already exists
- [x] 4.2 Add chimera_wind download block: frames 66600–66699 (6-digit padding) from `s3://download.opencontent.netflix.com/Chimera/tif_DCI4k5994p/` to `source/tif_tmp/chimera_wind/`; skip if directory exists
- [x] 4.3 Add chimera_cars download block: frames 2500–2599 (5-digit padding) from `tif_DCI4k2398p/` to `source/tif_tmp/chimera_cars/`; skip if directory exists
- [x] 4.4 Add chimera_dancers download block: frames 21800–21899 (6-digit padding, 59.94fps source) from `tif_DCI4k5994p/` to `source/tif_tmp/chimera_dancers/`; skip if directory exists
- [x] 4.5 Add chimera_fountains download block: frames 5400–5499 (5-digit padding) from `tif_DCI4k2398p/` to `source/tif_tmp/chimera_fountains/`; skip if directory exists

## 5. Conversion Phase (chimera TIF → PNG)

- [x] 5.1 Add oiiotool conversion for chimera_wind: `--framepadding 6 --frames 66600-66699 --resize 1920x1080 --powc 2 -d uint16 -o source/frames/chimera_wind/chimera_wind.#.png`; skip if output already exists
- [x] 5.2 Add oiiotool conversion for chimera_cars: `--framepadding 5 --frames 2500-2599`; skip if output exists
- [x] 5.3 Add oiiotool conversion for chimera_dancers: `--framepadding 6 --frames 21800-21899`; skip if output exists
- [x] 5.4 Add oiiotool conversion for chimera_fountains: `--framepadding 5 --frames 5400-5499`; skip if output exists
- [x] 5.5 Add cleanup block: `rm -rf source/tif_tmp/` unless `--keep-raw` was passed

## 6. Encoding Phase (ffmpeg-dailies)

- [x] 6.1 Encode `sparks.mov`: `python -m ffmpeg_dailies --config config_ocio.yaml --input source/frames/sparks/SPARKS_ACES_%05d.exr --output source/encoded/sparks.mov --start-number 6100 --framerate 25 --timecode 00:04:04:00 --meta-shot sparks --meta-filename SPARKS_ACES`; skip if output exists
- [x] 6.2 Encode `chimera_wind.mov`: `config_srgb.yaml`, input `chimera_wind/chimera_wind.%06d.png`, `--start-number 66600 --framerate 25 --timecode 00:44:24:00`; skip if output exists
- [x] 6.3 Encode `chimera_cars.mov`: `config_srgb.yaml`, input `chimera_cars/chimera_cars.%05d.png`, `--start-number 2500 --framerate 25 --timecode 00:01:40:00`; skip if output exists
- [x] 6.4 Encode `chimera_dancers.mov`: `config_srgb.yaml`, input `chimera_dancers/chimera_dancers.%06d.png`, `--start-number 21800 --framerate 25 --timecode 00:14:32:00`; skip if output exists
- [x] 6.5 Encode `chimera_fountains.mov`: `config_srgb.yaml`, input `chimera_fountains/chimera_fountains.%05d.png`, `--start-number 5400 --framerate 25 --timecode 00:03:36:00`; skip if output exists

## 7. Validation and Smoke Test

- [x] 7.1 Add an optional `--validate` flag that runs ffprobe on each encoded file and checks frame count (expected: 101 including slate), frame rate, and embedded timecode
- [ ] 7.2 Run the full script locally; verify all five `.mov` files are produced and open correctly in RV or ffplay
- [ ] 7.3 Verify frame sequences in `source/frames/` have exactly 100 files per clip with correct naming/padding

## 8. Documentation

- [x] 8.1 Add a `test_media/README.md` documenting: prerequisites, how to run the script, env var overrides, expected output layout, and the timecode-to-frame-number mapping table
- [x] 8.2 Update `sync_test/sync_tests.yaml` to reference the canonical encoded media paths under `test_media/source/encoded/`
