## 1. Script scaffold and prerequisites

- [x] 1.1 Create `test_media/create_otio_test_media.py` with argument parsing (`--skip-frames`, `--skip-encode`, `--skip-otio`) and a startup check that verifies `ffmpeg` is on PATH, exiting with a clear error if not
- [x] 1.2 Add idempotency helpers: `frames_exist(seq)` and `mov_exists(seq)` checks that gate each phase

## 2. PNG frame rendering (Phase 1)

- [x] 2.1 Implement `render_frames(seq_label)` using Pillow: 1280×720 white background, black text; large centered sequence label; "Frame: NNN" and "Rel: N" in the lower area
- [x] 2.2 Use `ImageFont.load_default(size=N)` (Pillow ≥ 10) with a graceful fallback warning if large-font API is unavailable
- [x] 2.3 Write frames to `test_media/source/frames/seq_<X>/seq_<X>.NNNN.png` (4-digit zero-padding, frames 100–119)
- [x] 2.4 Loop over sequences A, B, C, D and call `render_frames`, skipping any sequence whose first frame already exists

## 3. QuickTime encoding (Phase 2)

- [x] 3.1 Implement `encode_quicktime(seq_label)` that runs ffmpeg via `subprocess.run` with `-vcodec prores_ks -profile:v 3 -r 24 -timecode 00:00:04:04`, reading from `frames/seq_<X>/seq_<X>.%04d.png` at `-start_number 100`
- [x] 3.2 Write output to `test_media/source/encoded/seq_<X>.mov`, raising on non-zero ffmpeg exit
- [x] 3.3 Skip encoding if the `.mov` file already exists

## 4. OTIO file generation (Phase 3)

- [x] 4.1 Implement `build_clip_qt(i)`: creates an `otio.schema.Clip` with `ExternalReference` to `./encoded/seq_<X>.mov`, `available_range = TimeRange(RationalTime(100, 24), RationalTime(20, 24))`, and `source_range = TimeRange(RationalTime(100 + i, 24), RationalTime(1, 24))`
- [x] 4.2 Implement `build_clip_imageseq(i)`: creates an `otio.schema.Clip` with `ImageSequenceReference` (`target_url_base=./frames/seq_<X>`, `name_prefix=seq_<X>.`, `name_suffix=.png`, `start_frame=100`, `frame_zero_padding=4`, `rate=24.0`) and same source/available ranges as above
- [x] 4.3 Build the 20-clip timeline for each variant by looping `i = 0..19`, using `sequences[i % 4]` and calling the appropriate clip builder
- [x] 4.4 Write `test_media/source/otio_test_quicktime.otio` and `test_media/source/otio_test_imageseq.otio` via `otio.adapters.write_to_file`

## 5. Validation

- [x] 5.1 Run the script end-to-end: verify 80 PNG files are created across 4 sequence directories
- [x] 5.2 Verify 4 `.mov` files exist in `encoded/` and spot-check timecode with `ffprobe`
- [x] 5.3 Load both `.otio` files with `opentimelineio` in a Python shell; assert 20 clips, correct sequence order, and correct `source_range` for clip index 3 (D@103) and clip index 4 (A@104)
- [x] 5.4 Update `test_media/README.md` to document the new script, its phases, and the output layout
