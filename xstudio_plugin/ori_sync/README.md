---
layout: default
title: XStudio ORI Sync Plugin
parent: ORI Sync Tools
nav_order: 3.2
---
# ORI Sync Review — xStudio Plugin

Joins an ORI Sync session from xStudio, providing:

- Bidirectional playback sync (position, play/stop, loop state)
- Live annotation broadcast: strokes drawn in xStudio are sent to all peers as OTIO `insert_child` patches on pen-up
- Annotation receive: incoming annotation clips are injected back into xStudio's AnnotationsUI
- Master election and full state snapshot for late-joining peers

The plugin uses `SyncManager` and `RabbitMQNetwork` from `python/otio_sync_core/` — the same core library as the OpenRV plugin.

---

## Requirements

- An xStudio build from `develop` — the plugin's dependencies (owner-actor event routing, viewport scale/pan atoms, live-stroke annotation events) are upstream but not yet in a tagged release
- A running RabbitMQ broker accessible on the network (default: `localhost:5672`)

Nothing else. `opentimelineio` is used from xStudio's own bundled copy; `pika` ships vendored inside the plugin distribution, so no `pip install` into xStudio's interpreter is needed.

---

## Installation

Point xStudio at the plugin folder — no environment variables, and no packages to install into xStudio's interpreter.

The packaging script at the repo root (`./make_xstudio_package.sh --help` for the full list of modes) produces a self-contained tree containing both `ori_sync/` and `ori_annotations/` plus their supporting payload (`otio_sync_core`, `ORIAnnotations`, the `SyncEvent` schemadef, vendored `pika`). It has no default action — running it with no arguments just shows help, since it has real side effects (a `pip install` to vendor `pika` on first use, file writes). Pick whichever install mode suits your site:

### Suggested default: install into the app bundle

xStudio scans its own `Contents/Resources/plugin-python/` directory unconditionally, so this needs no further configuration at all:

```bash
./make_xstudio_package.sh --install-app /path/to/xSTUDIO.app
```

Re-run after rebuilding xStudio — `cmake --build --target install` repopulates that directory.

### Alternative: a persistent folder, independent of any one app bundle

If you maintain one plugin location shared across multiple xStudio builds or versions rather than tying it to a single app bundle, install into a folder of your own choosing instead — it's created if it doesn't exist yet, and safe to re-run after every plugin change:

```bash
./make_xstudio_package.sh --install-dir /path/to/a/persistent/folder
```

Then point xStudio at it either way:

```bash
export XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/a/persistent/folder
```

or via Preferences → Python → **"Python Plugins Search Paths"** — add the path in the settings UI. Persists across restarts; no environment variable needed.

### Alternative: a versioned artifact to hand someone

```bash
./make_xstudio_package.sh --package
```

Stages the same tree and zips it into `dist/ori-sync-xstudio-<version>.zip` — useful for distributing a specific build rather than installing directly from this checkout.

### Developing against a checkout

A repository checkout's `xstudio_plugin/` directory works directly as a search folder — the plugin resolves its payload from `python/` and `otio_event_plugin/` in that case, so no packaging step is needed while iterating:

```bash
export XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/ORIAnnotations/xstudio_plugin
```

### Optional: enable file logging

```bash
export ORI_SYNC_LOG_FILE=/tmp/ori_sync.log
xstudio
```

---

## Session connection

Connection settings are exposed as xStudio preferences under the `ori_sync_conn` attribute group and can also be changed at runtime from QML:

| Preference | Default | Description |
|---|---|---|
| MQ Host | `localhost` | RabbitMQ broker hostname or IP |
| MQ Port | `5672` | RabbitMQ AMQP port (use `5671` for TLS) |
| Session ID | `otio-sync-demo` | Logical session name; scopes which peers see each other. Must match across all participants. |

Call `plugin.connect_to_session()` from QML or Python to start the session. The plugin broadcasts `session.who_is_master` and waits up to 2 seconds for a response. On the **Create** path, no response means this peer starts the session as master. On the **Join** path, no response means the named session doesn't exist — the connect fails with a "Session Not Found" error instead of silently starting an empty one under that name.

The Create/Join dialogs also show the resolved **account** identity (read-only — the underlying OS/account login, not the editable display name below it) so you can confirm which account you're about to connect as before joining a shared session.

### Site config file

A JSON file named `ori_site_config.json`, dropped into this plugin's installed folder (alongside `ori_sync_plugin.py`), pre-defines the MQ Host default shown in the connect dialogs:

```json
{
  "host": "mq.example.com",
  "port": 5672
}
```

Precedence: a value typed into the dialog > the `ORI_RMQ_HOST` environment variable > this file > the hardcoded `localhost` fallback. A missing or malformed file is silently ignored — nothing breaks if it's absent, which is the common case.

---

## Environment variables

None are required. The only ones the plugin reads are optional debug logging switches:

| Variable | Description |
|---|---|
| `ORI_SYNC_LOG_FILE` | Absolute path for the plugin log file. If unset, no file logging occurs. Useful for debugging annotation event schemas and network messages. |

`XSTUDIO_PYTHON_PLUGIN_PATH` is not read by the plugin itself — it is one of several ways xStudio can be pointed at the plugin folder, listed under Installation above.

---

## Logging

Set `ORI_SYNC_LOG_FILE` to enable file logging:

```bash
export ORI_SYNC_LOG_FILE=/tmp/ori_sync.log
tail -f /tmp/ori_sync.log
```

All network send/receive, annotation events, and session state transitions are logged at `DEBUG` level.

### Diagnosing the annotation event schema

Annotation events reach the plugin through `subscribe_to_annotation_draw_events`, which joins AnnotationsCore's draw-events group and hands every event to `_on_annotation_draw_event(event_data, user_id, stroke_completed)` with the `JsonStore` already decoded. Two kinds arrive on that one callback, told apart by whether `stroke_completed` is present:

- **draw interactions** (`stroke_completed is None`) — `{"event": "PaintStart" | "PaintPoint" | "PaintEnd" | "PaintClear" | "HideDrawings" | ..., "payload": {...}}`
- **live strokes** — the serialised annotation, `{"Annotation Serialiser Version": N, "Data": {"pen_strokes": [...]}, "user_id": ..., "stroke_completed": bool}`

To inspect the exact shapes for your xStudio version, raise the raw-event log cap in `AnnotationSyncController.on_draw_event` — it already logs the first three events of a session with their top-level keys:

```python
if self._core_events_received <= 3:   # raise this while investigating
```

Do **not** subscribe to the AnnotationsUI or AnnotationsCore *plugin events* groups to get at this. `PluginBase` spawns those without an owner and nothing is ever broadcast on them, so the subscription silently delivers nothing — the bug that kept this whole path dead until `fix-xs-annotation-draw-subscription`. See [docs/xstudio_constraints.md](../../docs/xstudio_constraints.md) for the full account.

---

## Interoperability

The plugin uses the same wire protocol as the OpenRV plugin (`rvplugin/ori_sync/plugin.py`). Any mix of xStudio and RV peers can join the same session as long as they share the same `Session ID`, `MQ Host`, and `MQ Port`.

Annotations broadcast from xStudio are stored in the shared OTIO timeline as `insert_child` patches and are readable by the `sync_viewer` debug viewer and the OTIO export pipeline.
