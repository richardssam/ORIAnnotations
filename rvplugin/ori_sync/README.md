---
layout: default
title: OpenRV Sync Plugin
parent: ORI Sync Tools
nav_order: 3.1
---

# OTIO Sync Plugin for OpenRV

An OpenRV plugin that synchronises OTIO timeline state between multiple OpenRV instances in real time, using RabbitMQ as the message bus.

## Requirements

- **OpenRV** 2.0.0 or later
- **RabbitMQ** running locally (default: `localhost:5672`)
- **opentimelineio** installed in your Python environment
- **pika** (vendored into the `.rvpkg` bundle by `makepackage.csh`)

## Building and Installing

Build the package:

```bash
cd rvplugin/ori_sync
./makepackage.csh
```

Install into OpenRV:

```bash
./reinstall.csh
```

`reinstall.csh` rebuilds the package, removes any existing installation, and installs + enables the plugin in one step.

## Environment Variables

| Variable | Type | Description |
|---|---|---|
| `DEBUG_OTIO_SYNC` | any non-empty value | Enables console logging of all sync events. Set to any non-empty string to activate (e.g. `export DEBUG_OTIO_SYNC=1`). |
| `ORI_SYNC_LOG_FILE` | file path | Writes timestamped debug logs to the specified file (e.g. `export ORI_SYNC_LOG_FILE=/tmp/otio_sync.log`). Independent of `DEBUG_OTIO_SYNC` — both can be active at the same time. |

Both variables are read at plugin load time. Changes made after OpenRV starts will have no effect.

### Example

```bash
export DEBUG_OTIO_SYNC=1
export ORI_SYNC_LOG_FILE=/tmp/otio_sync.log
open /Applications/OpenRV.app
```

## How it works

On connect the plugin broadcasts a `WHO_IS_MASTER` discovery message and waits up to 2 seconds for a response. On the **Create Session** path, no response means this instance promotes itself to **master** and initialises a fresh OTIO timeline. On the **Join Session** path, no response means the named session doesn't exist — the connect fails with a "Session Not Found" warning instead of silently starting an empty one under that name. Subsequent instances that do find a master receive `I_AM_MASTER`, request a full state snapshot, and then sync incremental deltas (property changes, clip insertions, annotations) as they occur.

The Create/Join dialogs also show the resolved **account** identity (read-only — the underlying OS/account login, distinct from the editable display-name field below it) so you can confirm which account you're about to connect as.

### Site config file

A JSON file named `ori_site_config.json`, dropped into this plugin's folder (alongside `plugin.py`/`utils.py`), pre-defines the MQ Host default shown in the connect dialogs:

```json
{
  "host": "mq.example.com",
  "port": 5672
}
```

Precedence: a value typed into the dialog > the `ORI_RMQ_HOST` environment variable > this file > the hardcoded `127.0.0.1` fallback. A missing or malformed file is silently ignored.
