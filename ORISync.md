---
layout: default
title: ORI Sync Tools
has_children: true
nav_order: 3
---

# Sync tools for review systems

We provide a suite of tools to help with synchronization between different review systems. We provide reference versions for xStudio and OpenRV.

See:
* **OpenRV Sync Plugin (`rvplugin/ori_sync`)**: Enables OpenRV to join sync sessions, broadcasting and receiving playhead, display, and annotation changes. See the [rvplugin README](rvplugin/ori_sync/README.md) for details.
* **xStudio Sync Plugin (`xstudio_plugin/ori_sync`)**: Enables xStudio to join sync sessions. See the [xStudio plugin README](xstudio_plugin/README.md) for installation and usage.

Full docs for the Annotation API are [here](docs/otio_sync_docs.html).

## Sync Tools

A suite of tools is provided to interact with, record, and test the sync protocol:

* **Sync Viewer (`sync_viewer`)**: A lightweight, web-based viewer that joins a sync session as a passive observer to display the live timeline state in the browser. See the [sync_viewer README](sync_viewer/README.md).
* **Sync Recorder (`sync_recorder`)**: A tool to record live sync session events into JSONL files and replay them later. Useful for debugging and creating test cases. See the [sync_recorder README](sync_recorder/README.md).
* **UI Sync Testing (`sync_test`)**: An automated end-to-end integration test framework that launches actual application binaries (xStudio, OpenRV) and uses recorded events to verify state synchronization. See the [sync_test README](sync_test/README.md).
* **Debugging & State Inspection (`debug`)**: Utilities for connecting to running review instances, configuring ports, and inspecting active viewport state. See the [debug README](debug/README.md).

## Resources & Test Media

* **Test Media (`test_media`)**: Contains generated test charts and media sequences used for testing the sync plugins and test frameworks. See the [test_media README](test_media/README.md).
* **Test Chart Generator (`testchart`)**: Scripts for generating the synthetic test media. See the [testchart README](testchart/README.md).
