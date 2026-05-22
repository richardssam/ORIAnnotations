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
              ┌────────────────────┤
              │                    │
    sync_viewer/server.py     other RV instances
      └─ SyncManager               └─ SyncManager
         (passive observer)
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

### Receiving annotations from remote peers

Remote annotations arrive as `insert_child` events (action returned by
`manager.tick()`).  If the clip has `annotation_commands` in metadata,
`_apply_remote_annotation` converts the SyncEvent list to xStudio pen-stroke
dicts and calls `bm.set_annotation(strokes=…)` on the relevant bookmark.
`_annotation_bookmarks: dict[(clip_guid, frame), Bookmark]` caches the
bookmark so that subsequent `annotation_commands_added` events can update it
in place rather than creating a duplicate.

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
