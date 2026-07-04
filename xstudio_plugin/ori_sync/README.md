---
layout: default
title: XStudio ORI Sync Plugin
parent: ORI Sync Tools
nav_order: 3.6
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

- xStudio with Python plugin support, **built from a patched checkout** — see TODO below
- A running RabbitMQ broker accessible on the network (default: `localhost:5672`)
- `opentimelineio` and `pika` importable in the Python environment xStudio uses

```bash
pip install opentimelineio pika
```

---

## xStudio build + required patches

This plugin depends on xStudio C++/Python changes that are not yet upstream.
They are curated on the local **`xstudio_sync_fixes`** branch in
`/Users/sam/git/xstudio`, sitting **directly on top of upstream `develop`**
(fork point `eb3d235e`, "Merge pull request #294"). `xstudio_sync_fixes` is
exactly `develop` + the five commits below — nothing else.

### Required patches (rebuild recipe)

To rebuild `xstudio_sync_fixes`, apply these on top of `develop`. Four are
functionally required by the plugin; one is a local build convenience.

| Commit | Summary | Why it's needed | Cleaned-up branch / upstream status |
|---|---|---|---|
| `cc071f56` | `feat(python): expose viewport scale and pan atoms` | Pan/zoom sync — `display_sync.py` reads and writes `Viewport.scale` / `Viewport.pan`. | Being reworked to tedwaine's `V2f` naming under OpenSpec change `expose-viewport-scale-pan`. Branch `pr/expose-viewport-scale-pan` holds the older `Vec2f` version — **do not push that one**. |
| `a6893b38` | `Fix python event routing sender mismatch` | Event-group owner-actor routing. Without it `subscribe_to_event_group` silently drops timeline `change_atom` and current-selection events, so structure/selection sync breaks. **Verified required**: reverting the 3-arg call fails the `delete_media_xstudio` sync test. | Cleaner combined rebuild is `779c1857` on `pr/python-event-routing`. That branch was PR #270 (**closed** — rejected on its "playhead" framing); needs re-submitting reframed around generic event-group routing. |
| `8c978aa5` | `fix(python): purge stale actor callbacks on broadcast_down` | Prevents a SIGSEGV on timeline switch when a subscribed owner actor is torn down mid-dispatch. Pairs with the routing fix above. | Folded into `779c1857` (same `pr/python-event-routing` branch). |
| `7e9b44d1` | `feat(annotations): broadcast live-stroke geometry to plugin_events_group` | The 5-tuple live-stroke geometry broadcast the partial-annotation sync consumes (`xstudio-partial-annotations` change). | Intended for a `pr/annotation-stroke-events` branch (not yet raised). |
| `a099681e` | `build: upgrade FFmpeg vcpkg override to 8.0.1#2` | **Local build convenience only — NOT functionally required.** Omit for a clean upstream-tracking build. | Not for upstream. |

Note: the original event-routing work also patched `plugin_base.py` for playhead
events; that part was **dropped**. The plugin instead uses the maintainer-recommended
`subscribe_to_playhead_events()` + `playhead_attribute_changed` path. Only the
generic event-group owner-actor routing (`module.py` + `py_context.cpp`) survives
and is required.

### Build (macOS, as used here)

```bash
cd /Users/sam/git/xstudio
git checkout xstudio_sync_fixes
cmake -B build --preset MacOSNinjaReleaseLocal
cmake --build build --target install
```

This produces `build/xSTUDIO.app`. The Python API (`module.py`, `viewport.py`, …)
and the compiled `python_module` are packaged inside the bundle at
`build/xSTUDIO.app/Contents/Frameworks/lib/python3.11/site-packages/xstudio/`.
Point a running xstudio (or the `sync_test` harness) at `build/xSTUDIO.app`.

> Pure-Python binding tweaks (e.g. `module.py`) can be tested by editing the
> installed copy inside the app bundle — no rebuild needed. C++ changes
> (`py_context.cpp`, `py_atoms.cpp`, `py_register.cpp`, …) require a rebuild.

---

## Installation

No build step. Point xStudio at the plugin directory with environment variables.

### Required

```bash
# The parent directory containing both ori_annotations/ and ori_sync/
export XSTUDIO_PYTHON_PLUGIN_PATH=/path/to/ORIAnnotations/xstudio_plugin
```

### Recommended (set explicitly to avoid ordering issues)

```bash
# ORIAnnotations Python library (otio_sync_core, ORIAnnotations)
export PYTHONPATH=/path/to/ORIAnnotations/python:$PYTHONPATH

# SyncEvent OTIO schemadef (PaintStart, PaintPoints, TextAnnotation, etc.)
export OTIO_PLUGIN_MANIFEST_PATH=/path/to/ORIAnnotations/otio_event_plugin/plugin_manifest.json
```

