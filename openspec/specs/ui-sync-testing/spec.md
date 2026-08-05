# UI Sync Testing

## Purpose
TBD - This specification defines the requirements for the automated UI sync test framework.

## Requirements

### Requirement: Automated CLI Test Runner
The system SHALL provide a command-line test runner (`sync_test`) that can launch applications, replay recorded `.jsonl` sessions, and verify application state.

#### Scenario: Running a successful test suite
- **WHEN** the runner is executed with a valid `sync_tests.yaml` config
- **THEN** it executes all tests, verifies state assertions successfully, and exits with a 0 status code

### Requirement: Test Suite Configuration
The system SHALL support configuring test suites via a YAML file defining test names, associated `.jsonl` recordings, and the applications to launch.

#### Scenario: Running a specific test
- **WHEN** the runner is executed with the `--test <name>` argument
- **THEN** it only executes the specific test defined in the YAML configuration

### Requirement: Application Introspection (RPC)
The system SHALL inject a lightweight RPC server into launched applications that exposes a `GET_STATE` endpoint to return the true logical state (frame, clip, annotations). Annotation state SHALL include, per stroke, its native geometry (OpenRV: `width`/`size`; xStudio: `thickness`/`size`) in addition to the existing per-kind counts, so callers can assert on drawn/received geometry and not just presence.

#### Scenario: Querying application state
- **WHEN** the runner requests state from a launched application
- **THEN** the application returns a JSON payload containing its actual playhead, clip, and annotation state

#### Scenario: Querying annotation geometry
- **WHEN** the runner requests state from a launched application that has one or more annotations
- **THEN** the returned annotation state includes each stroke's native width/size (OpenRV) or thickness/size (xStudio), in addition to the existing stroke/caption counts

### Requirement: Isolated Application Logging
The system SHALL redirect stdout and stderr of each spawned application into isolated log files.

#### Scenario: Debugging a failed test
- **WHEN** a test fails due to a state mismatch
- **THEN** the runner outputs the failure diff and the location of the isolated application log file for the LLM or developer to review

### Requirement: Script-Driven Annotation Drawing
The system SHALL support a `draw_annotation` script-driven command that makes a driver app produce a native pen, rectangle, ellipse, arrow, or text annotation and broadcast it via that app's real, unmodified production send path — without driving real mouse/UI input.

For OpenRV, the command SHALL write native paint-node properties directly (not via the OTIO-import codec path) and then invoke the same function OpenRV's real pen-up handler invokes to broadcast a completed stroke. For xStudio, the command SHALL write a native annotation via the existing remote annotation-write API into the live session the running plugin is watching, and rely on that plugin's own existing poll loop to detect and broadcast it, exactly as it would a real user-drawn stroke.

The `rect`, `ellipse`, `arrow`, and `text` kinds SHALL be supported as driver actions for OpenRV only. xStudio SHALL NOT be required to support any of these kinds as a driver action until xStudio's native shape/text-drawing broadcast path exists.

For `text`, the command SHALL support two payload modes: writing the paint node's `fontSize` property directly (a WCS fraction of image height — the property that actually governs on-screen rendering in the current QPainter-based text renderer), or, via a `legacy_size` payload option, writing only the legacy `size` property with no `fontSize` at all, to exercise the fallback conversion older sessions/broadcasts rely on.

#### Scenario: Drawing a pen stroke in OpenRV
- **WHEN** the runner sends `{"action": "draw_annotation", "kind": "pen", ...}` to an OpenRV instance
- **THEN** OpenRV writes a native pen paint-node with the requested nominal width and broadcasts it to peers via its real send path, with no test-only broadcast code involved

#### Scenario: Drawing a pen stroke in xStudio
- **WHEN** the runner sends `{"action": "draw_annotation", "kind": "pen", ...}` to an xStudio instance
- **THEN** xStudio's live session gains a bookmark with the requested nominal thickness
- **AND** the running plugin's own poll loop detects and broadcasts it to peers within its existing debounce/scan-interval bounds, with no new xStudio-plugin code involved

#### Scenario: Drawing a rectangle in OpenRV
- **WHEN** the runner sends `{"action": "draw_annotation", "kind": "rect", ...}` to an OpenRV instance
- **THEN** OpenRV writes a native rectangle paint-node with the requested nominal border width and broadcasts it to peers via its real send path

#### Scenario: Drawing an ellipse in OpenRV
- **WHEN** the runner sends `{"action": "draw_annotation", "kind": "ellipse", ...}` to an OpenRV instance
- **THEN** OpenRV writes a native ellipse paint-node with the requested nominal border width and broadcasts it to peers via its real send path

#### Scenario: Drawing an arrow in OpenRV
- **WHEN** the runner sends `{"action": "draw_annotation", "kind": "arrow", ...}` to an OpenRV instance
- **THEN** OpenRV writes a native arrow paint-node with the requested nominal shaft thickness and broadcasts it to peers via its real send path

#### Scenario: Drawing a text annotation in OpenRV using the current fontSize convention
- **WHEN** the runner sends `{"action": "draw_annotation", "kind": "text", "font_size_wcs": ..., "position": ..., ...}` to an OpenRV instance
- **THEN** OpenRV writes a native text paint-node component with a `fontSize` property set to the requested nominal WCS-fraction size (times `scale`, if given), a reconstructible legacy `size`, and the requested position/text content, and broadcasts it to peers via its real send path

