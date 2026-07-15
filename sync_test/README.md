---
layout: default
title: Sync Testing Framework
parent: ORI Sync Tools
nav_order: 3.5
---

# UI Sync Test Framework

The `sync_test` directory contains an automated integration testing framework for the ORIAnnotations UI synchronization pipeline. It ensures that peer applications (like xStudio and OpenRV) correctly exchange state (playhead position, active clip, annotations) over the RabbitMQ network.

## How It Works

Instead of testing only the `SyncManager` library in isolation, this framework provides true end-to-end testing:

1. **App Spawner:** Launches the actual application binaries (xStudio, OpenRV) in isolated subprocesses.
2. **Inspection Server (RPC):** Injects a lightweight HTTP server (`inspector.py`) into the running applications. This exposes a `/state` endpoint to query the *true logical state* of the app directly from its native Python API (`xstudio_hook.py`, `openrv_hook.py`).
3. **Playback Automation:** Uses the existing `sync_recorder.player.SyncPlayer` to stream a `.jsonl` recording of OTIO sync events into the RabbitMQ exchange, simulating a remote master peer driving the session.
4. **State Assertion:** The Test Runner (`runner.py`) continuously polls the `/state` endpoint of all spawned apps and asserts that they match the expected synchronized state.

## Test Configuration

Tests are defined in a YAML configuration file (`sync_tests.yaml` at the project root).

Example `sync_tests.yaml`:

```yaml
tests:
  - name: "xstudio_vs_openrv_demo"
    recording: "demo.jsonl"
    apps:
      - "xstudio"
      - "openrv"
```

## Running Tests

You can run the full suite or a specific test using the `run_tests.sh` wrapper script. This script automatically configures the `PYTHONPATH` so the testing modules can be imported correctly.

```bash
# Run all tests using the default sync_tests.yaml
./run_tests.sh run

# Run a specific test
./run_tests.sh run --test xstudio_vs_openrv_demo

# Run with custom config
./run_tests.sh run --config my_tests.yaml

# Enable verbose logging
./run_tests.sh run -v
```

## Script-Driven Tests

Tests with `script_driven: true` drive the first app in the `apps` list via a sequence of high-level commands rather than replaying a `.jsonl` recording. The other app(s) receive changes through the normal sync session. After all commands complete the runner waits for convergence and then asserts that both apps report the same state.

Commands can be supplied in two ways:

1. **Explicit commands in `sync_tests.yaml`** via a `commands` key — use this when you want full control over the sequence.
2. **Derived from the recording** — if no `commands` key is present, the runner parses the `.jsonl` file and extracts `add_media` / `delete_media` / `set_selection` commands automatically from `INSERT_CHILD`, `REMOVE_CHILD`, and `PLAYBACK_SETTINGS` events.

### Available Actions

#### `add_media`

Add a media file to the first playlist in the driver app. The sync plugin broadcasts the insertion to all peers.

| Field    | Type   | Description                                           |
| -------- | ------ | ----------------------------------------------------- |
| `action` | string | `"add_media"`                                         |
| `url`    | string | Path to media. Relative paths resolve from repo root. |

```yaml
- action: "add_media"
  url: "test_media/source/encoded_notc/car_ACES_sRGB.mov"
```

#### `delete_media`

Remove a media item from the driver app by name. The sync plugin broadcasts the removal to all peers.

| Field    | Type   | Description                               |
| -------- | ------ | ----------------------------------------- |
| `action` | string | `"delete_media"`                          |
| `name`   | string | Clip name to remove. Matches file basename. |

```yaml
- action: "delete_media"
  name: "graphic_ACES_sRGB.mov"
```

#### `set_selection`

Set the active/viewed item in the driver app. Useful to verify selection sync or to put both apps in a known state before a subsequent assertion. For the first playlist, the aliases `"Default Sequence"`, `"Sequence"`, and `"Default"` also match regardless of the actual name.

| Field    | Type   | Description                                  |
| -------- | ------ | -------------------------------------------- |
| `action` | string | `"set_selection"`                            |
| `name`   | string | Name of the sequence or clip to make active. |

```yaml
- action: "set_selection"
  name: "car_ACES_sRGB.mov"
```

#### `save_session`

Save the current session to a file. Used automatically by the runner at the end of each test to capture final app state for debugging — you generally do not need this in a `commands` list.

| Field      | Type   | Description                              |
| ---------- | ------ | ---------------------------------------- |
| `action`   | string | `"save_session"`                         |
| `filepath` | string | Absolute path to write the session file. |

```yaml
- action: "save_session"
  filepath: "/tmp/debug_session.xst"
```

#### `draw_annotation`

Make the driver app produce a native pen or rectangle annotation and broadcast it via that app's real, unmodified send path — no mouse/UI automation involved. Writes go directly to native properties/dicts (RV paint-node properties; xStudio bookmark stroke dicts), as if a real draw had just completed, then trigger (RV) or wait on (xStudio) the same broadcast machinery a live user stroke uses. This exists to exercise the *reverse* codec direction (native draw → `SyncEvent`) that `testchart/` never covers — `testchart/` only exercises the forward (OTIO import → app) direction.

