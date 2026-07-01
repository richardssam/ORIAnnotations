## Context

The existing `test_media/create_test_media.sh` pipeline produces real-footage QuickTimes via a multi-phase download/convert/encode chain using `ffmpeg-dailies`. That pipeline is heavyweight and not suited for synthetic, deterministic test frames.

OTIO integration tests need:
1. Image sequences where each frame is visually self-describing (you can verify frame identity by inspection)
2. A matching QuickTime per sequence with embedded timecode so RV can reconcile image-seq vs QT views of the same content
3. OTIO timelines that exercise `ImageSequenceReference` and `ExternalReference` with non-trivial (multi-clip, sub-second) cuts

## Goals / Non-Goals

**Goals:**
- Four synthetic 720p PNG sequences (A/B/C/D), frames 100–119, each frame showing its sequence label, absolute frame number, and relative (0-indexed) frame number
- One ProRes QuickTime per sequence at 24fps, embedded TC `00:00:04:04` (frame 100 = 4s 4f @ 24fps)
- Two OTIO files: `otio_test_quicktime.otio` and `otio_test_imageseq.otio`, both using relative paths and containing a 20-clip, 1-frame-per-clip alternating A→B→C→D timeline
- Single idempotent Python script with no new dependencies

**Non-Goals:**
- Color pipeline testing (frames are white/black, no OCIO)
- Slate frames (frame content is the only content)
- Integration with the existing `create_test_media.sh` workflow

## Decisions

### Single Python script, not shell

**Decision**: `create_otio_test_media.py` handles all three phases (render, encode, write OTIO).

**Rationale**: Pillow for PNG rendering and opentimelineio for OTIO generation are both Python-native. Mixing Python and shell would require inter-process communication for loop logic. A single Python script is simpler and easier to test.

**Alternative considered**: Extend `create_test_media.sh` with a new phase. Rejected because that script's `ffmpeg-dailies` + oiiotool chain is not applicable here and would muddle a clean synthetic pipeline.

---

### Plain ffmpeg for QuickTime encoding (no ffmpeg-dailies)

**Decision**: Use `subprocess.run(["ffmpeg", ...])` directly with `prores_ks` codec.

**Rationale**: The frame content itself is the burn-in — no additional slate or overlay is needed. `ffmpeg-dailies` adds a slate frame and overlay pipeline that would duplicate what's already baked into the PNG pixels. Plain ffmpeg keeps the dependency surface small.

**Timecode**: `00:00:04:04` embedded via `-timecode` flag. Frame 100 at 24fps = 4 seconds + 4 frames (non-drop-frame).

---

### ProRes 422 HQ codec

**Decision**: Use `-vcodec prores_ks -profile:v 3` for QuickTime output.

**Rationale**: Consistent with the existing test suite's codec choice (ffmpeg-dailies defaults to ProRes). ProRes is frame-accurate and widely supported by RV and xStudio without transcoding artifacts.

---

### OTIO files in `test_media/source/` with relative paths

**Decision**: Place both `.otio` files in `test_media/source/`. Use relative paths (e.g., `./encoded/seq_A.mov`, `./frames/seq_A`) for all media references.

**Rationale**: Relative paths make the OTIO files portable — they work regardless of where the repo is checked out, as long as the `source/` directory structure is intact. Absolute paths would break on any machine other than the one that generated them.

**ImageSequenceReference base URL**: `./frames/seq_A` (no trailing slash — OTIO's `abstract_target_url` adds the separator). Name prefix `seq_A.`, suffix `.png`, padding 4.

---

### Timeline structure

20 clips × 1 frame each. Clip `i` (0-indexed) uses sequence `sequences[i % 4]` and frame `100 + i`. Source range: `TimeRange(RationalTime(100 + i, 24), RationalTime(1, 24))`.

```
i=0  → A, frame 100   i=4  → A, frame 104  ...
i=1  → B, frame 101   i=5  → B, frame 105
i=2  → C, frame 102   i=6  → C, frame 106
i=3  → D, frame 103   i=7  → D, frame 107
```

Each sequence's `available_range`: `TimeRange(RationalTime(100, 24), RationalTime(20, 24))`.

---

### Frame layout (Pillow rendering)

- 1280×720 white background, black text
- Sequence label: centered, large font (~240pt via truetype or PIL default scaled)
- "Frame: NNN" left-aligned, lower third (~80pt)
- "Rel: N" right-aligned, lower third (~80pt)
- Font: use PIL's default bitmap font (no system font dependency) for portability; if ImageFont.truetype is unavailable, fall back gracefully

## Risks / Trade-offs

- **PIL default font is small** → Use `ImageFont.load_default(size=N)` (Pillow ≥ 10) or `ImageFont.truetype` with a system font as a fallback. Script should warn if large font is unavailable.
- **ffmpeg not on PATH** → Script checks at startup and exits with a clear error, matching the existing `create_test_media.sh` pattern.
- **Relative paths in OTIO** → Only valid when the OTIO file is opened with the `test_media/source/` directory structure intact. If opened from a different working directory, a host may not resolve them. This is acceptable for test media used in the suite where paths are known.

## Open Questions

- None — all decisions resolved in explore session.
