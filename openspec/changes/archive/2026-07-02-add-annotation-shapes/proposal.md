## Why

Currently, the OTIO annotation schema only supports freehand drawings (`PaintStart` / `PaintPoints` / `PaintEnd`) and text annotations (`TextAnnotation`). Users need to be able to draw structured shapes—specifically circles/ellipses, straight arrows, and rectangles/squares. Approximate hand-drawing of these shapes is inefficient, imprecise, and lacks structured metadata that would allow peers to edit them as semantic objects.

## What Changes

- Add three new strongly-typed annotation schema classes to the `SyncEvent` definition:
  - `EllipseAnnotation.1`: Bounding box `min` `[x, y]` (top-left) and `max` `[x, y]` (bottom-right) coordinates, line `size`, border `rgba`, and fill `inner_rgba` (where alpha > 0.0 indicates a filled shape).
  - `RectangleAnnotation.1`: Bounding box `min` `[x, y]` (top-left) and `max` `[x, y]` (bottom-right) coordinates, line `size`, border `rgba`, and fill `inner_rgba` (where alpha > 0.0 indicates a filled shape).
  - `ArrowAnnotation.1`: Start coordinate `[x, y]` (tail), end coordinate `[x, y]` (tip), line `size`, and arrow color `rgba`.
- Update the documentation configuration and documentation generator to recognize, document, and generate examples for these new shapes.
- Update the test chart generation tool (`testchart/generate_testchart.py`) to generate a new `vector_primitives.png` (and `_uhd.png`) test chart image showing these shapes, and export the corresponding new `SyncEvent` shape annotations in `testchart_annotations.otio`.

These shapes have been defined with the newer openrv session in ~/git/openrv_annotations/_build/stage/app/RV.app/Contents/MacOS/RV we will need to modify the Annotation save events to include these new shapes. For xstudio we will need to convert them back to lines.

## Capabilities

### New Capabilities

*(None)*

### Modified Capabilities

- `otio-annotation-sync`: Adding Circle, Rectangle, and Arrow SyncEvent schemas for storing and synchronizing structured geometric shapes inside OTIO timelines.

## Impact

- `otio_event_plugin/schemadefs/SyncEvent.py`: Add the new `CircleAnnotation`, `RectangleAnnotation`, and `ArrowAnnotation` serialization schemas.
- `docs/config.yml`: Add documentation categories and example payloads for the new shapes.
- `docs/otio_sync_docs.html`: Re-generated to include documentation and examples for the new shapes.
- `testchart/generate_testchart.py`: Add a new `vector_primitives` frame with PIL-drawn shapes and equivalent OTIO shape annotation objects.
- Client applications (OpenRV and xStudio sync plugins) will need to be updated to convert these shapes to/from their native representations in future changes.
