# ORIAnnotations — LLM Session Guide

This document gives a future Claude Code session the context needed to work on this codebase without re-deriving everything from scratch.

---

## What this project is

A toolkit for sharing review annotations between applications in real time. The primary integration is OpenRV, with an xStudio plugin also present. A web-based debug viewer (`sync_viewer`) can join any live session as a passive observer.

The core protocol is built on top of **OpenTimelineIO (OTIO)**: the shared state is an OTIO `Timeline`, mutations are broadcast as structured JSON messages over **RabbitMQ**, and every peer maintains a local replica of that timeline.

---

## Repository layout

| Path | What it is |
| --- | --- |
| `python/otio_sync_core/` | Network-agnostic sync library. The thing all peers use. |
| `python/otio_sync_core/manager.py` | `SyncManager` — master-election, timeline mutations, annotation persistence. |
| `python/otio_sync_core/rabbitmq_network.py` | RabbitMQ fanout-exchange backend (uses `pika`). |
| `python/otio_sync_core/proxy.py` | `OTIOSyncProxy` — transparent attribute-write interceptor for OTIO objects. |
| `rvplugin/openrv_sync_plugin/plugin.py` | OpenRV plugin. Builds OTIO timelines from RV sessions, broadcasts playback & annotations. |
| `rvplugin/openrv_sync_plugin/makepackage.csh` | Build script that produces the `.rvpkg` installable. |
| `sync_viewer/server.py` | FastAPI + WebSocket debug viewer server. Joins the session as a passive peer. |
| `sync_viewer/static/index.html` | Single-file browser UI for the debug viewer. |
| `xstudio_plugin/` | xStudio equivalent of the RV plugin (separate integration). |
| `otio_event_plugin/` | OTIO schemadef plugin that defines `SyncEvent` — used to embed annotation commands in OTIO metadata. |
| `openspec/` | Protocol specification documents. |

---

## System architecture

```text
OpenRV (master)
  └─ plugin.py
       └─ SyncManager ──► RabbitMQ fanout exchange
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
    sync_viewer/server.py     other RV instances    xStudio
      └─ SyncManager               └─ SyncManager   └─ ori_sync_plugin.py
         (passive observer)                               └─ SyncManager
```

- **Master election**: on join, peers broadcast `WHO_IS_MASTER`. The first to respond becomes master. If no response within ~2 s, the caller elects itself. Only one master per session.
- **State sync**: a new peer broadcasts `STATE_REQUEST`; master responds with a full `STATE_SNAPSHOT` (serialised OTIO + playback state). The new peer applies it and enters `STATE_SYNCED`.
- **Delta updates**: after sync, mutations (inserts, property changes, annotations) are broadcast as `OTIO_SESSION` / `ANNOTATION` messages. Every peer applies them locally.
- **Self-filtering**: `RabbitMQNetwork` discards any incoming message whose `source_guid` matches the local peer's `self_guid`, preventing echo-loops.

---

## `SyncManager` — key public API

```python
manager = SyncManager(session_id="my-session", network=RabbitMQNetwork(...))
manager.start_session()          # begin discovery
events = manager.tick()          # call repeatedly; returns list of app-level events
manager.timelines                # {guid: otio.schema.Timeline}
manager.object_map               # {guid: otio_object} flat index
manager.playback_state           # dict with current_time, playing, fps, …
manager.active_timeline_guid
manager.is_master
manager.broadcast_annotation(data)
```

`tick()` drives the receive/apply loop and returns events like `("playback_settings", data)`, `("annotation_stroke_release", data)`. Callers react to these events; the viewer's `_poll_loop` in `server.py` pushes state to WebSocket clients whenever `tick()` returns a non-empty list.

---

## RV plugin — non-obvious constraints

### Frame numbering

RV uses **1-based** frame numbers. OTIO track time is **0-based**.

- On broadcast: `value = current_frame - 1`
- On apply: `target_frame = int(protocol_value) + 1`

### Getting clip duration from RV

`RVFileSource` media properties behave differently for image sequences vs. movie files:

