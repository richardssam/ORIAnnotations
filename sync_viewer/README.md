---
layout: default
title: ORI Sync Viewer
parent: ORI Sync Tools
nav_order: 3.4
---

# sync_viewer

A lightweight, web-based debug viewer for the OTIO Sync protocol. It joins a sync session as a **passive observer** — it receives all state changes but never acts as master or sends events.

## Overview

The viewer runs a FastAPI + WebSocket backend (`server.py`) that connects to RabbitMQ and streams live session state to a browser UI. It is useful for:

- Watching the timeline state as a session progresses
- Verifying playback and display sync between hosts
- Inspecting clip and track metadata in real time

It does **not** require an RV or xStudio plugin to be installed.

## Requirements

```
pip install -r requirements.txt
```

Dependencies: `fastapi`, `uvicorn[standard]`, `opentimelineio`.

`server.py` also imports `otio_sync_core` from the parent `python/` directory. When run from the `sync_viewer/` directory this is handled automatically via a `sys.path` insert.

RabbitMQ must be reachable (default: `localhost:5672`).

## Usage

```bash
cd sync_viewer
python server.py [options]
```

Open `http://localhost:8765` in a browser.

### Options

| Flag | Default | Description |
|---|---|---|
| `--host HOST` | `localhost` | Web server bind address |
| `--port PORT` | `8765` | Web server port |
| `--rmq-host HOST` | `localhost` | RabbitMQ host |
| `--rmq-port PORT` | `5672` | RabbitMQ port |
| `--session SESSION` | `otio-sync-demo` | Session ID to join |

## UI

The browser UI has three panels and two status bars:

**Left sidebar** — lists all timelines in the session. Click to switch which timeline is displayed.

**Centre — timeline viewer** — shows all tracks (Video, Audio, Annotations) as coloured clip blocks. Gaps are rendered in grey and are not selectable. Annotation clips appear as 1-frame-wide amber marks; zoom in to inspect them.

- The red **playhead** line shows the master's current playback position.
- The green **glow** highlights the clip currently under the playhead.
- **Follow mode** (top-right of zoom bar) auto-scrolls to keep the playhead in view. Click to detach.
- **Zoom**: `+` / `−` buttons, `Fit` to reset, or `Ctrl+scroll` with the mouse wheel.

**Right — Inspector** — click any clip to load its full OTIO JSON with syntax highlighting.

**Playback bar** (bottom) — shows playing/stopped state, current time, frame, FPS, and loop flag.

**Display bar** — shows the master's viewport state: pan, zoom, exposure, and active channel (R/G/B/A/RGBA).

**Top bar** — shows the session status badge (`SYNCED`, `JOINING`, `DISCOVERING`), the master peer GUID, and this viewer's own GUID. A `★ master` indicator appears if this viewer somehow becomes master (unexpected in normal use).

## REST API

The server also exposes a few debug endpoints:

| Endpoint | Description |
|---|---|
| `GET /api/state` | Full session state snapshot as JSON |
| `GET /api/debug` | Raw OTIO timeline structure dump; useful for diagnosing missing clips or bad durations |
| `GET /api/detail/{guid}` | Full OTIO JSON for any object by GUID |

## Notes

- The viewer uses **outlier rejection** when calculating timeline duration for zoom-to-fit. If a clip has a bogus large duration (e.g. a 10,000-frame fallback), it is dropped from the fit calculation so the view is not dominated by it.
- When a master switches sequences, the viewer automatically follows the new active timeline.
- The viewer re-requests session state if it gets stuck in `JOINING` for more than 10 seconds.
