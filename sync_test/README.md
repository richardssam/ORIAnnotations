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

## Checkpoint Validation

For recording-driven tests (`script_driven: false`), the runner derives two kinds of point-in-time checkpoints from the `.jsonl` recording: **frame checkpoints** (an app's playhead should read a specific frame) and **structural state checkpoints** (an app's full timeline structure should match a recorded `STATE_SNAPSHOT`). Each checkpoint becomes due `checkpoint_validation_delay` seconds after its recorded event.

While a checkpoint is being validated, the runner **freezes recording playback** — pausing `SyncPlayer` for the duration of the check and resuming once it reaches a verdict (pass, fail, or retry-exhausted). This means `checkpoint_validation_delay` is no longer a race against the recording: it's simply how long after the event to *start* checking. Once validation begins, the recording cannot advance further out from under it, however long the check itself takes (an inspector RPC, a multi-second convergence poll). Because the target can no longer go stale mid-check, both checkpoint types are retry-eligible: a first-attempt mismatch is retried once at 2x the original deadline (still frozen) before failing, the same bounded-retry pattern used elsewhere in the runner.

Every freeze/resume is logged with its duration (`⏸ Playback frozen N.Ns for checkpoint validation`), so time removed from real-time replay pacing is visible rather than silently distorting it. Total suite runtime grows by the sum of these freezes — expected, and bounded by each checkpoint's own retry deadline.

Freezing protects the evaluation window; it cannot protect a window that started elapsing before evaluation was possible at all. So the recording's t=0 is no longer set by `SyncPlayer`'s own peer-join gate. That gate sees a `STATE_SNAPSHOT` leave over RabbitMQ — which can happen while the runner is still waiting for apps to finish booting — but it cannot see an app finish *applying* that state and become queryable over HTTP. The two signals can be far apart, and the whole gap used to be charged against the recording's timeline before the validation loop ever ran.

The gate now tracks peer-snapshot delivery only. The runner calls `player.arm_clock()` after its own `_wait_for_snapshot()` confirms every app reports a clip, and the clock starts once the gate's conditions *and* that arming have both happened — whichever comes last. Arming never bypasses the gate: dispatching before peers hold the initial snapshot would be a worse race than the one this closes. Checkpoint hold windows therefore start from a moment the runner has itself confirmed.

The interval between the two is logged (`[player] Clock armed N.Ns after the peer-join gate cleared`), so the gap is a number you can read rather than an inference. Its companion line — `[player] Peer-join gate cleared; holding the recording's clock until arm_clock() is called` — is also the first thing to look for if a recording-driven test appears to hang: there is deliberately no auto-arm fallback, so a `wait_for_peer=True` caller that never arms services the network forever and dispatches nothing.

`frame_held` / `_FRAME_HOLD_SAFETY_MARGIN` and the `validation_delay`-based silence filters in `derive_checkpoints`/`derive_state_checkpoints` remain in place. Their original purpose — buying enough window to survive a still-advancing recording — is now largely redundant now that playback freezes during validation, but they still guard against sampling mid-burst, so they're left as belt-and-braces rather than load-bearing. Narrowing them is a separate, independently-verifiable follow-on.

## Test Configuration

Tests are defined in a YAML configuration file (`sync_tests.yaml` at the project root).

Example `sync_tests.yaml`:

```yaml
tests:
  - name: "xstudio_vs_openrv_demo"
    description: >
      Replays a basic two-app session (xStudio + OpenRV) from a recorded
      .jsonl and asserts both apps converge on the same clip/frame/state.
      Baseline smoke test for the sync pipeline.
    recording: "demo.jsonl"
    apps:
      - "xstudio"
      - "openrv"
```

`description` is **required** for every test in `sync_tests.yaml` — it's the canonical suite definition, and the runner rejects loading it if any entry is missing one. Explain what scenario the test exercises and why it exists, so a future reader (or a future you) can reconstruct it without archaeology.

Two optional fields track known issues:

- `status: known_broken` — the test is expected to fail right now for a known, already-tracked reason. It still runs and is still reported, but a `known_broken` failure does not fail the overall suite.
- `blocked_by: "<openspec-change-name>"` — required alongside `status: known_broken`, naming the OpenSpec change expected to fix it. If a `known_broken` test unexpectedly passes, the summary flags it (XPASS-style) as a signal to check whether the status can be reverted to `stable`.

`sync_tests_xstudio.yaml` and `sync_demos.yaml` duplicate a subset of `sync_tests.yaml`'s entries for convenience and are **not** required to carry `description`/`status`/`blocked_by` — `sync_tests.yaml` is the single source of truth for a test's intent.

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
