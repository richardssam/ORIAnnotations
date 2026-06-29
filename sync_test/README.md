---
layout: default
title: Sync Testing Framework.
parent: ORI Sync Tools
nav_order: 2.3
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

## Isolated Logging

When apps are launched, their `stdout` and `stderr` are redirected to isolated log files to make debugging easy. Logs are grouped by test name in the top-level `logs/` directory:

```
logs/
└── xstudio_vs_openrv_demo/
    ├── openrv_9001.log
    ├── xstudio_9000.log
    └── xstudio_inspector_9000.log
```
