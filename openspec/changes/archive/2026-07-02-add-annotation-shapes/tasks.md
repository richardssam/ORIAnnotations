## 1. Define Shape Classes in SyncEvent.py

- [x] 1.1 Implement the `EllipseAnnotation` schema class with serializable fields for `min`, `max`, `rgba`, `size`, `inner_rgba`, `uuid`, and `timestamp`
- [x] 1.2 Implement the `RectangleAnnotation` schema class with serializable fields for `min`, `max`, `rgba`, `size`, `inner_rgba`, `uuid`, and `timestamp`
- [x] 1.3 Implement the `ArrowAnnotation` schema class with serializable fields for `start`, `end`, `rgba`, `size`, `uuid`, and `timestamp`

## 2. Update Configuration and Documentation

- [x] 2.1 Add example payloads and categories for `EllipseAnnotation`, `RectangleAnnotation`, and `ArrowAnnotation` in `docs/config.yml` under `otio_events`
- [x] 2.2 Run the unified documentation generator `python docs/doc_generator.py --config docs/config.yml` to produce the updated `otio_sync_docs.html`

## 3. Implement Vector Primitives Test Chart

- [x] 3.1 Implement PIL drawing logic in `testchart/generate_testchart.py` to produce a reference image showing hollow and filled ellipses, rectangles, and straight arrows
- [x] 3.2 Implement `vector_primitives_annotations()` in `testchart/generate_testchart.py` to construct corresponding `EllipseAnnotation`, `RectangleAnnotation`, and `ArrowAnnotation` commands
- [x] 3.3 Register the new test chart frames in `main()` of `generate_testchart.py` and export them to `testchart_annotations.otio`

## 4. Verification

- [x] 4.1 Create a verification script to instantiate the new shape classes with the configured examples, serialize them to JSON, and check that all properties roundtrip cleanly
- [x] 4.2 Run the updated `testchart/generate_testchart.py` tool to verify that the primitives test chart generates and exports to OTIO successfully without errors
- [x] 4.3 Verify the generated HTML documentation correctly displays the parameters and code snippets for all three new shapes