| Property | Meaning for movies | Usable? |
| --- | --- | --- |
| `media.numFrames` | Always 1 (file count, not frame count) | No |
| `media.startFrame` | Uninitialized default (0 or 9999) | No |
| `media.endFrame` | Uninitialized default | No |
| `media.fps` | Actual media fps ✓ | **Yes** |

**The correct approach for movie files** is to read the sequence **EDL** from the inner `RVSequence` node (not the `RVSequenceGroup`):

```python
for n in rv.commands.nodesInGroup(seq_group):
    if rv.commands.nodeType(n) == "RVSequence":
        frames = rv.commands.getIntProperty(f"{n}.edl.frame")
        # frames[i+1] - frames[i] = frame count for source i
```

`edl.frame` is a list of sequence-start-frame numbers (one per source). The duration of source `i` is `edl.frame[i+1] - edl.frame[i]`. For the last source, subtract from `rv.commands.frameRange()[1] - frameRange()[0] + 1`.

### `rv.commands.fps()` returns 24 at init time

The session fps is not reliably correct until media headers are fully read. Always read the media's own fps from `media.fps` on the `RVFileSource` node:

```python
media_fps = rv.commands.getFloatProperty(f"{file_source_node}.media.fps")[0]
if media_fps and media_fps > 0:
    fps = media_fps
```

### Display state sync — RV

The plugin broadcasts and applies `DISPLAY_SETTINGS` messages containing `pan`, `zoom`, `exposure`, and `channel`.  Several non-obvious constraints apply.

#### The two `RVDisplayColor` nodes

`rv.commands.nodesOfType("RVDisplayColor")` returns **two** nodes:

| Node name prefix | Pipeline | Affected by r/g/b/a keys? |
| --- | --- | --- |
| `defaultOutputGroup_colorPipeline_0` | Output / export | **No** |
| `displayGroup0_colorPipeline_0` | Active viewer display | **Yes** |

Always prefer the `displayGroup*` node for channel isolation:

```python
def _rv_display_color_node(self):
    for n in rv.commands.nodesOfType("RVDisplayColor"):
        if n.startswith("displayGroup"):
            return n
    return rv.commands.nodesOfType("RVDisplayColor")[0]
```

#### Channel isolation — `channelFlood`, not `channelOrder`

The r/g/b/a key bindings change `color.channelFlood` (int), **not** `color.channelOrder` (string, used for channel reordering permutations like GBRA):

```python
_RV_FLOOD_TO_CH = {0: "RGBA", 1: "R", 2: "G", 3: "B", 4: "A"}
_RV_CH_TO_FLOOD = {"RGBA": 0, "R": 1, "G": 2, "B": 3, "A": 4}
```

#### Pan and zoom — `rv.extra_commands`, not node properties

Pan and zoom are viewer-level transforms, not properties on any DAG node.  Use:

```python
import rv.extra_commands
zoom = rv.extra_commands.scale()           # float, 1.0 = fit-to-window
rv.extra_commands.setScale(float(zoom))
pan  = rv.extra_commands.translation()    # plain tuple (x, y)
rv.extra_commands.setTranslation((x, y))  # plain tuple — rv.rvtypes.Point does NOT exist
```

`RVDisplayGroup` has no transform component; attempting to set transform2D properties on it raises `invalid property name`.

#### Exposure — per-source `RVColor` node, 3-element array

The `e` key changes `RVColor.color.exposure`, a **3-element** `[r, g, b]` float array on the **current source's** node.  To find it:

```python
sources = rv.commands.sourcesAtFrame(rv.commands.frame())
src = sources[0]
node = src[:-len("_source")] + "_colorPipeline_0"  # e.g. sourceGroup000002_colorPipeline_0
exp = rv.commands.getFloatProperty(f"{node}.color.exposure")[0]
```

When broadcasting an exposure change, normalise **all** `RVColor` nodes to the same value so that navigating between clips doesn't trigger spurious re-broadcasts:

```python
for node in rv.commands.nodesOfType("RVColor"):
    rv.commands.setFloatProperty(f"{node}.color.exposure", [ev, ev, ev], True)
```

#### `None` pan/zoom in the protocol

