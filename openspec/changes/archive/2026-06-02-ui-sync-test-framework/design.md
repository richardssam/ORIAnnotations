## Context

Currently, testing the synchronization between XStudio and OpenRV relies on manual verification or testing the `sync_manager` in isolation. This does not verify if the underlying applications actually reached the expected UI state (e.g., did XStudio actually move to frame 10?). We have a `sync_recorder` tool that can record and replay sync events to `.jsonl` files. We need an automated integration testing framework built on top of this that LLMs and CI systems can use to verify UI sync changes safely.

## Goals / Non-Goals

**Goals:**
- Provide a CLI-based automated test runner for AI agents and CI.
- Support YAML configuration for test suites (mapping tests to `.jsonl` recordings and specifying which apps to launch).
- Verify the *true* logical state of the applications by querying them directly, bypassing the sync manager.
- Isolate application logs for easy debugging upon failure.

**Non-Goals:**
- Pixel-perfect visual regression testing (image diffing). We are verifying logical UI state (playhead, loaded clip, annotations).
- Creating a GUI for the test runner itself. It is designed to be headless and scriptable.

## Decisions

**1. Architecture: Orchestrator + RPC**
- *Decision:* The test framework will run as a standalone Python CLI that spawns target applications (XStudio/OpenRV) as subprocesses. It will inject a lightweight Inspection Server (RPC socket) into their Python environments.
- *Rationale:* This decouples the test orchestrator from the applications. It avoids complex C++ IPC and leverages the existing Python APIs in both XStudio and OpenRV to gather logical state.
- *Alternative:* Compiling test hooks directly into the C++ applications. Rejected because it's intrusive and harder to maintain across both disparate codebases.

**2. Test Configuration: YAML**
- *Decision:* Use a `sync_tests.yaml` file to define test topologies (e.g., test XStudio vs OpenRV, or XStudio vs XStudio).
- *Rationale:* Allows easy expansion of test scenarios without modifying Python code. Makes it simple to filter and run specific tests via the CLI (e.g., `--test basic_scrub`).

**3. State Verification: Logical Object Diffing**
- *Decision:* The RPC `GET_STATE` call will return a standard JSON object containing playhead, clip, and annotation data. The orchestrator will assert equality between these JSON objects.
- *Rationale:* Robust against minor rendering differences, rendering engine initialization times, and OS-level windowing quirks that cause pixel tests to flake.

## Risks / Trade-offs

- **[Risk] Zombie Processes:** The test runner crashes and leaves XStudio or OpenRV running in the background.
  - *Mitigation:* Implement robust `subprocess` management using Python context managers or `atexit` handlers to aggressively clean up spawned processes upon exit.
- **[Risk] Port Collisions:** Running multiple instances of apps on hardcoded RPC ports might collide during parallel testing.
  - *Mitigation:* Start by assigning unique, static ports per app in the YAML config. In the future, dynamic port allocation via OS-assigned ports could be used.
