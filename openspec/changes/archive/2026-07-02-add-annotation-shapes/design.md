## Context

Currently, the OTIO-based synchronized review toolkit only supports freehand drawings (`PaintStart`, `PaintPoints`, `PaintEnd`) and text annotations (`TextAnnotation`). To facilitate more precise annotations and support the newer OpenRV annotations (found in `~/git/openrv_annotations/_build/stage/app/RV.app/Contents/MacOS/RV`), the schema needs support for structured shapes (ellipses, straight arrows, and rectangles).

## Goals / Non-Goals

**Goals:**
- Define three new serializable OTIO types in `SyncEvent.py` representing ellipses, rectangles, and arrows.
- Ensure the new schemas are documented and generated with examples in the unified reference document (`docs/otio_sync_docs.html`).
- Ensure type-safe and consistent serialization to OpenTimelineIO JSON.

**Non-Goals:**
- Implementing actual rendering or drawing of these shapes in OpenRV or xStudio (this is out of scope and will be implemented in subsequent client-specific changes).

## Decisions

### 1. Strongly-Typed Shape Classes vs. Generic Shape Class
We will implement specific classes (`EllipseAnnotation`, `RectangleAnnotation`, and `ArrowAnnotation`) rather than a single generic shape class.
- **Rationale**: Keeps properties strongly-typed and self-documenting. For example, `start`/`end` coordinates only exist on arrows, and `min`/`max` bounding box corners exist on ellipses and rectangles.
- **Alternatives Considered**: A generic `ShapeAnnotation` with a flexible `points` array. Rejected because it makes client-side conversion and validation complex and error-prone.

### 2. Using `inner_rgba` Alpha instead of a `filled` Boolean
For `EllipseAnnotation` and `RectangleAnnotation`, the shape's fill state is determined by the alpha channel of `inner_rgba`.
- **Rationale**: If `inner_rgba` is `None` or has an alpha of `0.0`, the shape is unfilled (transparent). If `inner_rgba` has `alpha > 0.0`, it is filled. This simplifies the class interface and prevents invalid states (e.g., `filled=True` but no fill color specified).
- **Alternatives Considered**: Keeping a separate `filled` boolean. Rejected as redundant.

### 3. Reusing `size` for Line/Border Thickness
We will use the property name `size` to represent the outline border thickness of all shapes.
- **Rationale**: Consistent with `PaintVertex.size` and `PaintVertices.size` in the existing schema.

### 4. Rectangle and Ellipse Bounding Box (`min` and `max`)
For `RectangleAnnotation` and `EllipseAnnotation`, we will define a `min` coordinate `[x, y]` (top-left) and `max` coordinate `[x, y]` (bottom-right) representing the bounding box.
- **Rationale**: This is directly compatible with the geometry representation used by Autodesk's annotation schema (`min` and `max` bounds), while keeping the properties flat (a simple list of 2 floats) to match our existing `TextAnnotation.position` parameter design rather than nesting `Position.1` custom class objects.

## Risks / Trade-offs

- **[Risk] Compatibility with older peers**: Older clients that do not have these schemas registered in their plugin manifest will fail to deserialize timelines containing them.
  - **Mitigation**: Deserialization is scoped to peers running the matching plugin version. Because this change updates the schema definitions, it serves as the baseline for all subsequent client implementations.