#### Scenario: Drawing a text annotation exercising the legacy fallback path
- **WHEN** the runner sends `{"action": "draw_annotation", "kind": "text", "legacy_size": ..., ...}` to an OpenRV instance
- **THEN** OpenRV writes a native text paint-node component with only the legacy `size` property set (no `fontSize`), broadcasting it to peers via its real send path
- **AND** the peer's received geometry SHALL match the value predicted by reconstructing the fallback conversion (`size * 10000 / 1080 * scale`), not by misreading `size` as if it were already a WCS fraction

#### Scenario: Shape and text drawing is not required from xStudio
- **WHEN** a test suite targets xStudio as the driver app
- **THEN** it SHALL NOT be required to support `kind: "rect"`, `"ellipse"`, `"arrow"`, or `"text"`, since xStudio has no wired-up native shape/text broadcast path

### Requirement: Round-Trip Annotation Geometry Verification
The system SHALL be able to verify, after a `draw_annotation` command converges to a peer, that the peer's native readback of the annotation's width/size/font-size/position matches — within `assertAlmostEqual`-style tolerance — an expected value computed by feeding the driver's nominal input through the same production codec functions and constants the apps themselves use for that conversion (not a hardcoded or independently-derived expected value).

For `text`, the codec functions used to compute the expected value SHALL be the ones that actually govern the current on-screen rendering (`fontSize`-based), not a stale or dead property/formula — a numeric round-trip check that merely re-derives its expected value from the same wrong formula the production code also (mis)uses would pass without ever detecting a real defect; this requirement exists specifically to catch that class of drift (see the `sync-test-text-annotation-scale` change design doc for the concrete case this happened).

Pen coverage SHALL run bidirectionally (OpenRV driving/xStudio verifying, and xStudio driving/OpenRV verifying). Rectangle, ellipse, arrow, and text coverage SHALL run with OpenRV as the driver and xStudio as the verifier.

#### Scenario: OpenRV-drawn pen width round-trips to xStudio
- **WHEN** OpenRV draws a pen stroke with a chosen nominal native width and it converges to an xStudio peer
- **THEN** the xStudio peer's native stroke thickness, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal width through OpenRV's reverse codec and then xStudio's forward codec

#### Scenario: xStudio-drawn pen width round-trips to OpenRV
- **WHEN** xStudio draws a pen stroke with a chosen nominal native thickness and it converges to an OpenRV peer
- **THEN** the OpenRV peer's native stroke width, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal thickness through xStudio's reverse codec and then OpenRV's forward codec

#### Scenario: OpenRV-drawn rectangle border width round-trips to xStudio
- **WHEN** OpenRV draws a rectangle with a chosen nominal native border width and it converges to an xStudio peer
- **THEN** the xStudio peer's native tessellated-stroke thickness, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal border width through OpenRV's reverse shape codec and then xStudio's forward shape-tessellation codec

#### Scenario: OpenRV-drawn ellipse border width round-trips to xStudio
- **WHEN** OpenRV draws an ellipse with a chosen nominal native border width and it converges to an xStudio peer
- **THEN** the xStudio peer's native tessellated-stroke thickness, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal border width through OpenRV's reverse shape codec and then xStudio's forward shape-tessellation codec

#### Scenario: OpenRV-drawn arrow shaft thickness round-trips to xStudio
- **WHEN** OpenRV draws an arrow with a chosen nominal native shaft thickness and it converges to an xStudio peer
- **THEN** the xStudio peer's native tessellated-stroke thickness, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal shaft thickness through OpenRV's reverse arrow codec and then xStudio's forward shape-tessellation codec

#### Scenario: OpenRV-drawn text font size round-trips to xStudio (current fontSize convention)
- **WHEN** OpenRV draws a text annotation with a chosen nominal `fontSize` (WCS fraction of image height) and it converges to an xStudio peer
- **THEN** the xStudio peer's native caption `font_size`, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal size through OpenRV's reverse text codec (which reads `fontSize`, reconstructing via the legacy fallback only when `fontSize` is absent) and then xStudio's forward text codec

#### Scenario: OpenRV-drawn text font size round-trips to xStudio (legacy fallback convention)
- **WHEN** OpenRV draws a text annotation with only a legacy `size` set (no `fontSize`) and it converges to an xStudio peer
- **THEN** the xStudio peer's native caption `font_size` SHALL be within tolerance of the value predicted by first reconstructing the WCS-fraction size via the fallback formula (`size * 10000 / 1080 * scale`), then running it through the same forward text codec as the current-convention case

#### Scenario: OpenRV-drawn text position round-trips to xStudio
- **WHEN** OpenRV draws a text annotation with a chosen nominal native `position` and it converges to an xStudio peer
- **THEN** the xStudio peer's native caption position, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by applying the aspect-ratio coordinate transform (`x_xs = x_otio / aspect_half`, `y_xs = -y_otio / aspect_half`) to that nominal position

### Requirement: Script-Driven Frame Capture
The system SHALL support a `capture_frame` script-driven command, available for both OpenRV and xStudio driver/peer apps, that renders the target app's current live frame (video plus applied annotations) to an image file at a caller-specified output path.

#### Scenario: Capturing a peer's rendered frame after a draw_annotation converges
- **WHEN** the runner sends `{"action": "capture_frame", "output_path": ..., ...}` to an app after a prior `draw_annotation` has converged
- **THEN** the app SHALL render its current frame, including the annotation, to the requested output path
