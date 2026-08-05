## MODIFIED Requirements

### Requirement: Automated CLI Test Runner
The system SHALL provide a command-line test runner (`sync_test`) that can launch applications, replay recorded `.jsonl` sessions, and verify application state.

#### Scenario: Running a successful test suite
- **WHEN** the runner is executed with a valid `sync_tests.yaml` config
- **THEN** it executes all tests, verifies state assertions successfully, and exits with a 0 status code

#### Scenario: Running-test banner shows suite progress
- **WHEN** a test is launched as part of a multi-test run (`run_all`)
- **THEN** its running-test banner SHALL show its position in the suite (e.g. `[3/10]`)
- **AND** a single-test `--test <name>` invocation SHALL NOT show a position, since there is no suite position to report

#### Scenario: Every run is recorded to history
- **WHEN** the runner completes a test, whether it passes or fails
- **THEN** it SHALL append one entry to a persistent, append-only run-history log containing at minimum: test name, timestamp, source git commit, pass/fail result, failure kind (if failed), whether the result required a retry to converge, how much of the allotted convergence time was actually used, the recording file the test read from (or an explicit absence marker for a test with no recording), and the test's total wall-clock duration

#### Scenario: Run summary shows per-test duration and total suite time
- **WHEN** the runner prints its end-of-suite summary
- **THEN** each test's line SHALL show its wall-clock duration
- **AND** the summary SHALL report the total elapsed time for the whole run

#### Scenario: Run summary shows prior results
- **WHEN** the runner prints its end-of-suite summary for a test that has prior entries in the run-history log
- **THEN** it SHALL show the immediately-previous recorded result and a compact trend of up to the last 5 recorded results, sourced from history as it stood before this run started
- **AND** a test with no prior history entries SHALL be shown as having no prior runs, not a fabricated or blank result

#### Scenario: A known_broken test failing as expected does not fail the suite
- **WHEN** a test with `status: known_broken` fails
- **THEN** the runner SHALL record the failure in the run-history log and report it distinctly in the run summary
- **AND** SHALL NOT count it toward the suite's overall pass/fail exit code

#### Scenario: A known_broken test unexpectedly passes
- **WHEN** a test with `status: known_broken` passes
- **THEN** the runner SHALL flag it distinctly in the run summary (not silently reported as an ordinary pass), so the declared `blocked_by` change can be checked for whether it is safe to reclassify the test as `stable`

#### Scenario: Run summary reports why, not just whether
- **WHEN** the runner prints its end-of-suite summary
- **THEN** each non-passing or specially-flagged test SHALL show its failure kind (or "converged late" marker), not only a bare pass/fail indicator

### Requirement: Test Suite Configuration
The system SHALL support configuring test suites via a YAML file defining test names, associated `.jsonl` recordings, and the applications to launch.

#### Scenario: Running a specific test
- **WHEN** the runner is executed with the `--test <name>` argument
- **THEN** it only executes the specific test defined in the YAML configuration

#### Scenario: A test declares its intent
- **WHEN** a test entry is defined in `sync_tests.yaml`, the canonical suite definition
- **THEN** it SHALL include a `description` field explaining, in human-readable terms, what scenario the test exercises and why it exists

#### Scenario: A test entry without a description fails config validation
- **WHEN** the runner loads `sync_tests.yaml` and it contains a test entry with no `description` field
- **THEN** it SHALL report a configuration error identifying the offending test and SHALL NOT run that suite

#### Scenario: A test is declared known_broken
- **WHEN** a test entry in `sync_tests.yaml` sets `status: known_broken`
- **THEN** it SHALL also declare `blocked_by`, naming the OpenSpec change expected to resolve the underlying issue
- **AND** the runner SHALL still execute the test (per the known_broken run/report/exit-code behavior defined under Automated CLI Test Runner)

#### Scenario: A stable test needs no status field
- **WHEN** a test entry omits `status`
- **THEN** the runner SHALL treat it as `status: stable`, requiring an ordinary pass to avoid failing the suite

#### Scenario: Subset config files are not required to carry their own description
- **WHEN** the runner loads a test suite configuration other than `sync_tests.yaml` (e.g. `sync_tests_xstudio.yaml`, `sync_demos.yaml`) whose entries duplicate a subset of `sync_tests.yaml`'s tests
- **THEN** it SHALL NOT require a `description`, `status`, or `blocked_by` field on those entries, since `sync_tests.yaml` is the authoritative source for a given test's intent
