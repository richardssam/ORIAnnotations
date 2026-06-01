#!/usr/bin/env bash
# create_test_media.sh — downloads and encodes ORIAnnotations test media.
# See README.md for prerequisites and usage.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Configuration — override any of these via environment variables
# ---------------------------------------------------------------------------
: "${FFMPEG_DAILIES_DIR:=}"
: "${FFMPEG_BIN:=ffmpeg}"
export FFMPEG_BIN

KEEP_RAW=0
VALIDATE=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: $0 [--keep-raw] [--validate]"
    echo "  --keep-raw   Do not delete downloaded raw EXRs after downrez"
    echo "  --validate   Run ffprobe checks on encoded files after encoding"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-raw) KEEP_RAW=1 ;;
        --validate) VALIDATE=1 ;;
        -h|--help)  usage ;;
        *) echo "ERROR: Unknown argument: $1" >&2; usage ;;
    esac
    shift
done

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
require_tool() {
    if ! command -v "$1" &>/dev/null; then
        echo "ERROR: Required tool '$1' not found on PATH." >&2
        exit 1
    fi
}

require_tool aws
require_tool oiiotool

if ! python -c "import requests" 2>/dev/null; then
    echo "ERROR: Python 'requests' package not found." >&2
    echo "       Install with: pip install requests" >&2
    exit 1
fi

# Resolve ffmpeg-dailies directory
if [[ -z "$FFMPEG_DAILIES_DIR" ]]; then
    _CANDIDATE="$(cd "$SCRIPT_DIR/../../ffmpeg-dailies" 2>/dev/null && pwd || true)"
    if [[ -d "$_CANDIDATE" ]]; then
        FFMPEG_DAILIES_DIR="$_CANDIDATE"
    else
        echo "ERROR: ffmpeg-dailies not found at '$_CANDIDATE'." >&2
        echo "       Set FFMPEG_DAILIES_DIR=/path/to/ffmpeg-dailies and retry." >&2
        exit 1
    fi
fi

if ! PYTHONPATH="$FFMPEG_DAILIES_DIR" python -c "import ffmpeg_dailies" 2>/dev/null; then
    echo "ERROR: Cannot import ffmpeg_dailies from FFMPEG_DAILIES_DIR=$FFMPEG_DAILIES_DIR" >&2
    exit 1
fi

_ffmpeg_filters=$("$FFMPEG_BIN" -filters 2>&1 || true)
if ! echo "$_ffmpeg_filters" | grep -q drawtext; then
    echo "ERROR: '$FFMPEG_BIN' is missing the drawtext filter (requires --enable-libfreetype)." >&2
    echo "       Set FFMPEG_BIN=/path/to/ocio-enabled/ffmpeg and retry." >&2
    exit 1
fi
unset _ffmpeg_filters

echo "=== Prerequisites OK ==="
echo "  ffmpeg-dailies : $FFMPEG_DAILIES_DIR"
echo "  ffmpeg         : $FFMPEG_BIN"
echo ""

DAILIES_CMD="python -m ffmpeg_dailies"

# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------
mkdir -p \
    source/frames/sparks \
    source/frames/laser \
    source/frames/car \
    source/frames/warp \
    source/frames/graphic \
    source/exr_tmp/sparks \
    source/exr_tmp/stem2 \
    source/encoded

# ---------------------------------------------------------------------------
# S3 base path for sparks (public, no credentials required)
# ---------------------------------------------------------------------------
S3_SPARKS="s3://download.opencontent.netflix.com/sparks/aces_image_sequence_59_94_fps/"

# ---------------------------------------------------------------------------
# Phase 1 — Download
# ---------------------------------------------------------------------------

echo "=== Phase 1: Download source media ==="

if [[ ! -f source/exr_tmp/sparks/SPARKS_ACES_06100.exr ]]; then
    echo "--- Downloading sparks EXRs (frames 6100–6199) ..."
    aws s3 cp --no-sign-request "$S3_SPARKS" source/exr_tmp/sparks/ \
        --recursive --exclude "*" --include "SPARKS_ACES_061*.exr"
else
    echo "--- sparks EXRs already present, skipping"
fi

