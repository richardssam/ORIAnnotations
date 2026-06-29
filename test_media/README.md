---
layout: default
title: Test Media for sync testing.
parent: ORI Sync Tools
---

# ORIAnnotations Test Media

Two scripts produce the test media used by the ORIAnnotations sync test suite:

| Script | Media | Source |
|--------|-------|--------|
| `create_test_media.sh` | Real-footage QuickTimes (sparks, StEM2 clips) | Netflix open content on public S3 |
| `create_otio_test_media.py` | Synthetic OTIO test sequences A/B/C/D | Generated locally — no download |

## Prerequisites

| Tool | Purpose | Notes |
|------|---------|-------|
| `aws` CLI | Download source material from S3 | `brew install awscli` |
| `oiiotool` | Convert Chimera TIFs to 1080p sRGB PNGs | Part of OpenImageIO |
| `ffmpeg` (OCIO build) | Encode QuickTimes | Must have `drawtext` and `ocio` filters |
| `python` + `ffmpeg_dailies` | Slate + burn-in encoding | See [ffmpeg-dailies](https://github.com/sam/ffmpeg-dailies) |

The script expects the `ffmpeg-dailies` repo to be a sibling of `ORIAnnotations`:
```
git/
├── ORIAnnotations/
└── ffmpeg-dailies/
```

## Usage

```bash
cd test_media/
./create_test_media.sh
```

The script is idempotent — re-running it skips any step whose output already exists.

### Options

```
--keep-raw    Do not delete downloaded TIF files after PNG conversion
--validate    Run ffprobe checks on encoded files after encoding
```

### Environment variable overrides

| Variable | Default | Description |
|----------|---------|-------------|
| `FFMPEG_DAILIES_DIR` | `../../ffmpeg-dailies` | Path to the ffmpeg-dailies repo |
| `FFMPEG_BIN` | `ffmpeg` | Path to the OCIO-enabled ffmpeg binary |

Example:
```bash
FFMPEG_BIN=/opt/tools/ffmpeg-ocio/bin/ffmpeg \
FFMPEG_DAILIES_DIR=/opt/tools/ffmpeg-dailies \
./create_test_media.sh --validate
```

## Output layout

```
test_media/
├── source/
│   ├── frames/
│   │   ├── sparks/            # EXRs, frames 6100–6199 (ACEScg, source 59.94fps)
│   │   ├── chimera_wind/      # PNGs, frames 66600–66699 (sRGB, source 59.94fps)
│   │   ├── chimera_cars/      # PNGs, frames 2500–2599   (sRGB, source 23.98fps)
│   │   ├── chimera_dancers/   # PNGs, frames 21800–21899 (sRGB, source 59.94fps)
│   │   ├── chimera_fountains/ # PNGs, frames 5400–5499   (sRGB, source 23.98fps)
│   │   ├── seq_A/             # PNGs, frames 100–119 (synthetic, 24fps)
│   │   ├── seq_B/             # PNGs, frames 100–119 (synthetic, 24fps)
│   │   ├── seq_C/             # PNGs, frames 100–119 (synthetic, 24fps)
│   │   └── seq_D/             # PNGs, frames 100–119 (synthetic, 24fps)
│   ├── encoded/
│   │   ├── sparks.mov
│   │   ├── chimera_wind.mov
│   │   ├── chimera_cars.mov
│   │   ├── chimera_dancers.mov
│   │   ├── chimera_fountains.mov
│   │   ├── seq_A.mov          # ProRes, 24fps, TC 00:00:04:04
│   │   ├── seq_B.mov
│   │   ├── seq_C.mov
│   │   └── seq_D.mov
│   ├── otio_test_quicktime.otio   # 20-clip timeline referencing seq_*.mov
│   └── otio_test_imageseq.otio   # 20-clip timeline referencing PNG sequences
```

All `source/` content is gitignored.

---

## OTIO synthetic test media (`create_otio_test_media.py`)

Generates four lightweight synthetic image sequences (A/B/C/D) and two OTIO timeline files.
No downloads required — everything is rendered locally from Pillow.

Requires `Pillow >= 10`, `opentimelineio` (both in the project `.venv`), and `ffmpeg` on PATH.
Set `FFMPEG_BIN=/path/to/ffmpeg` to use a non-default binary.

### Running the OTIO script

```bash
cd test_media/
python create_otio_test_media.py
```

The script is idempotent — re-running skips any step whose output already exists.

Flags: `--skip-frames` / `--skip-encode` / `--skip-otio` skip individual phases.

Example with a custom ffmpeg:

```bash
FFMPEG_BIN=/path/to/ffmpeg python create_otio_test_media.py
```

### Frame content

Each 1280×720 PNG frame has a white background with black text:

- **Large centered label** — sequence name (A, B, C, or D)
- **Frame: NNN** — absolute frame number (100–119)
- **Rel: N** — relative frame index, 0-based (0–19)

### OTIO timeline structure

Both OTIO files contain a single video track with **20 clips**, each 1 frame at 24fps.
Clips alternate A→B→C→D. Clip `i` (0-indexed) references sequence `[A,B,C,D][i % 4]`
at `source_range.start_time = RationalTime(100 + i, 24)`.

Example: the 4th clip (index 3) is sequence D at frame 103; the 5th clip (index 4) is
sequence A at frame 104.

Media paths are **relative** to `test_media/source/`, so the files are portable as long
as the `source/` directory structure is intact.

---

## Timecode reference

Each encoded QuickTime has an embedded start timecode at 25fps that corresponds to the source frame number. This lets RV (and other tools) reconcile frame identity when the same content is loaded as both a QT and an image sequence.

| Clip | Source frames | Source FPS | Encoded at | Embedded TC (25fps) |
|------|--------------|------------|------------|---------------------|
| sparks | 6100–6199 | 59.94 | 25fps | `00:04:04:00` |
| chimera_wind | 66600–66699 | 59.94 | 25fps | `00:44:24:00` |
| chimera_cars | 2500–2599 | 23.98 | 25fps | `00:01:40:00` |
| chimera_dancers | 21800–21899 | 59.94 | 25fps | `00:14:32:00` |
| chimera_fountains | 5400–5499 | 23.98 | 25fps | `00:03:36:00` |

Note: the actual TC embedded in the QuickTime is shifted back by one frame (the slate frame), so the first *content* frame lands on the TC shown above.

## Color pipeline

- **sparks**: ACEScg EXR → OCIO (`ACEScg → sRGB - Display`, ACES 1.0 - SDR Video) → H264 QT
- **chimera clips**: 4K DCI P3 PQ TIF → `oiiotool --powc 2` (approximate display gamma) → 1080p sRGB PNG → H264 QT (no OCIO step; gamma already baked)

## Source credits

All media from [Netflix Open Content](https://opencontent.netflix.com/), freely available via public S3 under their respective licenses.
