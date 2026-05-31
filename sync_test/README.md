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

You can run the full suite or a specific test using the CLI entry point:

```bash
# Run all tests using the default sync_tests.yaml
python -m sync_test.cli run

# Run a specific test
python -m sync_test.cli run --test xstudio_vs_openrv_demo

# Run with custom config
python -m sync_test.cli run --config my_tests.yaml

# Enable verbose logging
python -m sync_test.cli run -v
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