# StEM2: all four clips live in the same master sequence, downloaded in one pass.
# Clip frame ranges (100 frames each at 24fps):
#   laser   91700–91799   car      96900–96999
#   graphic 89900–89999   warp     98500–98599
_STEM2_DIR="source/exr_tmp/stem2"
_STEM2_RANGES=()
[[ ! -f "$_STEM2_DIR/STEM2_4k_ctm_ACES_239.00091700.exr" ]] && _STEM2_RANGES+=(91700:100) || echo "--- laser EXRs already present, skipping"
[[ ! -f "$_STEM2_DIR/STEM2_4k_ctm_ACES_239.00096900.exr" ]] && _STEM2_RANGES+=(96900:100) || echo "--- car EXRs already present, skipping"
[[ ! -f "$_STEM2_DIR/STEM2_4k_ctm_ACES_239.00098500.exr" ]] && _STEM2_RANGES+=(98500:100) || echo "--- warp EXRs already present, skipping"
[[ ! -f "$_STEM2_DIR/STEM2_4k_ctm_ACES_239.00089900.exr" ]] && _STEM2_RANGES+=(89900:100) || echo "--- graphic EXRs already present, skipping"

if [[ ${#_STEM2_RANGES[@]} -gt 0 ]]; then
    echo "--- Downloading StEM2 EXRs: ${_STEM2_RANGES[*]} ..."
    python "$SCRIPT_DIR/stem_download.py" "$_STEM2_DIR" "${_STEM2_RANGES[@]}"
fi
unset _STEM2_DIR _STEM2_RANGES

echo ""

# ---------------------------------------------------------------------------
# Phase 2 — Downrez source EXRs to 1920x1080 half-float
# ---------------------------------------------------------------------------

echo "=== Phase 2: Downrez source EXRs to 1080p ==="

if [[ ! -f source/frames/sparks/sparks_ACES2065-1.06100.exr ]]; then
    echo "--- Resizing sparks EXRs (frames 6100–6199, 5-digit padding) ..."
    oiiotool -v --framepadding 5 --parallel-frames --frames 6100-6199 \
        -i "source/exr_tmp/sparks/SPARKS_ACES_@@@@@.exr" \
        --resize 1920x1080 -d half \
        -o "source/frames/sparks/sparks_ACES2065-1.#.exr"
else
    echo "--- sparks 1080p EXRs already present, skipping"
fi

# StEM2 clips share the same input pattern; each is extracted by frame range.
_stem2_downrez() {
    local clip="$1"
    local start="$2"
    local end="$3"
    local pad="$4"   # number of # chars / framepadding digits
    local first_out
    first_out="source/frames/${clip}/${clip}_ACES2065-1.$(printf "%0${pad}d" "$start").exr"

    if [[ -f "$first_out" ]]; then
        echo "--- ${clip} 1080p EXRs already present, skipping"
        return
    fi
    echo "--- Resizing ${clip} EXRs (frames ${start}–${end}, ${pad}-digit padding) ..."
    oiiotool -v --framepadding "$pad" --parallel-frames --frames "${start}-${end}" \
        -i "source/exr_tmp/stem2/STEM2_4k_ctm_ACES_239.########.exr" \
        --resize 1920x0 -d half \
        -o "source/frames/${clip}/${clip}_ACES2065-1.#.exr"
}

# All StEM2 clips use 8-digit padding to match the source filenames.
_stem2_downrez laser   91700 91799 8
_stem2_downrez car     96900 96999 8
_stem2_downrez warp    98500 98599 8
_stem2_downrez graphic 89900 89999 8

echo ""

# ---------------------------------------------------------------------------
# Phase 3 — Clean up raw EXRs (unless --keep-raw)
# ---------------------------------------------------------------------------

if [[ $KEEP_RAW -eq 0 ]]; then
    echo "=== Phase 3: Cleaning up raw source EXRs (pass --keep-raw to skip) ==="
    rm -rf source/exr_tmp/
else
    echo "=== Phase 3: Keeping raw source EXRs (--keep-raw set) ==="
fi
echo ""

# ---------------------------------------------------------------------------
# Phase 4 — Encode QuickTimes via ffmpeg-dailies
#
# Timecodes at 24fps matching source frame numbers:
#   sparks  (25fps)  6100  → 00:04:04:00
#   laser   (24fps) 91700  → 01:03:40:20
#   car     (24fps) 96900  → 01:07:17:12
#   warp    (24fps) 98500  → 01:08:24:04
#   graphic (24fps) 89900  → 01:02:25:20
# ---------------------------------------------------------------------------

echo "=== Phase 4: Encode QuickTimes ==="

_encode() {
    local output_stem="$1"   # basename without .mov, used for skip check and metadata
    local config="$2"
    local input="$3"
    local start_number="$4"
    local framerate="$5"
    local timecode="$6"
    local notes="$7"
    local shot="${8:-$output_stem}"
    local output="source/encoded/${output_stem}.mov"

    if [[ -f "$output" ]]; then
        echo "--- ${output_stem}.mov already exists, skipping"
        return
    fi

    echo "--- Encoding ${output_stem}.mov ..."
    PYTHONPATH="$FFMPEG_DAILIES_DIR" $DAILIES_CMD \
        --config "$config" \
        --input "$input" \
        --output "$output" \
        --start-number "$start_number" \
        --framerate "$framerate" \
        --timecode "$timecode" \
        --meta-shot "$shot" \
        --meta-filename "$output_stem" \
        --meta-notes "$notes"
    echo "--- ${output_stem}.mov done"
}

_encode sparks \
    config_ocio.yaml \
    "source/frames/sparks/sparks_ACES2065-1.%05d.exr" \
    6100 25 "00:04:04:00" \
    "Netflix Sparks | ACES2065-1 EXR | OCIO: ACES2065-1->sRGB | 25fps"

_encode laser_ACES_sRGB \
    config_ocio.yaml \
    "source/frames/laser/laser_ACES2065-1.%08d.exr" \
    91700 24 "01:03:40:20" \
    "ASWF StEM2 Laser | ACES2065-1 EXR | OCIO: ACES2065-1->sRGB | 24fps" \
    "laser"

_encode car_ACES_sRGB \
    config_ocio.yaml \
    "source/frames/car/car_ACES2065-1.%08d.exr" \
    96900 24 "01:07:17:12" \
    "ASWF StEM2 Car | ACES2065-1 EXR | OCIO: ACES2065-1->sRGB | 24fps" \
    "car"

_encode warp_ACES_sRGB \
    config_ocio.yaml \
    "source/frames/warp/warp_ACES2065-1.%08d.exr" \
    98500 24 "01:08:24:04" \
    "ASWF StEM2 Warp | ACES2065-1 EXR | OCIO: ACES2065-1->sRGB | 24fps" \
    "warp"

_encode graphic_ACES_sRGB \
    config_ocio.yaml \
    "source/frames/graphic/graphic_ACES2065-1.%08d.exr" \
    89900 24 "01:02:25:20" \
    "ASWF StEM2 Graphic | ACES2065-1 EXR | OCIO: ACES2065-1->sRGB | 24fps" \
    "graphic"

echo ""

# ---------------------------------------------------------------------------
# Phase 5 — Optional validation (--validate)
# ---------------------------------------------------------------------------

if [[ $VALIDATE -eq 1 ]]; then
    echo "=== Phase 5: Validating encoded files ==="
    FFPROBE_BIN="${FFMPEG_BIN%ffmpeg}ffprobe"
    if ! command -v "$FFPROBE_BIN" &>/dev/null; then
        FFPROBE_BIN=ffprobe
    fi

    ALL_OK=1
    _validate() {
        local clip="$1"
        local file="source/encoded/${clip}.mov"

        if [[ ! -f "$file" ]]; then
            echo "  FAIL: $file does not exist" >&2
            ALL_OK=0
            return
        fi

        local size
        size=$(wc -c < "$file")
        if [[ "$size" -lt 10000 ]]; then
            echo "  FAIL: $file is suspiciously small (${size} bytes)" >&2
            ALL_OK=0
            return
        fi

        local nb_frames
        nb_frames=$("$FFPROBE_BIN" -v error -select_streams v:0 \
            -count_packets -show_entries stream=nb_read_packets \
            -of csv=p=0 "$file" 2>/dev/null || echo "?")

        local tc
        tc=$("$FFPROBE_BIN" -v error -select_streams v:0 \
            -show_entries stream_tags=timecode \
            -of default=noprint_wrappers=1:nokey=1 "$file" 2>/dev/null || echo "unknown")

        echo "  OK  $clip : ${nb_frames} frames (incl. slate) | TC: $tc"
    }

    _validate sparks
    _validate laser_ACES_sRGB
    _validate car_ACES_sRGB
    _validate warp_ACES_sRGB
    _validate graphic_ACES_sRGB

    if [[ $ALL_OK -eq 0 ]]; then
        echo "Validation failed — see errors above." >&2
        exit 1
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo "=== Complete ==="
echo "  Frame sequences : test_media/source/frames/"
echo "  Encoded QTs     : test_media/source/encoded/"
