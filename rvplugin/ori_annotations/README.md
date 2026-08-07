---
layout: default
title: RV Annotation Plugin
parent: ORI Annotations
nav_order: 2.2
---

# RV Annotation Plugin

This directory contains the **OpenRV Annotation Plugin** (`ori_annotations`). It provides tools to import and export drawing annotations and notes as custom OpenTimelineIO (`.otio`) files directly from the OpenRV interface.

Unlike the live synchronization plugin (`ori_sync`), this plugin is designed for file-based review workflows where annotations are saved to and loaded from disk.

---

## Features

- **Export Annotations**: Exports RV drawings, paint strokes, text captions, and notes into an `.otio` timeline.
- **Import Annotations**: Reads drawing strokes and events from an `.otio` file and reconstructs them in RV as active `RVPaint` strokes.
- **Custom Export Options**:
  - **Include Media**: Copies the source media files into the target export directory alongside the OTIO file.
  - **Include Annotation Media**: Renders the annotated frames to transparent PNGs using `rvio`.
  - **Export as Nested Stacks**: Wraps the timeline structure in nested stacks for compatibility.

---

## Installation

To build the plugin package:

```bash
./rvplugin/ori_annotations/makepackage.csh
```

This will create `oriannotations.zip` in the `rvplugin/ori_annotations` directory. This can be loaded directly into openRV in the Package Manager window, or you can use the reinstall.csh script to install it to your local OpenRV configuration.

To install:

```bash
./rvplugin/ori_annotations/reinstall.csh
```

This is designed to currently work only on Mac, you will need to adjust the script for other platforms.

---

## Usage

Once installed, the plugin adds the following options to the **Tools** menu in OpenRV:

### 1. Export Annotations

Select **Tools > Export annotations**. A custom dialog will appear letting you choose the output directory and configure the following options:

- **Include media in export**: Copies source media to the output directory.
- **Include Annotation Media**: Automatically triggers `rvio` to render frames with annotations to PNGs.
- **Export as Nested Stacks**: Structures the output OTIO track representation as nested stacks.
- **OTIO Export Name**: Customize the name of the output `.otio` file.

### 2. Import Annotations

Select **Tools > Import annotations**. Choose a `.otio` file, and the plugin will reconstruct the timeline along with any embedded paint stroke drawings and notes.
