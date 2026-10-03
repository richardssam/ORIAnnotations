# ORIAnnotations — LLM Session Guide

This document gives a future AI agent session the context needed to work on this codebase without re-deriving everything from scratch.

---

## What this project is

A toolkit for sharing review annotations between applications in real time. The primary integration is OpenRV, with an xStudio plugin also present. A web-based debug viewer (`sync_viewer`) can join any live session as a passive observer.

The core protocol is built on top of **OpenTimelineIO (OTIO)**: the shared state is an OTIO `Timeline`, mutations are broadcast as structured JSON messages over **RabbitMQ**, and every peer maintains a local replica of that timeline.

For a detailed breakdown of the protocol and architecture, see [docs/architecture.md](docs/architecture.md).

---

## Repository layout

| Path | What it is |
| --- | --- |
| `python/otio_sync_core/` | Network-agnostic sync library. The thing all peers use. |
| `python/otio_sync_core/manager.py` | `SyncManager` — master-election, timeline mutations, annotation persistence. |
| `python/otio_sync_core/rabbitmq_network.py` | RabbitMQ fanout-exchange backend (uses `pika`). |
| `python/otio_sync_core/proxy.py` | `OTIOSyncProxy` — transparent attribute-write interceptor for OTIO objects. |
| `rvplugin/ori_sync/plugin.py` | OpenRV sync plugin. Builds OTIO timelines from RV sessions, broadcasts playback & annotations. |
| `rvplugin/ori_sync/makepackage.csh` | Build script that produces the `.rvpkg` installable. |
| `rvplugin/ori_annotations/` | Legacy OpenRV plugin for exporting/importing annotations as custom OTIO files. |
| `sync_viewer/server.py` | FastAPI + WebSocket debug viewer server. Joins the session as a passive peer. |
| `sync_viewer/static/index.html` | Single-file browser UI for the debug viewer. |
| `xstudio_plugin/` | xStudio equivalent of the RV plugin (separate integration). |
| `otio_event_plugin/` | OTIO schemadef plugin that defines `SyncEvent` — used to embed annotation commands in OTIO metadata. |
| `openspec/` | OpenSpec root: the `otio_sync_protocol_v1.md` protocol spec, plus `specs/` and `changes/` (the change-proposal workflow the `.claude/skills/openspec-*` skills operate on). |

---

## Important Technical Documentation

Before modifying specific components, **you MUST read the corresponding constraints document**. The codebase has numerous non-obvious gotchas regarding threading, event loops, and coordinate systems.

- **OpenRV Plugin**: [docs/openrv_constraints.md](docs/openrv_constraints.md)
- **xStudio Plugin**: [docs/xstudio_constraints.md](docs/xstudio_constraints.md)
- **Sync Viewer**: [docs/sync_viewer_constraints.md](docs/sync_viewer_constraints.md)
- **Running & writing tests**: [docs/testing.md](docs/testing.md) — two suites on two interpreters (`./run_tests_core.sh`, `./run_tests_xstudio.sh`), the `rv` stub collision that makes tests pass alone and fail in a full run, and the one known flaky test.
- **Debugging & Diagnostics**: [debug/README.md](debug/README.md)

---

## Python coding style

All Python uses **Sphinx reStructuredText docstrings** (docs built with `make html` in `docs/`). See `python/otio_sync_core/manager.py` for examples. Key rules:

- `:param name:`, `:returns:`, `:rtype:`, `:raises ExcType:` fields in every public function/method.
- Class docstrings document `__init__` params; don't repeat them on `__init__` itself.
- Cross-reference OTIO types as `:class:`~opentimelineio.schema.Timeline``.
- No docstrings on private helpers (`_foo`) unless the logic is non-obvious.
- Inline comments explain *why*, not *what*.

---

## Dependencies

Runtime: `opentimelineio`, `pika` (RabbitMQ client, vendored into `.rvpkg`), `fastapi` + `uvicorn` (sync_viewer only). Requires a RabbitMQ broker at `localhost:5672` (no auth needed for local use).

---

## Tests

- **Unit Tests**: `tests/otio_sync/` via `./run_tests_core.sh`, and `tests/xstudio_plugin/` via `./run_tests_xstudio.sh` (needs xStudio's bundled interpreter)
- **UI / Integration Tests**: `sync_test/` (Python framework driving OpenRV and xStudio)

See [docs/testing.md](docs/testing.md) for the `rv` stub collision and the known flaky test.
