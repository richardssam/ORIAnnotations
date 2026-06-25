# unified-doc-generator

## Purpose

Generate a single, self-contained HTML documentation page that unifies the OTIO
SyncEvent schemas and the protocol message (transport-layer) reference, driven by
one YAML configuration file.

## Requirements

### Requirement: Unified configuration file
The system SHALL accept a single YAML configuration file with three top-level sections: `meta`, `otio_events`, and `protocol_messages`. The `meta` section SHALL contain at minimum `otio_input` (path to the OTIO schema source file). The `meta` section MAY contain `introduction` (path to a Markdown file) and `output` (default output HTML path). The `otio_events` and `protocol_messages` sections SHALL use class name keys with `_category` and named example sub-keys, matching the format of the existing per-source configs.

#### Scenario: Config loaded from unified file
- **WHEN** the generator is invoked with `--config config.yml`
- **THEN** it SHALL parse `meta`, `otio_events`, and `protocol_messages` sections from that single file without requiring any other config file

#### Scenario: OTIO input path resolved from meta
- **WHEN** `meta.otio_input` is set to a path
- **THEN** the generator SHALL use that path as the OTIO schema source file, and the `--input` CLI flag SHALL NOT be required

#### Scenario: Output path falls back to meta
- **WHEN** `meta.output` is set and `--output` is not passed on the CLI
- **THEN** the generator SHALL write the HTML to the path specified in `meta.output`

### Requirement: Single HTML output with layered sidebar
The generator SHALL produce a single self-contained HTML file. The sidebar SHALL contain two labelled sections — one for OTIO SyncEvents grouped by their configured categories, one for Protocol Messages grouped by their configured categories — in that order.

#### Scenario: Sidebar shows both layers
- **WHEN** the HTML is rendered
- **THEN** the sidebar SHALL list OTIO SyncEvent entries first, under a section heading, followed by Protocol Message entries under a separate section heading

#### Scenario: Categories within each layer remain grouped
- **WHEN** multiple events share a `_category` within the same layer
- **THEN** the sidebar SHALL group those entries under that category within their layer section

### Requirement: Markdown introduction section
When `meta.introduction` is set, the generator SHALL render the referenced Markdown file as HTML using `mistune` and inject it as the first content section of the document, replacing the default overview paragraph.

#### Scenario: Introduction rendered from Markdown file
- **WHEN** `meta.introduction` points to a valid `.md` file
- **THEN** the first content section SHALL contain HTML rendered from that file's Markdown content

#### Scenario: Default overview used when introduction absent
- **WHEN** `meta.introduction` is not set
- **THEN** the generator SHALL render a default overview paragraph in place of the introduction section

### Requirement: Python instantiation examples for protocol messages
For each protocol message, the generator SHALL produce a Python example tab showing how to instantiate the message class. The example SHALL begin with a comment identifying it as a Protocol Message transport-layer object. The example SHALL be formatted with `black` when available, falling back to unformatted code.

#### Scenario: Python tab shown for protocol message
- **WHEN** a protocol message has example parameters in the config
- **THEN** the rendered HTML SHALL include a Python tab alongside the JSON tab, containing a `from otio_sync_core.protocol_messages import <ClassName>` import and a `msg = <ClassName>(...)` instantiation

#### Scenario: Layer comment present in Python example
- **WHEN** the Python example for a protocol message is rendered
- **THEN** the first line SHALL be `# Protocol Message (transport layer)`

#### Scenario: OTIO Python examples retain their existing comment
- **WHEN** the Python example for an OTIO SyncEvent is rendered
- **THEN** the first line SHALL be `# OTIO SyncEvent (serialized as OpenTimelineIO object)`