A peer that cannot read its own pan/zoom (e.g. xStudio — see below) sends `"pan": null, "zoom": null` in the `DISPLAY_SETTINGS` payload.  **Skip** applying null fields rather than treating them as zero/one:

```python
pan = data.get("pan")   # None → don't touch local pan
zoom = data.get("zoom") # None → don't touch local zoom
if pan is not None:
    rv.extra_commands.setTranslation((float(pan[0]), float(pan[1])))
if zoom is not None:
    rv.extra_commands.setScale(float(zoom))
```

After applying a received display state, read the current RV state back into `_last_display_state` so the null fields don't look like a change on the next broadcast poll.

### Annotation persistence must happen on all peers

In `manager.py` `_process_message`, `_persist_annotation_to_timeline` must be called for **all** received `ANNOTATION` messages, not just when `self.is_master`. The master persists its own strokes inside `broadcast_annotation` (before sending), so there is no double-persist: self-sent messages are filtered by `source_guid` in `RabbitMQNetwork` before reaching `_process_message`.

---

## Building and installing the RV plugin

```bash
cd rvplugin/openrv_sync_plugin
bash makepackage.csh          # produces otiosyncdemo-0.1.rvpkg
```

`makepackage.csh` vendors `pika` (from `~/.pyenv/…/site-packages/pika`) and zips `plugin.py`, `PACKAGE`, `pika/`, and `otio_sync_core/` into the `.rvpkg`. After rebuilding you must reinstall the package in OpenRV's Package Manager and **restart RV**.

The `otio_sync_core` library bundled inside the `.rvpkg` is a **copy** of `python/otio_sync_core/`. Any change to the library files requires a package rebuild.

Logs are written to `rvplugin/openrv_sync_plugin/host.log` (set `RV_OTIO_SYNC_LOG_FILE` env var to the desired path, or see `_make_otio_logger` in `plugin.py`).

---

## Running the sync_viewer

```bash
cd sync_viewer
pip install -r requirements.txt   # fastapi, uvicorn, opentimelineio
python server.py [--host localhost] [--port 8765] \
                 [--rmq-host localhost] [--rmq-port 5672] \
                 [--session otio-sync-demo]
```

Open `http://localhost:8765`. The viewer joins as a non-master passive observer. It does **not** need the RV plugin to be installed.

`server.py` requires `opentimelineio` and `otio_sync_core` on its Python path. `sys.path.insert(0, "../python")` in `server.py` handles this when run from the `sync_viewer/` directory.

---

## sync_viewer — non-obvious constraints

### `contentDur()` — outlier-rejection for bogus clip durations

Before the EDL fix was in place, the plugin produced clips with a 10 000-frame fallback duration. The viewer's `contentDur(tl)` function in `index.html` iterates all item `(start + duration)` values and strips top-end outliers where the maximum is ≥ 10× the 25th-percentile value. This keeps the zoom-to-fit from being dominated by a single bad clip.

### Auto-refit when data changes

`autoZoomed` is a one-way latch that suppresses re-fitting after the user manually zooms. It is reset when `contentDur` changes by more than 20% between renders (tracked in `lastFitDur`), so that the view refits when corrected clip durations arrive from a plugin update.

### Annotation clips are 1 frame wide

`_persist_annotation_to_timeline` creates a `source_range` of 1 frame per stroke. At fit-zoom they appear as ~2 px amber marks in the Annotations track row. Zoom in to inspect them.

---

## xStudio plugin — non-obvious constraints

The plugin lives in `xstudio_plugin/ori_sync/ori_sync_plugin.py`.  Set
`ORI_SYNC_LOG_FILE=/path/to/xstudio_client.log` before launching xStudio so
the plugin writes a persistent log (mirrors `RV_OTIO_SYNC_LOG_FILE` for RV).

### Global playhead events — Form 1 vs Form 2

`subscribe_to_global_playhead_events` delivers events in two shapes:

| Form | Length | `event[1]` | Playhead actor |
| --- | --- | --- | --- |
| 1 | 3 | `viewport_playhead_atom` | `event[2]` |
| 2 | 4 | `viewport_playhead_atom` | `event[3]` (also has viewport name at `event[2]`) |

