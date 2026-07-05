## ADDED Requirements

### Requirement: Script-Driven Annotation Drawing
The system SHALL support a `draw_annotation` script-driven command that makes a driver app produce a native pen or rectangle annotation and broadcast it via that app's real, unmodified production send path — without driving real mouse/UI input.

For OpenRV, the command SHALL write native paint-node properties directly (not via the OTIO-import codec path) and then invoke the same function OpenRV's real pen-up handler invokes to broadcast a completed stroke. For xStudio, the command SHALL write a native annotation via the existing remote annotation-write API into the live session the running plugin is watching, and rely on that plugin's own existing poll loop to detect and broadcast it, exactly as it would a real user-drawn stroke.

The `rect` kind SHALL be supported as a driver action for OpenRV only. xStudio SHALL NOT be required to support `rect` as a driver action until xStudio's native shape-drawing broadcast path exists.

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

#### Scenario: Rectangle drawing is not required from xStudio
- **WHEN** a test suite targets xStudio as the driver app
- **THEN** it SHALL NOT be required to support `kind: "rect"`, since xStudio has no wired-up native shape broadcast path

### Requirement: Round-Trip Annotation Geometry Verification
The system SHALL be able to verify, after a `draw_annotation` command converges to a peer, that the peer's native readback of the annotation's width/size matches — within `assertAlmostEqual`-style tolerance — an expected value computed by feeding the driver's nominal input through the same production codec functions and constants the apps themselves use for that conversion (not a hardcoded or independently-derived expected value).

Pen coverage SHALL run bidirectionally (OpenRV driving/xStudio verifying, and xStudio driving/OpenRV verifying). Rectangle coverage SHALL run with OpenRV as the driver and xStudio as the verifier.

#### Scenario: OpenRV-drawn pen width round-trips to xStudio
- **WHEN** OpenRV draws a pen stroke with a chosen nominal native width and it converges to an xStudio peer
- **THEN** the xStudio peer's native stroke thickness, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal width through OpenRV's reverse codec and then xStudio's forward codec

#### Scenario: xStudio-drawn pen width round-trips to OpenRV
- **WHEN** xStudio draws a pen stroke with a chosen nominal native thickness and it converges to an OpenRV peer
- **THEN** the OpenRV peer's native stroke width, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal thickness through xStudio's reverse codec and then OpenRV's forward codec

#### Scenario: OpenRV-drawn rectangle border width round-trips to xStudio
- **WHEN** OpenRV draws a rectangle with a chosen nominal native border width and it converges to an xStudio peer
- **THEN** the xStudio peer's native tessellated-stroke thickness, read via its `/state` annotation geometry, SHALL be within tolerance of the value predicted by running that nominal border width through OpenRV's reverse shape codec and then xStudio's forward shape-tessellation codec

## MODIFIED Requirements

### Requirement: Application Introspection (RPC)
The system SHALL inject a lightweight RPC server into launched applications that exposes a `GET_STATE` endpoint to return the true logical state (frame, clip, annotations). Annotation state SHALL include, per stroke, its native geometry (OpenRV: `width`/`size`; xStudio: `thickness`/`size`) in addition to the existing per-kind counts, so callers can assert on drawn/received geometry and not just presence.

#### Scenario: Querying application state
- **WHEN** the runner requests state from a launched application
- **THEN** the application returns a JSON payload containing its actual playhead, clip, and annotation state

#### Scenario: Querying annotation geometry
- **WHEN** the runner requests state from a launched application that has one or more annotations
- **THEN** the returned annotation state includes each stroke's native width/size (OpenRV) or thickness/size (xStudio), in addition to the existing stroke/caption counts
