## Why

We currently lack automated end-to-end testing for UI synchronization between applications like XStudio and OpenRV. Testing relies heavily on manual verification or testing just the sync managers in isolation, which doesn't guarantee the underlying applications actually reached the expected state. We need a framework that can automatically launch applications, replay recorded network sessions, and directly query the applications' internal states to catch synchronization regressions.

## What Changes

- Implement an automated CLI test runner designed for AI agents and CI environments.
- Implement support for test suite configuration via YAML (e.g., `sync_tests.yaml`) to define tests mapping to `.jsonl` recordings and the apps to launch.
- Implement a lightweight Inspection Server (RPC) that gets injected into the launched applications (XStudio/OpenRV) to expose their true internal state (`GET_STATE`).
- Implement robust process management for launching, configuring, and tearing down XStudio and OpenRV.
- Ensure isolated logging by piping each application's output into test-specific log files for easy debugging.

## Capabilities

### New Capabilities
- `ui-sync-testing`: Automated UI state synchronization testing harness and test suite execution.

### Modified Capabilities
None.

## Impact

- Adds new Python test infrastructure (`sync_test` package).
- Integrates small Inspection Server hooks into the Python environments of XStudio and OpenRV during test runs.
- Does not modify the core `sync_recorder` or `sync_manager` functionality, but relies heavily on the output from `sync_recorder`.
