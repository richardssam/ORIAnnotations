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

## Creating and joining a session

The OpenRV and xStudio plugins share the same core sync library (`python/otio_sync_core`) and wire protocol, so peers running either app can join the same session interchangeably. A session is identified purely by a session name/ID — it becomes a RabbitMQ exchange (`sync_session_<session_id>`), and there is no separate server-side session registry.

**Via the UI:**

* **OpenRV**: the "OTIO Sync" menu offers **Create Session...** and **Join Session...**, each opening a dialog for MQ Host, Session Name, and a display-name override. Create also lets you pick a default role (Driver/Reviewer/Viewer) for the session.
* **xStudio**: connection settings (MQ Host, MQ Port, Session ID) live under Preferences → `ori_sync_conn`; connecting is triggered from there.

**Via environment variable:** setting `ORI_SESSION=[host:]session_name` before launching either app auto-connects at startup, skipping the dialog.

**What happens on connect (both apps):** the plugin broadcasts `WHO_IS_MASTER` and waits briefly for a reply.

* **Create**: if nobody answers, this peer self-elects as master and initializes a fresh timeline.
* **Join**: if nobody answers, the named session doesn't exist yet and the connection fails ("Session Not Found") rather than silently creating an empty one. If a master does answer, the joiner requests a full state snapshot and then stays in sync via incremental deltas.

## Roles

Sync authority is checked along a few independent axes. The one most relevant to users is **role**, which caps what a participant is ever allowed to broadcast:

* **Driver** — full control: can change what's shown (visibility), playhead position, display settings, timeline structure, and annotations (including clearing others' annotations), and can grant roles to other peers. This is the default role when no role policy has been configured for a session.
* **Reviewer** — can change playhead position, display settings, and add (non-destructive) annotations, but cannot change what's shown, edit timeline structure, clear other peers' annotations, or administer roles.
* **Viewer** — passive: can only change their own local per-peer display state (pan/zoom/exposure/channel). Everything else is stripped from their broadcasts.

An unset or unrecognized role resolves to Driver, not Viewer — the permissive end, not the restrictive one. Roles are granted with `SyncManager.set_peer_role(user, role)`, and any Driver can grant roles to others (this isn't restricted to a single "host"). If a session ends up with no eligible Driver, any peer can use "Become Controller" to self-promote, which is only offered in that situation.

A few related but distinct concepts:

* **Master** — the peer holding the canonical timeline snapshot; the first peer to answer `WHO_IS_MASTER`, or the one who self-elected on Create.
* **Host** — the current "visibility authority" for the session (i.e. whose selection of clip/view is authoritative). Computed automatically from the peer table, preferring xStudio over OpenRV, then the master, then a GUID tie-break — filtered to peers whose role permits visibility changes.
* **Lease owner** — a momentary, per-channel (position/display/structure/visibility) write lock that prevents two peers from fighting over the same change at once; refreshed on each broadcast.

## Common environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `ORI_SESSION` | `[host:]session_name` — auto-connects at launch, skipping the Create/Join dialog | unset (start disconnected) |
| `ORI_RMQ_HOST` | RabbitMQ broker host used as the dialog default and by `ORI_SESSION` when no host is given | `127.0.0.1` (OpenRV), `localhost` (xStudio) |
| `ORI_SYNC_USER` | Overrides the resolved account identity used to attribute changes | OS login |
| `ORI_SYNC_NAME` | Overrides the resolved display name | OS full name |
| `ORI_SESSION_DEFAULT_ROLE` | Default role assigned to peers when no other role policy applies | `driver` |
| `ORI_SESSION_PEER_ROLES` | Pre-seeded role grants at startup, as `user=role,user=role` | none |
| `ORI_SESSION_ROLES` | Set to `0`/`false`/`no`/`off` to disable role enforcement entirely | enabled |
| `ORI_VISIBILITY_AUTHORITY` | Set to `0`/`false`/`no`/`off` to disable host-only visibility gating | enabled |
| `ORI_BROADCAST_OWNERSHIP` | Set to `0`/`false`/`no`/`off` to disable position/display/structure write-lease gating | enabled |
| `ORI_STRUCTURE_EVENTS` | Set to `0` to disable event-driven structure discovery (xStudio only) | enabled |
| `DEBUG_OTIO_SYNC` | Any non-empty value enables console logging of sync events | unset |
| `ORI_SYNC_LOG_FILE` | File path for timestamped debug logs, independent of `DEBUG_OTIO_SYNC` | unset (no file logging) |
| `ORI_FULLSTATE_FILE` | Path xStudio periodically dumps its full session state to, for out-of-process test tooling | unset |

A site config file (`ori_site_config.json`, next to the plugin) can also set a default `{"host": ..., "port": ...}`. Precedence for the MQ host is: value typed into the dialog > `ORI_RMQ_HOST` > this config file > the hardcoded fallback.

## Resources & Test Media

* **Test Media (`test_media`)**: Contains generated test charts and media sequences used for testing the sync plugins and test frameworks. See the [test_media README](test_media/README.md).
* **Test Chart Generator (`testchart`)**: Scripts for generating the synthetic test media. See the [testchart README](testchart/README.md).
