## Context

The sync test suite in `sync_tests.yaml` currently references media files at hardcoded local paths, making contributor onboarding and CI reproduction difficult. All source material comes from Netflix open content on public S3 (no authentication required). The ffmpeg-dailies framework (`/Users/sam/git/ffmpeg-dailies`) is already used in the project for encoding and provides OCIO, slate, and burn-in support via YAML config.

Five clips are needed: **sparks** (EXR, ACEScg, 59.94fps), **chimera_wind** (TIF→PNG, 59.94fps), **chimera_cars** (TIF→PNG, 23.98fps), **chimera_dancers** (TIF→PNG, 59.94fps), **chimera_fountains** (TIF→PNG, 23.98fps). 100 frames per clip. Both raw frame sequences and encoded QuickTimes are required — frame sequences for testing RV frame-offset behavior, QTs for normal playback/sync testing.

## Goals / Non-Goals

**Goals:**
- Single script (`create_test_media.sh`) that reproduces all test media from scratch
- Five clips at 100 frames each, covering mixed frame rates (23.98 and 59.94)
- Source frame sequences preserved in `test_media/source/frames/` (gitignored)
- Encoded H264 QuickTimes in `test_media/source/encoded/` (gitignored)
- Embedded timecode in each QT that matches source frame numbers (enables frame-identity testing)
- Slate + burn-ins on all encoded files (production-representative)
- OCIO color management: ACEScg→sRGB for EXR sources; passthrough for display-ready PNG sources

**Non-Goals:**
- Distributing the media files in the repo (gitignored, generated)
- Supporting codecs other than H264 (ProRes upgrade deferred)
- HDR output variants (PQ, HLG) at this stage
- Automating OCIO config creation (user-provided)

## Decisions

### Two ffmpeg-dailies configs rather than one

**Decision**: Use `config_ocio.yaml` (OCIO enabled) for sparks EXR and `config_srgb.yaml` (OCIO disabled) for chimera PNG sources.

**Rationale**: Chimera TIFs go through `oiiotool --powc 2` during conversion, which bakes an approximate display gamma — treating them as linear in OCIO would double-apply a transform. Sparks EXRs are scene-linear ACEScg and require a proper OCIO transform to sRGB for display. Two configs with shared slate/burnin layout is cleaner than a per-clip OCIO override flag.

**Alternative considered**: Single config with OCIO off, pre-converting sparks EXR to sRGB PNG via oiiotool. Rejected because it loses the EXR source files that are needed for the frame-offset tests (EXRs must remain as the canonical frame sequence for sparks).

### H264 for encoded output

**Decision**: Use `h264_hq` ffmpeg-dailies codec profile (libx264, CRF 18, yuv420p10le) rather than ProRes.

**Rationale**: H264 at CRF 18 is visually lossless at this frame count, universally playable, and dramatically smaller for distribution. Both RV and xStudio handle it without issue. If a ProRes path is needed later, it's a single config key change.

### Timecode embedding matches source frame numbers

**Decision**: Each encoded QT gets an embedded start timecode derived from the source frame number at native frame rate.

**Rationale**: This is the only way to make frame identity unambiguous when the same content is loaded as both a frame sequence (e.g., `SPARKS_ACES_06100.exr`) and a QT in RV simultaneously. Without this, annotation frame sync testing across source types has no ground truth.

| Clip | Frame start | FPS | Embedded TC |
|------|-------------|-----|-------------|
| sparks | 6100 | 59.94 | 00:01:41;14 |
| chimera_wind | 66600 | 59.94 | 00:18:31;06 |
| chimera_cars | 2500 | 23.98 | 00:01:44:05 |
| chimera_dancers | 21800 | 59.94 | 00:06:03;02 |
| chimera_fountains | 5400 | 23.98 | 00:03:45:00 |

### oiiotool for TIF→PNG conversion (chimera)

**Decision**: Use oiiotool (already a project dependency) rather than ffmpeg for the TIF→PNG step.

**Rationale**: The EncodingGuidelines scripts already use oiiotool for this exact task with `--resize 1920x1080 --powc 2`, and the result is a known quantity. Using ffmpeg here would require careful colorspace flag matching to get the same pixel values.

### Raw TIF downloads are temporary

**Decision**: Chimera TIF files are downloaded to `source/tif_tmp/` and can be deleted after PNG conversion. They are not kept as "source frames."

**Rationale**: TIFs are 4K DCI P3 PQ — useful for re-encoding at full quality but too large to keep for annotation testing. The 1080p sRGB PNGs serve the frame-offset test use case. A `--keep-raw` flag allows skipping cleanup if needed.

## Risks / Trade-offs

- **oiiotool path** — oiiotool must be on `PATH`. Script should check for it and fail early with a clear message. → Early dependency check block at top of script.
- **chimera_dancers frame numbering** — The enc_sources script has inconsistent padding (5-digit) for dancers vs. other 59.94 clips (6-digit). The actual S3 TIF filenames use 6-digit padding. → Script uses 6-digit padding for all chimera clips and validates with a probe download before the full fetch.
- **OCIO built-in config availability** — `ocio://studio-config-v1.0.0_aces-v1.3_ocio-v2.1` requires OCIO built with bundled configs. If unavailable, ffmpeg-dailies OCIO step will fail. → Script documents this requirement; user will supply explicit `.ocio` file path as next step.
- **Download time** — 5 × 100 frames of 4K DCI TIF + EXR is significant. Script uses `--no-sign-request` and S3's parallel transfer; oiiotool uses `--parallel-frames`. Still potentially 20–40 min on first run.
- **ffmpeg-dailies as a sibling repo** — Script assumes `ffmpeg-dailies` is at `../ffmpeg-dailies` relative to ORIAnnotations. Document this assumption; make the path configurable via env var `FFMPEG_DAILIES_DIR`.

## Open Questions

- Should the OCIO config be bundled in `test_media/` as a file (self-contained) or keep the `ocio://` URI for now? (User will create the explicit file — path TBD)
- Should `sync_tests.yaml` be updated as part of this change, or deferred until the media actually exists?
