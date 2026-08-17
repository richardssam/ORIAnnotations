## MODIFIED Requirements

### Requirement: Plugin Installation

The plugin SHALL be loadable by xstudio from any directory xstudio scans for Python
plugins, with no modifications to the xstudio installation and without any environment
variable being set beyond whichever one the site chooses to use for discovery.

The plugin SHALL NOT require `PYTHONPATH` or `OTIO_PLUGIN_MANIFEST_PATH` to be set, and
SHALL NOT require any package to be installed into the interpreter xstudio uses. It SHALL
obtain `ORIAnnotations`, `otio_sync_core`, and the `SyncEvent` schemadef through the
shared payload resolver.

A repository checkout's `xstudio_plugin/` directory SHALL remain valid as a search folder,
resolving its payload from the repository.

#### Scenario: Plugin discovered via env var

- **WHEN** xstudio starts with `XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/ORIAnnotations/xstudio_plugin`
- **THEN** the `ori_annotations` plugin SHALL be loaded and its menu entries SHALL appear in the xstudio UI

#### Scenario: Plugin discovered without any environment variable

- **WHEN** the distribution's contents are placed in a directory xstudio already scans,
  and none of `XSTUDIO_PYTHON_PLUGIN_PATH`, `PYTHONPATH`, or `OTIO_PLUGIN_MANIFEST_PATH`
  is set
- **THEN** the `ori_annotations` plugin SHALL be loaded and its menu entries SHALL appear
  in the xstudio UI
- **AND** exporting a playlist's annotations SHALL produce the same `.otio` output as an
  environment-variable-configured installation

#### Scenario: Plugin not present

- **WHEN** the plugin is not present in any directory xstudio scans
- **THEN** the plugin SHALL not load and xstudio SHALL start normally without error
