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
The system SHALL inject a lightweight RPC server into launched applications that exposes a `GET_STATE` endpoint to return the true logical state (frame, clip, annotations).

#### Scenario: Querying application state
- **WHEN** the runner requests state from a launched application
- **THEN** the application returns a JSON payload containing its actual playhead, clip, and annotation state

### Requirement: Isolated Application Logging
The system SHALL redirect stdout and stderr of each spawned application into isolated log files.

#### Scenario: Debugging a failed test
- **WHEN** a test fails due to a state mismatch
- **THEN** the runner outputs the failure diff and the location of the isolated application log file for the LLM or developer to review
