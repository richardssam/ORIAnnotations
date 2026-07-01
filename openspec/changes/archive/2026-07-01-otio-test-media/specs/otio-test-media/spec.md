## ADDED Requirements

### Requirement: Script renders 720p PNG image sequences for four synthetic clips
The `create_otio_test_media.py` script SHALL render four PNG image sequences (A, B, C, D) into `test_media/source/frames/seq_<X>/`. Each sequence SHALL contain exactly 20 frames numbered 100–119 (4-digit zero-padded: `seq_A.0100.png` through `seq_A.0119.png`). Each PNG SHALL be 1280×720, white background with black text showing: the sequence label in large text (centered), the absolute frame number, and the relative (0-based) frame index. The script SHALL be idempotent — skipping sequences whose frames already exist.

#### Scenario: PNG frames are rendered for all four sequences
- **WHEN** `create_otio_test_media.py` is run and `test_media/source/frames/` does not contain sequence directories
- **THEN** directories `seq_A`, `seq_B`, `seq_C`, `seq_D` are created under `test_media/source/frames/`, each containing exactly 20 PNG files named `seq_<X>.0100.png` through `seq_<X>.0119.png`

#### Scenario: Each PNG is the correct resolution
- **WHEN** any generated PNG frame is inspected
- **THEN** its dimensions are exactly 1280×720 pixels

#### Scenario: Frame content encodes correct identity
- **WHEN** `seq_B.0105.png` is inspected visually or by OCR
- **THEN** the frame displays sequence label "B", text "Frame: 105", and text "Rel: 5"

#### Scenario: Idempotent re-run skips existing frames
- **WHEN** `create_otio_test_media.py` is run a second time and PNG frames already exist
- **THEN** the rendering phase is skipped and existing frames are not overwritten

---

### Requirement: Script encodes one ProRes QuickTime per sequence with embedded timecode
The script SHALL encode four QuickTime files (`seq_A.mov` through `seq_D.mov`) into `test_media/source/encoded/` using ffmpeg with the `prores_ks` codec at 24fps. Each QuickTime SHALL have an embedded start timecode of `00:00:04:04` (corresponding to frame 100 at 24fps, non-drop-frame). The script SHALL check that `ffmpeg` is available on PATH at startup and exit with a clear error if not found. Encoding SHALL be skipped for any `.mov` file that already exists.

#### Scenario: QuickTime files are created for all sequences
- **WHEN** `create_otio_test_media.py` completes successfully
- **THEN** `test_media/source/encoded/seq_A.mov`, `seq_B.mov`, `seq_C.mov`, `seq_D.mov` all exist and are larger than 10 KB

#### Scenario: Embedded timecode is correct
- **WHEN** `seq_A.mov` is probed with ffprobe
- **THEN** the reported start timecode is `00:00:04:04`

#### Scenario: Frame rate is 24fps
- **WHEN** any encoded QuickTime is probed with ffprobe
- **THEN** the reported frame rate is 24fps (24/1)

#### Scenario: ffmpeg not on PATH
- **WHEN** `create_otio_test_media.py` is run and `ffmpeg` is not found on PATH
- **THEN** the script exits with a non-zero code and prints a clear error message before encoding begins

#### Scenario: Idempotent re-run skips existing QuickTimes
- **WHEN** `create_otio_test_media.py` is run and `seq_A.mov` already exists
- **THEN** ffmpeg is not invoked for that sequence

---

### Requirement: Script writes two OTIO timeline files with relative media paths
The script SHALL write `test_media/source/otio_test_quicktime.otio` and `test_media/source/otio_test_imageseq.otio`. Both files SHALL contain a single video track with 20 clips, each with a `source_range` duration of 1 frame at 24fps. Clips SHALL alternate A→B→C→D, with clip `i` (0-indexed) using sequence `[A,B,C,D][i % 4]` and `source_range.start_time = RationalTime(100 + i, 24)`. All media paths SHALL be relative to `test_media/source/` (e.g. `./encoded/seq_A.mov`).

#### Scenario: QuickTime OTIO has 20 clips with ExternalReference
- **WHEN** `otio_test_quicktime.otio` is loaded with opentimelineio
- **THEN** the single track contains exactly 20 clips, each with a `DEFAULT_MEDIA` reference that is an `ExternalReference` pointing to `./encoded/seq_<X>.mov`

#### Scenario: Image-sequence OTIO has 20 clips with ImageSequenceReference
- **WHEN** `otio_test_imageseq.otio` is loaded with opentimelineio
- **THEN** the single track contains exactly 20 clips, each with a `DEFAULT_MEDIA` reference that is an `ImageSequenceReference` with `target_url_base = ./frames/seq_<X>`, `name_prefix = seq_<X>.`, `name_suffix = .png`, `start_frame = 100`, `frame_zero_padding = 4`, and `rate = 24.0`

#### Scenario: Frame identity is correct for the 4th clip
- **WHEN** clip index 3 (the 4th clip) in either OTIO file is inspected
- **THEN** it references sequence D and has `source_range.start_time = RationalTime(103, 24)` with duration `RationalTime(1, 24)`

#### Scenario: Clip 5 is sequence A at frame 104
- **WHEN** clip index 4 (the 5th clip) in either OTIO file is inspected
- **THEN** it references sequence A and has `source_range.start_time = RationalTime(104, 24)` with duration `RationalTime(1, 24)`

#### Scenario: Available range covers all 20 frames
- **WHEN** the media reference of any clip in either OTIO file is inspected
- **THEN** `available_range.start_time = RationalTime(100, 24)` and `available_range.duration = RationalTime(20, 24)`
