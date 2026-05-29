# Test Chart Annotations Batch Processing

This folder contains test charts and batch scripts to automate loading annotations from an OTIO file and exporting them as images in both **xStudio** and **OpenRV**.

## Contents

- `testchart_annotations.otio`: The source OTIO file containing test chart media references and annotation SyncEvents (paint strokes, text captions, varying stroke widths, etc.).
- `batch_xstudio.py`: Command-line script to automate importing annotations into **xStudio** and exporting the transparent drawings as PNG files.
- `batch_openrv.py`: Command-line runner to automate importing annotations into **OpenRV** and rendering the annotated frames using `rvio`.
- `batch_openrv_helper.py`: Helper script evaluated inside OpenRV to handle the import and export.
- `run_batch.csh`: C-shell script that runs both the xStudio and OpenRV batch processing in sequence.

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
