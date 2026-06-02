## 1. Inspection Server (RPC)
- [x] 1.1 Create the lightweight Python Inspection Server module (HTTP or Socket based) that can be injected into the target applications.
- [x] 1.2 Implement the `GET_STATE` endpoint hook for XStudio to retrieve its true logical state (frame, clip, annotations).
- [x] 1.3 Implement the `GET_STATE` endpoint hook for OpenRV to retrieve its true logical state.

## 2. Test Runner Core

- [x] 2.1 Set up the `sync_test` Python package and CLI entry point (`argparse` or similar).
- [x] 2.2 Implement parsing logic for the `sync_tests.yaml` configuration file.
- [x] 2.3 Implement the Application Spawner to launch XStudio and OpenRV as `subprocess`es.
- [x] 2.4 Implement log redirection in the Spawner to route stdout/stderr to isolated, test-specific log files.
- [x] 2.5 Implement robust process teardown (e.g., using `atexit` or context managers) to prevent zombie processes.

## 3. Test Execution Loop

- [x] 3.1 Implement `.jsonl` event parsing to load recorded sessions.
- [x] 3.2 Implement event broadcasting over the RabbitMQ sync bus (reusing or adapting parts of `sync_recorder`).
- [x] 3.3 Implement the verification polling step: fetch state from the spawned applications via their Inspection Server ports.
- [x] 3.4 Implement the state assertion logic to diff the returned JSON objects.
- [x] 3.5 Format the test output clearly (Pass/Fail with diffs) and ensure the CLI exits with the correct status code (0 for pass, non-zero for fail).

## 4. Verification & Testing

- [x] 4.1 Create an initial `sync_tests.yaml` configuration file and a simple `.jsonl` recording to validate the framework.
- [x] 4.2 Manually verify that interrupting the test runner cleanly tears down the spawned applications.
- [x] 4.3 Verify that isolated log files are generated correctly for a test run.
