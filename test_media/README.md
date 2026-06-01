# ORIAnnotations Test Media

Scripts and configs that produce the standard test media for the ORIAnnotations sync test suite. All source material comes from Netflix open content on public S3 — no credentials required.

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
│   │   └── chimera_fountains/ # PNGs, frames 5400–5499   (sRGB, source 23.98fps)
│   └── encoded/
│       ├── sparks.mov
│       ├── chimera_wind.mov
│       ├── chimera_cars.mov
│       ├── chimera_dancers.mov
│       └── chimera_fountains.mov
```

All `source/` content is gitignored.

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