**Only handle Form 2** (`len(event) > 3`).  Form 1's playhead actor may differ
from the one the user is actually scrubbing on.  This matches the reference
plugin `xstudio_live_review.py`.

### `subscribe_to_playhead_events` cancels all previous subscriptions

`auto_cancel=True` (the default) calls `unsubscribe_from_event_group` on
**every** entry in `self.playhead_subscriptions`, not just the one for the same
event group.  With multiple timelines loaded, Form 2 fires once per timeline;
each re-subscription cancels the previous one, leaving only the last timeline's
playhead active.  The user scrubs on the first timeline → no events arrive.

**Workaround**: do not rely on playhead-event subscriptions for scrub detection.
Use poll-based position reading from the poll thread instead (see
`_poll_and_broadcast_frame`).

### Poll-based scrubbing with echo guard

`_poll_and_broadcast_frame` (called every `POLL_INTERVAL` from the poll thread)
reads `active_playhead.position` directly.  An echo guard prevents re-broadcasting
a frame that was just applied from a remote `PLAYBACK_SETTINGS` message:

```python
# In _apply_playback_state, before setting position:
self._last_applied_frame = frame
self._last_polled_frame = frame

# In _poll_and_broadcast_frame:
if frame == self._last_polled_frame:
    return          # no change
self._last_polled_frame = frame
if frame == self._last_applied_frame:
    return          # remote-applied, skip echo
```

### Annotation trigger: `show_atom` + periodic fallback scan

`annotation_atom` events from the `AnnotationsUI` plugin events group do **not**
fire in the tested builds.  `show_atom` fires when a **new** bookmark is created,
but does **not** fire when the user adds a second stroke to an existing bookmark
on the same frame.

Therefore `_on_global_playhead_event` sets `_annotation_pending_time` when
`show_atom` arrives (fast path, ~250 ms debounce for new bookmarks), **and**
`_flush_pending_annotations` also runs a periodic fallback scan every
`ANNOTATION_SCAN_INTERVAL` (0.5 s) so that strokes added to existing bookmarks
are caught even when no event fires.

### `annotation_data` structure

`bm.annotation_data` returns:

```python
{"plugin_uuid": "…", "Data": {"pen_strokes": […], "captions": […], …}}
```

The canvas dict lives under `"Data"`, **not** at the top level:

```python
canvas = ann_data.get("Data", ann_data)   # fallback covers format changes
```

### Coordinate system: xStudio ↔ OTIO/RV

| System | x range | y | origin |
| --- | --- | --- | --- |
| xStudio native | `[-1, 1]` (W-norm) | down | centre |
| OTIO SyncEvent / RV paint | `[-aspect_half, aspect_half]` (H-norm) | up | centre |

Conversion (send path, xStudio → OTIO):

```python
x_otio =  x_xs * aspect_half    # aspect_half = W / (2 * H)
y_otio = -y_xs * aspect_half
```

Inverse (receive path, OTIO → xStudio):

```python
x_xs =  x_otio / aspect_half
y_xs = -y_otio / aspect_half
```

RV's `{pen}.points` property uses the same H-normalised Y-up system as the
OTIO SyncEvent, so xStudio-origin OTIO coordinates can be written to
`{pen}.points` directly without further transformation.

### Multiple strokes per frame — delta tracking

Delta tracking uses the **OTIO timeline as ground truth**, not a per-bookmark
counter.  `_count_track_strokes(annotation_track, clip_guid, frame)` counts
`PaintStart` events already in the annotation track (looked up directly from
`manager._object_map`, not traversed from `timeline.tracks`).  The delta is
`all_strokes[sent_strokes:]`.

Why not a counter keyed by bookmark UUID or `(clip_guid, frame)`?  xStudio may
replace a bookmark with a new UUID when the user adds strokes to an existing
frame.  A UUID-keyed counter resets to zero for the new UUID and re-sends
already-broadcast strokes.  A `(clip_guid, frame)`-keyed counter misses strokes
when xStudio creates a fresh bookmark per stroke with only that one stroke in it.
The OTIO timeline is always correct because `broadcast_add_annotation` updates
it synchronously before returning.