> The plugin extends `sys.path` and `OTIO_PLUGIN_MANIFEST_PATH` automatically at load time, so only `XSTUDIO_PYTHON_PLUGIN_PATH` is strictly required if `PYTHONPATH` already covers the repo. Setting all three explicitly avoids any load-order surprises.

### Full example (bash)

```bash
export REPO=/path/to/ORIAnnotations

export XSTUDIO_PYTHON_PLUGIN_PATH=$REPO/xstudio_plugin
export PYTHONPATH=$REPO/python:$PYTHONPATH
export OTIO_PLUGIN_MANIFEST_PATH=$REPO/otio_event_plugin/plugin_manifest.json

# Optional: enable file logging (see Logging section below)
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

Call `plugin.connect_to_session()` from QML or Python to start the session. The plugin broadcasts `session.who_is_master`, waits up to 2 seconds for a response, then self-elects as master if none arrives.

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `XSTUDIO_PYTHON_PLUGIN_PATH` | Yes | Directory containing `ori_sync/` (and `ori_annotations/`). xStudio scans this for plugin packages. |
| `PYTHONPATH` | Recommended | Should include `$REPO/python` so that `otio_sync_core` and `ORIAnnotations` are importable. The plugin inserts this path automatically at load time if not already present. |
| `OTIO_PLUGIN_MANIFEST_PATH` | Recommended | Path to `$REPO/otio_event_plugin/plugin_manifest.json`. Registers the `SyncEvent` schemadef (`PaintStart.1`, `PaintPoints.1`, `TextAnnotation.1`, etc.) with OTIO. The plugin extends this variable at load time if not already set. |
| `ORI_SYNC_LOG_FILE` | No | Absolute path for the plugin log file. If unset, no file logging occurs. Useful for debugging annotation event schemas and network messages. |

---

## Logging

Set `ORI_SYNC_LOG_FILE` to enable file logging:

```bash
export ORI_SYNC_LOG_FILE=/tmp/ori_sync.log
tail -f /tmp/ori_sync.log
```

All network send/receive, annotation events, and session state transitions are logged at `DEBUG` level.

### Diagnosing the annotation event schema

xStudio's AnnotationsUI delivers annotation events as an opaque `JsonStore`. The exact field names for pen lifecycle events (`pen_down`, `pen_up`, etc.) and the stroke data layout depend on the xStudio version. To inspect them, temporarily add a print to `_on_annotation_event` in [ori_sync_plugin.py](ori_sync_plugin.py):

```python
def _on_annotation_event(self, data):
    if len(data) == 3 and isinstance(data[0], event_atom) and isinstance(data[1], annotation_atom):
        import json
        print("ANNOTATION EVENT:", json.loads(data[2].dump()))
```

The plugin currently assumes `event == "pen_down"` and `event == "pen_up"` as the stroke lifecycle markers, and `ann["base"]["annotation"]["Data"]["pen_strokes"]` as the stroke payload — the same schema used by the `ori_annotations` export plugin. Adjust the field names in `_on_annotation_event` and `_do_broadcast_annotation` if your xStudio version differs.

---

## Interoperability

The plugin uses the same wire protocol as the OpenRV plugin (`rvplugin/ori_sync/plugin.py`). Any mix of xStudio and RV peers can join the same session as long as they share the same `Session ID`, `MQ Host`, and `MQ Port`.

Annotations broadcast from xStudio are stored in the shared OTIO timeline as `insert_child` patches and are readable by the `sync_viewer` debug viewer and the OTIO export pipeline.

---

## Known limitations

- **Remote annotation rendering**: The `_apply_remote_annotation` method sends a `"draw_remote"` event to xStudio's AnnotationsUI. The exact event name and JsonStore schema required by AnnotationsUI to render an incoming stroke is not fully documented; this may need adjustment for the installed xStudio version.
- **Aspect ratio for coordinate conversion**: The stroke coordinate transform uses a hardcoded `aspect_half = 0.8889` (equivalent to 16:9). For other aspect ratios the strokes will be slightly scaled. A future version should read the actual media resolution from the clip.
- **No TLS support** in the current `RabbitMQNetwork` backend. For the AWS-hosted broker used by the ASWF demo (`amqps://...`), replace `RabbitMQNetwork` with the `pika_in`/`pika_out` modules from `ori-sync-plugin` which support TLS via `pika.URLParameters`.
