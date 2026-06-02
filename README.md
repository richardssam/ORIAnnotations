# ORIAnnotations
A toolkit for sharing annotations between review systems.

See: [docs/html/introduction.html](https://richardssam.github.io/ORIAnnotations/docs/html/introduction.html)

## Requirements

Python libraries:

```
pip install opentimelineio
```

For building the docs:
```
pip install -U sphinx sphinx-mermaid
```

Creating the docs:
```
cd docs
make html
```

## Example OTIO files

   * [examples/testexport/annotationreview.otio](https://github.com/richardssam/ORIAnnotations/tree/main/examples) is an example OTIO annotation file.
   * [examples/testsession.rv](https://github.com/richardssam/ORIAnnotations/blob/main/examples/testsession.rv) is the rv-session file that was used to generate this file, and the media is in that folder.

## Sync Plugins

The toolkit includes plugins that enable real-time synchronization between different review applications using RabbitMQ and OTIO.

* **OpenRV Sync Plugin (`rvplugin/openrv_sync_plugin`)**: Enables OpenRV to join sync sessions, broadcasting and receiving playhead, display, and annotation changes. See the [rvplugin README](rvplugin/openrv_sync_plugin/README.md) for details.
* **xStudio Sync Plugin (`xstudio_plugin/ori_sync`)**: Enables xStudio to join sync sessions. See the [xStudio plugin README](xstudio_plugin/README.md) for installation and usage.
* **Legacy OpenRV Exporter/Importer (`rvplugin`)**: Contains a plugin for OpenRV to export and import annotations as custom OTIO files. Load `oriannotations.zip` via the OpenRV package manager.

## Sync Tools

A suite of tools is provided to interact with, record, and test the sync protocol:

* **Sync Viewer (`sync_viewer`)**: A lightweight, web-based viewer that joins a sync session as a passive observer to display the live timeline state in the browser. See the [sync_viewer README](sync_viewer/README.md).
* **Sync Recorder (`sync_recorder`)**: A tool to record live sync session events into JSONL files and replay them later. Useful for debugging and creating test cases. See the [sync_recorder README](sync_recorder/README.md).
* **UI Sync Testing (`sync_test`)**: An automated end-to-end integration test framework that launches actual application binaries (xStudio, OpenRV) and uses recorded events to verify state synchronization. See the [sync_test README](sync_test/README.md).
* **Debugging & State Inspection (`debug`)**: Utilities for connecting to running review instances, configuring ports, and inspecting active viewport state. See the [debug README](debug/README.md).