Do **not** add locally-drawn bookmark UUIDs to `_our_bookmark_uuids` — that set
is only for *remote-sourced* bookmarks.  Local ones must remain scannable so
subsequent strokes on the same frame are picked up.

### Display state sync — xStudio

#### Reading zoom and pan via `serialise_atom`

xStudio's viewport exposes its internal `state_.scale_` and `state_.translate_` through `serialise_atom` (exported to Python in `py_atoms.cpp`):

```python
from xstudio.core import serialise_atom
import json

js = connection.request_receive(vp.remote, serialise_atom())[0]
vp_state = json.loads(js.dump())["base"]
raw_scale = float(vp_state["scale"])
translate = vp_state["translate"]   # Imath::V3f serialises as a JSON array [x, y, z]
pan = [float(translate[0]), float(translate[1])]
```

`Imath::V3f` serialises as a **JSON array** `[x, y, z]`, **not** a dict `{"x":…, "y":…, "z":…}`.

#### xStudio zoom convention vs. RV

xStudio's `state_.scale_` is NOT a direct zoom multiplier.  It is proportional to `image_pixels / viewport_pixels`, so:

- **Larger `state_.scale_`** → more zoomed in (the projection matrix uses `1/scale`, so a larger divisor magnifies the image)
- At fit-to-window: `state_.scale_` ≈ `image_width / viewport_width` (can be 5–15 for a large image in a normal window)

To convert to RV's convention (1.0 = fit-to-window, 2.0 = 2× zoom in):

```python
# On first successful read, record the fit-to-window baseline
if self._xs_base_scale is None and raw_scale > 0.0:
    self._xs_base_scale = raw_scale

# Protocol zoom: ratio relative to baseline
zoom_protocol = raw_scale / self._xs_base_scale  # >1 = zoomed in, <1 = zoomed out
```

Reset `_xs_base_scale = None` on disconnect so it re-calibrates on reconnect.

#### "Pan" and "Zoom" module attributes are boolean toggles

`vp.get_attribute("Zoom")` and `vp.get_attribute("Pan")` return **boolean mode toggles** (enter/exit zoom-drag or pan-drag mode), defined as `add_boolean_attribute("Zoom", "Zm", false)` in `viewport.cpp`.  They are **not** the current pan/zoom position.  Do not use them to read or set viewport position.

#### Writing zoom/pan — `deserialise_atom` crashes xStudio

`deserialise_atom` feeds the full viewport JSON back through `Viewport::deserialise`, which then reconstructs `ColourTriplet` and other complex C++ types from the JSON.  The round-trip through Python's `json.loads` does not preserve the type information those deserializers expect, causing a **signal 11 crash** inside `adl_serializer<ColourTriplet>::from_json`.

**Do not use `deserialise_atom` to write pan/zoom from Python.**

#### One-way zoom sync and the missing atoms

As a result: xStudio → RV zoom sync works (read via `serialise_atom`, broadcast), but **RV → xStudio zoom sync is not possible** with the current Python API.

The proper fix is to expose `viewport_scale_atom` and `viewport_pan_atom` in `py_atoms.cpp` (two lines, both atoms already exist in `atoms.hpp` and are handled by the viewport actor).  `viewport_scale_atom` already takes/returns a plain `float`; `viewport_pan_atom` would need a `(float, float)` overload or an `Imath::V2f` binding.

#### Lazy playhead initialisation

`current_playhead()` raises `RuntimeError: invalid_argument` if xStudio has no media loaded when the plugin connects.  The poll loop retries lazily:

```python
if not self.active_playhead:
    try:
        self.active_playhead = self.current_playhead()
    except Exception:
        return
```

### Receiving annotations from remote peers

Remote annotations arrive as `insert_child` events (action returned by
`manager.tick()`).  If the clip has `annotation_commands` in metadata,
`_apply_remote_annotation` converts the SyncEvent list to xStudio pen-stroke
dicts and calls `bm.set_annotation(strokes=…)` on the relevant bookmark.
`_annotation_bookmarks: dict[(clip_guid, frame), Bookmark]` caches the
bookmark so that subsequent `annotation_commands_added` events can update it
in place rather than creating a duplicate.

---

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
