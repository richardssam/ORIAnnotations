---
layout: default
title: OpenRV Sync Plugin
parent: ORI Sync Tools
nav_order: 3.7
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

On startup the plugin broadcasts a `WHO_IS_MASTER` discovery message. If no response arrives within 2 seconds the instance promotes itself to **master** and initialises a fresh OTIO timeline. Subsequent instances receive `I_AM_MASTER`, request a full state snapshot, and then sync incremental deltas (property changes, clip insertions, annotations) as they occur.
