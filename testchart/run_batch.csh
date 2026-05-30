#!/bin/bash

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "========================================="
echo "Generating Test Charts"
echo "========================================="
python generate_testchart.py

echo ""
echo "========================================="
echo "Running xStudio Batch Import & Export"
echo "========================================="
python batch_xstudio.py testchart_annotations.otio xstudio_output

echo ""
echo "========================================="
echo "Running OpenRV Batch Import & Export"
echo "========================================="
python batch_openrv.py testchart_annotations.otio rv_output

echo ""
echo "========================================="
echo "Running Line Thickness Comparison (xStudio)"
echo "========================================="
python compare_thickness.py vector_thickness.png xstudio_output/vector_thickness.00000.png

echo ""
echo "========================================="
echo "Running Line Thickness Comparison (OpenRV)"
echo "========================================="
python compare_thickness.py vector_thickness.png "rv_output/vector_thickness.png_DEFAULT_MEDIA.00001.png"

echo ""
echo "========================================="
echo "Running Color Registration Comparison (xStudio)"
echo "========================================="
python compare_testchart.py xstudio_output/vector_colors.00000.png

echo ""
echo "========================================="
echo "Running Color Registration Comparison (OpenRV)"
echo "========================================="
python compare_testchart.py rv_output/vector_colors.png_DEFAULT_MEDIA.00001.png

echo ""
echo "Batch processing finished. Check testchart/xstudio_output/ and testchart/rv_output/."

