# Vector annotations vs. baked images

How the vector stroke data in an exported OTIO compares with the PNG frames
exported beside it: are they faithful to each other, and what does each cost to
store?

Measured 2026-10-31 on `examples/exporttest` and `examples/exporttest2`. These
are exports from the legacy `ori_annotations` RV plugin, made in Aug 2025.

## TL;DR

- **The positions agree.** The vector strokes land within 2 px of the painted
  pixels, so the coordinate mapping and frame numbering are correct.
- **The vector widths are about 0.6× the rendered width** in these files. The
  likely cause is that the exports predate the `RV_WIDTH_SCALE` read-back fix,
  so the files hold raw RV widths.
- **One stroke is in the vectors but not in its PNG** (cars frame 158).
- **The vectors as stored are about 20× smaller than the exported PNGs**, but
  only because those PNGs include the whole picture. Against an annotation-only
  PNG, the indented OTIO is about 1.5× *larger*. Compacted and gzipped it is
  about 9× smaller.

## The data

Each export folder holds:

- `annotationreview.otio`, with a `Media` track (the source movies) and a
  `Review` track holding one clip per annotated frame. Each review clip carries
  the frame's commands in `metadata.annotation_commands`: `PaintStart` /
  `PaintPoint` (with `PaintVertices` x/y/size arrays) and `TextAnnotation`.
- `<media>.<frame:05d>.png`: a 1920×1080 RV render of that frame with the
  annotations baked in.
- `<media>.mov`: the source movies.

The review clip's frame number is 1-based within the media, so review frame `N`
is frame `N-1` of the `.mov` (counting from 0).

Points are in RV's normalized space: the origin is the image centre, y points
up, and the unit is the image height. For a W×H image:

```
px = W/2 + x*H
py = H/2 - y*H
stroke width (px) = size*H
```

## Method 1: do the vectors match the image?

[`debug/compare_annotation_vectors.py`](../debug/compare_annotation_vectors.py)
does the following for each annotated frame:

1. Extract the clean frame from the `.mov` (`rez-env ffmpeg -- ffmpeg …`).
2. **Painted mask:** pixels where the PNG and the clean frame differ by more
   than 40/255 in any channel, followed by a 3×3 morphological open to remove
   speckle.
3. **Vector mask:** each `PaintPoint` stroke drawn as round-capped segments of
   width `size*H`.
4. Text is masked out of both sides; it is not compared.
5. Compute precision, recall and IoU. Then search over width scale (1.0–2.2×)
   and a ±2 px offset to expose any systematic size or position bias.
6. Write a side-by-side PNG per frame: the original on the left; on the right,
   green where both masks agree, red where only the image has paint, blue where
   only the vectors do.

```
python debug/compare_annotation_vectors.py examples/exporttest <out_dir>
```

Run it with the repo's `python` (it needs `cv2` and `numpy`).

### Results (`exporttest`)

**Precision** is the share of vector pixels that land on painted pixels.
**Recall** is the share of painted pixels covered by the vectors. **IoU** is the
overlap divided by the union of the two masks; 1.0 is a perfect match.

| Frame | Strokes | Points | Precision | Recall | IoU as exported | Best IoU | Best width × | Best offset (px) |
|---|---|---|---|---|---|---|---|---|
| cars 1 | 1 | 841 | 1.00 | 0.56 | 0.56 | 0.81 | 1.6 | −1, −1 |
| cars 65 | 3 | 747 | 0.97 | 0.68 | 0.67 | 0.91 | 1.4 | −1, −1 |
| cars 158 | 4 | 272 | **0.80** | 0.76 | 0.64 | 0.67 | 1.4 | 0, −2 |
| coaster 1 | 2 | 404 | 0.99 | 0.60 | 0.60 | 0.93 | 1.6 | −1, 0 |
| coaster 15 | 3 | 689 | 0.99 | 0.70 | 0.69 | 0.92 | 1.6 | −1, 0 |
| wind 38 | 4 | 389 | 1.00 | 0.55 | 0.55 | 0.95 | 1.85 | −1, −1 |

`exporttest2` has the same vectors and gives identical results, except for
cars frame 1. That frame's PNG differs, probably because the text renders
differently.

### Findings

1. **The positions are right.** Precision is about 1.0 and the best offset is
   2 px or less on every frame.
