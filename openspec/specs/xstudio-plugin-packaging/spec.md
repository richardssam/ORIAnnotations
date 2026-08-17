# xStudio Plugin Packaging Specification

## Purpose

Define the distributable folder tree for the xStudio ORI Sync / ORI Annotations
plugins: its layout, the payload-resolution order that lets both a packaged
distribution and a live repository checkout serve as an xStudio plugin search
folder, the vendoring policy for third-party dependencies, and the packaging
script's contract for producing and installing that tree.

## Requirements

### Requirement: The distribution is a single self-contained folder tree

The xStudio plugins SHALL be distributable as one folder tree that requires no
environment variables and no package installation into the host interpreter in order to
function.

The tree's top level SHALL be shaped like an xStudio plugin *search folder*, so that the
tree may be used either by pointing xStudio at the tree itself, or by copying the tree's
contents into a directory xStudio already scans.

The package SHALL NOT depend on which discovery route a site uses. `XSTUDIO_PYTHON_PLUGIN_PATH`,
the app bundle's local plugin directory, and the `python_plugin_search_folders` /
`user_python_plugin_search_folders` preferences SHALL all be equally valid, and the choice
SHALL remain the site's.

#### Scenario: Installed with no environment variables set

- **WHEN** the tree's contents are copied into a directory xStudio scans, with none of
  `XSTUDIO_PYTHON_PLUGIN_PATH`, `PYTHONPATH`, or `OTIO_PLUGIN_MANIFEST_PATH` set
- **AND** `pika` is not installed in the interpreter xStudio uses
- **THEN** both plugins SHALL load
- **AND** the sync plugin SHALL be able to connect to a session and exchange annotations
  with a peer

#### Scenario: Installed at a site-chosen location

- **WHEN** the tree is placed at an arbitrary filesystem location and xStudio is pointed
  at it by any of its supported discovery routes
- **THEN** both plugins SHALL load and behave identically to the app-bundle placement

### Requirement: Only plugin package directories appear at the top level

The top level of the distribution tree SHALL contain exactly the directories that are
loadable xStudio plugins, and no others. Supporting code, vendored dependencies, and
resources SHALL be either files at the top level or directories nested inside a plugin
package.

This is a hard constraint of xStudio's scanner, not a stylistic one: `load_plugins_in_path`
probes every top-level subdirectory as a plugin and has no silent failure path — a
directory that is not a plugin produces either a "not python importable" message or a
`create_plugin_instance` traceback on the console at every startup.

#### Scenario: Startup produces no plugin-load errors

- **WHEN** xStudio scans a search folder containing the distribution
- **THEN** no `Failed to load plugin` or `not python importable` message SHALL be emitted
  for any entry belonging to the distribution

#### Scenario: Packaging rejects a stray top-level directory

- **WHEN** the packaging step stages a tree whose top level contains a directory that is
  not a loadable plugin
- **THEN** packaging SHALL fail rather than produce an artifact that will log errors at
  every xStudio startup

### Requirement: Payload resolution supports distribution and live development

The plugins SHALL locate their supporting payload — `otio_sync_core`, `ORIAnnotations`,
the `SyncEvent` schemadef manifest, and vendored third-party packages — through one shared
resolver used by both plugins.

The resolver SHALL check candidate roots in a defined order and SHALL make every root it
finds importable. The order SHALL place the packaged payload ahead of the repository
layout.

The resolver SHALL support a repository checkout being used directly as a search folder,
resolving to the repository's own `python/` and `otio_event_plugin/` directories. Producing
a package SHALL NOT be a precondition for developing or testing the plugins.

The resolver SHALL be loadable without depending on any particular directory already being
on `sys.path`, since the plugins are imported both by xStudio (which places the search
folder on `sys.path`) and by tests (which may place a plugin directory there instead).

The resolver SHALL record which roots it selected, so that a payload shadowing the wrong
location is diagnosable from the log rather than by inspection.

#### Scenario: Repository checkout used directly

- **WHEN** a repository checkout's plugin directory is used as an xStudio search folder,
  with no packaged payload present
- **THEN** the resolver SHALL resolve the payload to the repository's `python/` and
  `otio_event_plugin/` directories
- **AND** both plugins SHALL behave as they did before this change

