# Debug Tools & Sync Port Configurations

This directory contains utility scripts and documentation to assist with debugging selection synchronization and media updates between running xStudio and OpenRV instances.

## Port Configurations

During synchronization sessions, the application instances are configured to listen on the following local TCP ports for API commands and state inspection:

| Application | Role | Port | Connection String / Scheme |
|---|---|---|---|
| **xStudio Host** | Master | `14441` | `127.0.0.1:14441` |
| **xStudio Client** | Peer / Client | `14442` | `127.0.0.1:14442` |

## Inspection Scripts

### xStudio Inspector

The script [xstudio_inspect.py](file:///Users/sam/git/ORIAnnotations/debug/xstudio_inspect.py) queries the running xStudio Master and Client processes, printing information about the viewport states, viewed containers, playhead positions, and track/clip hierarchies.

#### How to Run

To run the script, use the xStudio embedded python interpreter:

```bash
/Users/sam/git/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 debug/xstudio_inspect.py
```

## Logs

Log output for the plugins and network messaging are generated to the following locations when debugging is active:
- Plugin log files specified via `ORI_SYNC_LOG_FILE` environment variable.
- standard xStudio log outputs (`xstudio_host.log`, `xstudio_client.log`).

## Diagnosing live viewer state from an external script

### OpenRV — rvpush

`rvpush` connects to a running OpenRV session over its network port.  Use
`py-eval-return` to get values back and `py-exec` to execute statements.

```bash
RVPUSH=/Applications/openRV.app/Contents/MacOS/rvpush

# Current zoom and pan
$RVPUSH py-eval-return "rv.extra_commands.scale()"
$RVPUSH py-eval-return "rv.extra_commands.translation()"

# All RVDisplayColor nodes and their channelFlood value
$RVPUSH py-eval-return "[(n, rv.commands.getIntProperty(n+'.color.channelFlood')) for n in rv.commands.nodesOfType('RVDisplayColor')]"

# Exposure on the current source
$RVPUSH py-eval-return "[(n, rv.commands.getFloatProperty(n+'.color.exposure')) for n in rv.commands.nodesOfType('RVColor')]"

# Fire a key event (e.g. simulate pressing 'r' to switch to red channel)
$RVPUSH py-exec "rv.commands.sendInternalEvent('key-down--r', '')"

# Set zoom and pan programmatically
$RVPUSH py-exec "import rv.extra_commands; rv.extra_commands.setScale(2.0)"
$RVPUSH py-exec "import rv.extra_commands; rv.extra_commands.setTranslation((0.1, 0.0))"
```

Note: use `py-eval-return` (not `py-eval`) for expressions that return values.

### xStudio — external Python connection

xStudio exposes the same Python API to external scripts as to in-process
plugins.  `Connection(auto_connect=True)` discovers the running xStudio
instance via a local socket file — no host/port needed.

```python
import json
from xstudio.connection import Connection
from xstudio.api.intrinsic.viewport import Viewport
from xstudio.core import serialise_atom

XSTUDIO = Connection(auto_connect=True)

# Read current viewport zoom and pan via serialise_atom
vp = Viewport(XSTUDIO, active_viewport=True)
js = XSTUDIO.request_receive(vp.remote, serialise_atom())[0]
state = json.loads(js.dump())["base"]
print("scale  :", state["scale"])
print("translate:", state["translate"])   # [x, y, z]

# Read exposure and channel via colour pipeline
cp = vp.colour_pipeline
print("exposure:", cp.exposure.value())
print("channel :", cp.channel.value())

XSTUDIO.disconnect()
```

Run with xStudio's bundled Python so the `xstudio` package is on the path:

```bash
/Users/sam/git/xstudio/build/xSTUDIO.app/Contents/Frameworks/bin/python3 diag.py
```

Or add the package to your own Python's path:

```bash
export PYTHONPATH=/Users/sam/git/xstudio/build/xSTUDIO.app/Contents/Frameworks/lib/python3.12/site-packages:$PYTHONPATH
python3 diag.py
```

### Using the xStudio Inspector Debug Tool

For quick and comprehensive connection status, viewport container type, active playhead frame, and playlist/track/clip hierarchy verification, use the dedicated [xstudio_inspect.py](file:///Users/sam/git/ORIAnnotations/debug/xstudio_inspect.py) script:

```bash
/Users/sam/git/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 -u debug/xstudio_inspect.py
```

See [debug/README.md](file:///Users/sam/git/ORIAnnotations/debug/README.md) for more details on port numbers and log file locations.

## Merging peer logs

[merge_sync_logs.py](file:///Users/sam/git/ORIAnnotations/debug/merge_sync_logs.py)
interleaves two or more peer logs into one time-ordered view. Every sync
question is a question about two peers at the same instant, and answering it
from separate files means grepping each for the terms you already suspect —
which biases the answer towards whatever you thought to grep for.

```bash
# Everything, interleaved and tagged by peer
python debug/merge_sync_logs.py rvplugin/ori_sync/xstudio_{host,client}.log

# Only the intervals where peers disagreed about what is on screen
python debug/merge_sync_logs.py --view diverge <logs...>

# What each peer believed: view, clip, timeline, frame, playback mode, role
python debug/merge_sync_logs.py --view state --since 15:03:00 <logs...>

# Each send, with which peers received it and how long it took
python debug/merge_sync_logs.py --view wire <logs...>

# Machine-readable, for one-off analysis
python debug/merge_sync_logs.py --json <logs...> > events.jsonl
```

Filters apply to every view: `--since` / `--until` (HH:MM:SS), `--grep REGEX`,
`--peer NAME`, `--no-wire` (drop MQ traffic), `--payload` (print each wire
message's payload in the merge view).

Peer names come from the filename (`xstudio_host.log` → `host`); override with
`name=path`. Pure stdlib, so any interpreter in the repo runs it.

**Reading `diverge` honestly.** A follower has its visibility fields stripped,
so it asserts nothing about the view — silence is not disagreement, and the
report only compares views a peer actually established, whether by asserting it
(the host) or applying it (everyone else). Source mode is keyed on the clip and
sequence mode on the timeline, because a peer isolated on a clip and a peer
showing the sequence containing it carry the same `timeline_guid` while looking
at different things.