2. **The widths are short by about 1.4–1.85×.** The midpoint is close to
   `1 / RV_WIDTH_SCALE` (1 / 0.6 = 1.67). The protocol convention is
   `size = rv_width / 0.6`, but read-back from RV was missing that division
   until the pen-width fix
   ([`rv_annotation_codec.py`](../python/otio_sync_core/rv_annotation_codec.py),
   search for "Invert the forward `_pen_spec`"). These Aug 2025 files therefore
   hold raw RV widths. The scale isn't perfectly constant: the widest stroke
   (size 0.022) wants 1.85×, while the thin ones (size 0.01) want 1.4–1.6×. The
   gauss brush's soft edge, antialiasing and the 40/255 threshold all blur this.
   **Not yet confirmed:** re-exporting from current RV and rerunning should give
   a best width scale near 1.0.
3. **One stroke on cars frame 158 is in the vectors but not in the PNG.** It is
   the first stroke (white gauss, 42 points, starting near pixel 850, 621 over
   the van). It may have been drawn after the frame image was rendered.
4. **Colours outside 0–1.** One stroke on cars 158 is stored as
   `rgba [2, 2, 2, 1]`. It renders as plain white because the PNG clips it.

## Method 2: storage cost

For each annotated frame, compare:

- **Full-frame PNG:** the exported file (picture plus annotation).
- **Overlay-only PNG:** an RGBA PNG holding only the painted-mask pixels, with
  everything else transparent; compression level 9. This is a fair
  image-vs-vector comparison.
- **OTIO, indented:** that frame's `annotation_commands` as
  `json.dumps(indent=4)`. This is close to how the file stores them.
- **Compact JSON:** `separators=(",", ":")`.
- **Compact + gzip.**
- **float32 binary:** points × 3 values (x, y, size) × 4 bytes, raw, as a
  reference point.

### Results (`exporttest`, bytes)

| Frame | Points | Full-frame PNG | Overlay-only PNG | OTIO indented | Compact JSON | Compact + gzip | float32 binary |
|---|---|---|---|---|---|---|---|
| cars 1 | 841 | 1,068,317 | 49,370 | 95,153 | 51,640 | 5,207 | 10,092 |
| cars 65 | 747 | 1,241,133 | 49,005 | 85,860 | 46,577 | 6,507 | 8,964 |
| cars 158 | 272 | 1,197,806 | 23,699 | 34,328 | 18,875 | 3,338 | 3,264 |
| coaster 1 | 404 | 1,258,926 | 23,148 | 46,351 | 24,956 | 3,965 | 4,848 |
| coaster 15 | 689 | 1,663,160 | 19,500 | 79,313 | 42,988 | 4,024 | 8,268 |
| wind 38 | 389 | 1,672,232 | 96,318 | 46,862 | 25,442 | 5,306 | 4,668 |
| **Total** | **3,342** | **8,101,574** | **261,040** | **387,867** | **210,478** | **28,347** | **40,104** |

### Findings

1. **Against the exported PNGs, the vectors are about 20× smaller** (8.1 MB vs
   388 KB). Almost all of the PNG's bytes are the underlying picture, not the
   annotation.
2. **Against an overlay-only PNG, the indented OTIO is about 1.5× larger.**
   Which one wins depends on the drawing. Large strokes on a mostly empty
   canvas favour the vectors (wind 38: 47 KB vs 96 KB). Dense scribbles in a
   small area favour the image (coaster 15: 79 KB vs 20 KB).
3. **Formatting whitespace is the biggest overhead.** OTIO's indented JSON puts
   every number on its own line. Compacting halves the size.
4. **Gzip takes off another ~7×** and beats raw float32 binary (28 KB vs
   40 KB). Every point repeats an identical `size`, many points are exact
   duplicates (cars 1 starts with a run of the same point), and the
   coordinates share long prefixes.
5. **The file is mostly indentation.** The whole `annotationreview.otio` is
   718 KB, while the commands alone (at indent 4) total 388 KB. The difference
   comes from nesting: the commands sit about 8 levels deep, so each one-number
   line carries about 40 leading spaces. Leading spaces make up 484 KB of the
   718 KB file (67%).
6. Beyond size, the vectors are **lossless, can be re-rendered at any
   resolution, and stay editable**, which a baked image is not.

### Caveats

- The overlay-only PNG sizes depend on how cleanly the paint separates from
  the background. Antialiased edges below the threshold are dropped. On cars
  158, the stroke that is missing from the image (finding 3 of Method 1) isn't
  counted at all.
- The text annotation on cars frame 1 is included in the byte counts but was
  not part of the geometric comparison.
- This is one small sample: 6 frames, 17 strokes and 3,342 points, all drawn
  with constant pen size.

## Possible follow-ups

- Re-export the same session from current RV and rerun Method 1 to confirm the
  width scale is now about 1.0.
- If file size matters, write OTIO without indentation, or gzip it (`.otioz`
  or transport-level compression), before considering a binary point encoding.
- Drop consecutive duplicate points, and store `size` once per stroke when it
  is constant.
