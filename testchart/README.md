---
layout: default
title: Annotation Test Charts
parent: ORI Annotations
nav_order: 2.5
---

# Annotation Test Charts

This folder contains test charts and batch scripts to automate loading annotations from an OTIO file and exporting them as images in both **xStudio** and **OpenRV**.

## Contents

- `testchart_annotations.otio`: The source OTIO file containing test chart media references and annotation SyncEvents (paint strokes, text captions, varying stroke widths, etc.).
- `batch_xstudio.py`: Command-line script to automate importing annotations into **xStudio** and exporting the transparent drawings as PNG files.
- `batch_openrv.py`: Command-line runner to automate importing annotations into **OpenRV** and rendering the annotated frames using `rvio`.
- `batch_openrv_helper.py`: Helper script evaluated inside OpenRV to handle the import and export.
- `run_batch.csh`: C-shell script that runs both the xStudio and OpenRV batch processing in sequence.

## Vector Test Charts

Below is a list of the vector test chart image assets used in the test suite:

| Chart Type | Standard Resolution |
| :--- | :--- |
| **Calligraphy** | ![Calligraphy]({% link testchart/vector_calligraphy.png %}) |
| **Colors** | ![Colors]({% link testchart/vector_colors.png %}) |
| **Fonts** | ![Fonts]({% link testchart/vector_fonts.png %}) |
| **Primitives** | ![Primitives]({% link testchart/vector_primitives.png %}) |
| **Shapes** | ![Shapes]({% link testchart/vector_shapes.png %}) |
| **Thickness** | ![Thickness]({% link testchart/vector_thickness.png %}) |

## How to Run

You can run the full test suite (both xStudio and OpenRV batch processing) using the C-shell script:

```bash
./testchart/run_batch.csh
```

### Running xStudio Batch Processing Individually

```bash
python testchart/batch_xstudio.py testchart/testchart_annotations.otio testchart/xstudio_output
```

*Note: The script automatically handles running under the correct xStudio Python interpreter and configures the environment/port parameters to ensure xStudio starts and connects successfully.*

### Running OpenRV Batch Processing Individually

```bash
python testchart/batch_openrv.py testchart/testchart_annotations.otio testchart/rv_output
```

*Note: This script launches OpenRV in GUI mode, loads the timeline/media, reconstructs paint strokes/captions, runs `rvio` to render the outputs, and closes cleanly.*