#### Scenario: Packaged payload takes precedence

- **WHEN** the tree contains a packaged payload
- **THEN** the resolver SHALL use it in preference to any repository layout
- **AND** SHALL log the roots it selected

#### Scenario: Resolver reached from a test harness

- **WHEN** a test places a single plugin directory on `sys.path` and imports a plugin
  module directly
- **THEN** the resolver SHALL still be locatable and SHALL resolve the payload

### Requirement: The SyncEvent schemadef registers through the OTIO API

The plugins SHALL register the `SyncEvent` schemadef by extending OTIO's active manifest
directly, and SHALL NOT rely on mutating `OTIO_PLUGIN_MANIFEST_PATH`.

Registration SHALL be idempotent: it SHALL check whether the schemadef is already
registered and SHALL NOT register it a second time. A duplicate registration loads the
schemadef module twice, after which `isinstance` checks against its types silently return
`False` and annotations disappear without an error.

Registration SHALL succeed regardless of whether OTIO's active manifest has already been
built by the time a plugin loads.

The schemadef manifest and its referenced Python files SHALL remain real files on disk,
because OTIO resolves a manifest's schemadef paths relative to the manifest's own
location.

#### Scenario: Both plugins load in either order

- **WHEN** both plugins are loaded, in either order, each invoking registration
- **THEN** `SyncEvent` SHALL be registered exactly once
- **AND** `isinstance` checks against `SyncEvent` types SHALL hold for objects created by
  either plugin

#### Scenario: OTIO manifest already built

- **WHEN** something has already caused OTIO to build its active manifest before the
  plugins load
- **THEN** registration SHALL still make `SyncEvent` available

#### Scenario: Environment variable also set

- **WHEN** a site additionally sets `OTIO_PLUGIN_MANIFEST_PATH` to the packaged manifest
- **THEN** `SyncEvent` SHALL still be registered exactly once

### Requirement: Vendoring policy

Pure-Python dependencies that xStudio does not provide SHALL be vendored into the package.
`pika` is such a dependency and SHALL be vendored.

`opentimelineio` SHALL NOT be vendored. It is a compiled extension, and xStudio bundles a
working version; shipping a second copy risks displacing it with a version carrying known
defects.

The packaging step SHALL collect the project's own Python packages by enumerating their
contents at package time rather than from a hand-maintained module list. A hand-maintained
list that falls behind produces a plugin that fails to import and is silently disabled
rather than one that fails loudly.

#### Scenario: pika absent from the host interpreter

- **WHEN** the interpreter xStudio uses has no `pika` installed
- **THEN** the sync plugin SHALL still connect to a broker using the vendored copy

#### Scenario: A module is added to the core library

- **WHEN** a new module is added to the project's core Python package and the package is
  rebuilt
- **THEN** the new module SHALL be present in the artifact without any packaging file
  being edited

### Requirement: Install documentation describes the folder, not a recipe

Each plugin's README SHALL describe installation as placing one folder, and SHALL NOT
instruct a reader to set `PYTHONPATH` or `OTIO_PLUGIN_MANIFEST_PATH`, or to install
packages into the interpreter xStudio uses.

The READMEs SHALL NOT describe a patched or branch-specific xStudio build. The xStudio
changes the plugins depend on are upstream. Where a minimum xStudio version matters, the
README SHALL state that floor rather than a procedure for reproducing it.

Documented limitations SHALL correspond to the current code. A limitation that the code no
longer has SHALL be removed rather than left standing, because a reader cannot distinguish
a stale caveat from a live one and will design around both.

#### Scenario: A reader follows the README with no prior setup

- **WHEN** a reader with a stock xStudio build follows the installation section
- **THEN** the steps SHALL be sufficient for both plugins to load and for the sync plugin
  to join a session
- **AND** no step SHALL involve building or patching xStudio

#### Scenario: A documented limitation is fixed

- **WHEN** a change removes a limitation the README documents
- **THEN** that change SHALL also remove the README entry

#### Scenario: Environment variables that remain

- **WHEN** the README documents an environment variable
- **THEN** it SHALL be one that is genuinely optional, such as log-file destinations
- **AND** the README SHALL state that no variable is required for the plugins to function