| Field          | Type   | Description                                                  |
| -------------- | ------ | ------------------------------------------------------------ |
| `action`       | string | `"draw_annotation"`                                          |
| `kind`         | string | `"pen"` (both apps) or `"rect"`, `"ellipse"`, `"arrow"` (OpenRV only, see below). |
| `width`        | float  | Pen only, OpenRV: nominal native RV pen width.               |
| `thickness`    | float  | Pen only, xStudio: nominal native xStudio pen thickness; OR Arrow only, OpenRV: nominal native RV arrow shaft thickness. |
| `border_width` | float  | Rect/Ellipse only, OpenRV: nominal native RV border width.   |
| `points`       | list   | Pen only, OpenRV, optional: flat [x0, y0, x1, y1] override.  |

```yaml
- action: "draw_annotation"
  kind: "pen"
  width: 3.0
```

**Note:** `kind: "rect"`, `"ellipse"`, and `"arrow"` are only supported with OpenRV as the driver app. xStudio has no wired-up native shape-drawing broadcast path yet, so a shape command sent to xStudio raises an error rather than silently no-op'ing.

Use `sync_test.annotation_assertions` to verify round-trip fidelity after a `draw_annotation` converges to a peer — it computes the expected peer-side width/thickness from the same production codec constants the apps themselves use (not a hardcoded number), so the check fails precisely when an app's forward and reverse conversions disagree.

#### `capture_frame`

Render the target app's current live frame (video plus any applied annotations) to an image file, in-process — no external render subprocess and no save/reload round-trip. xStudio resolves the bookmark at the current playhead's media/frame and renders via `OffscreenViewport.render_bookmark_with_transparency`; OpenRV grabs its live viewport widget (`rv.commands.sessionGLView()` wrapped as a Qt widget, `.grab().save(...)`), the same technique `testchart/grab_frame.py` uses.

| Field         | Type   | Description                                        |
| ------------- | ------ | -------------------------------------------------- |
| `action`      | string | `"capture_frame"`                                  |
| `output_path` | string | Absolute path to write the PNG.                    |
| `width`       | int    | Optional requested output width (default 1920).    |
| `height`      | int    | Optional requested output height (default 1080).   |

```yaml
- action: "capture_frame"
  output_path: "/tmp/capture.png"
```

`width`/`height` are a *request*, not a guarantee (xStudio honors them exactly; OpenRV's in-process grab may not, depending on window/HiDPI state) — any comparison against a capture should read the saved image's own actual pixel dimensions rather than assume the request was honored precisely. See `sync_test.visual_geometry` and the `visual_check` flag below for a ready-made comparison built on this.

##### `visual_check` (in the `annotation_geometry` yaml block)

Setting `visual_check: true` inside a test's `annotation_geometry` block additionally captures *both* the driver's and the peer's rendered frame after the numeric round-trip check and verifies the annotation is actually rendered where/how thick expected on each — projecting the same known OTIO-normalized geometry (`sync_test.annotation_assertions.DEFAULT_SHAPE_GEOMETRY`, driver-adjusted for xStudio-native pen strokes via `shape_geometry_for_driver`) into each captured image's own actual resolution and sampling a perpendicular cross-section, the same technique `testchart/compare_testchart.py` uses for its reference chart. Capturing both apps (not just the peer) means both PNGs land in `logs_dir` for inspection and both hosts' `capture_frame` implementations stay under test. This is the check that would have caught the 2x rect-border bug automatically instead of requiring manual visual inspection — it also caught a real colour bug in this harness's own `xstudio_hook.py::_draw_xstudio_annotation` (an unrecognised `"type": "Brush"` and missing legacy `r`/`g`/`b` keys silently rendered every xStudio-driven pen stroke as plain white, regardless of the requested colour — the numeric check alone only ever asserted thickness). Supports every `draw_annotation` kind (`pen`/`rect`/`ellipse`/`arrow`); soft-skips (does not fail the test) if PIL/numpy are unavailable in the interpreter `runner.py` is running under. The pass/fail tolerance scales with the expected thickness (floored at `tolerance_px`, default 4px) to account for the proportionally larger antialiasing bias on thick/soft-edged strokes — the same effect `compare_thickness.py` already reports as normal (e.g. ~1.19x scale factors on solid lines).

```yaml
annotation_geometry:
  driver: "openrv"
  peer: "xstudio"
  kind: "rect"
  nominal: 0.005
  visual_check: true
```

## Isolated Logging

When apps are launched, their `stdout` and `stderr` are redirected to isolated log files to make debugging easy. Logs are grouped by test name in the top-level `logs/` directory:

```
logs/
└── xstudio_vs_openrv_demo/
    ├── openrv_9001.log
    ├── xstudio_9000.log
    └── xstudio_inspector_9000.log
```
