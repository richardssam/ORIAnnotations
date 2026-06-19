# xStudio plugin — non-obvious constraints

The plugin lives in `xstudio_plugin/ori_sync/ori_sync_plugin.py`.  Set
`ORI_SYNC_LOG_FILE=/path/to/xstudio_client.log` before launching xStudio so
the plugin writes a persistent log (mirrors `RV_OTIO_SYNC_LOG_FILE` for RV).

## ⚠️ xStudio OTIO export strips ALL metadata — no sync GUIDs survive

**This has bitten us more than once.**  `timeline_to_otio_string` (from
`xstudio.api.auxiliary.otio`) and the timeline's `to_otio_string()` build OTIO
objects from an xStudio timeline carrying **only** `name`, time ranges, and
media references — they copy **no `metadata` whatsoever** (see `__process_obj`
in the xStudio `auxiliary/otio/writer.py`).  So the exported OTIO has **no
`metadata.sync.guid`** on timelines *or* clips, and even the names are
unreliable (a Timeline actor named `"Default Sequence"` can export as
`"Default "` or `""`).

**Consequence:** you cannot recover sync GUIDs by exporting an xStudio timeline
and reading its metadata back.  Anything that needs guid-accurate state must
come from the plugin's `manager` (`manager._timelines` / `manager.export_state()`,
keyed by the real sync GUIDs) — **never** from an OTIO-export round-trip.

Where this has actually bitten us:
- `_poll_sequence_new_media` can't diff clips by GUID → uses index-based name
  alignment instead (see "Index-Based Timeline Sequence Polling" below).
- The `sync_test` inspector first reconstructed xStudio's `/full_state` via
  `timeline_to_otio_string` and got name-keyed, GUID-less timelines that never
  matched the recording.  Fixed with a **file bridge**: the plugin periodically
  writes `manager.export_state()` to `$ORI_FULLSTATE_FILE` (`_write_fullstate_file`
  in the poll loop) and the out-of-process inspector reads that file.

(Aside: `timeline_to_otio_string` also defaults `adapter_name=None`, which newer
OTIO rejects with "Could not find plugin: 'None'"; pass `adapter_name="otio_json"`
if you ever must call it.)

## Global playhead events — Form 1 vs Form 2

`subscribe_to_global_playhead_events` delivers events in two shapes:

| Form | Length | `event[1]` | Playhead actor |
| --- | --- | --- | --- |
| 1 | 3 | `viewport_playhead_atom` | `event[2]` |
| 2 | 4 | `viewport_playhead_atom` | `event[3]` (also has viewport name at `event[2]`) |

**Only handle Form 2** (`len(event) > 3`).  Form 1's playhead actor may differ
from the one the user is actually scrubbing on.  This matches the reference
plugin `xstudio_live_review.py`.

## `subscribe_to_playhead_events` cancels all previous subscriptions

`auto_cancel=True` (the default) calls `unsubscribe_from_event_group` on
**every** entry in `self.playhead_subscriptions`, not just the one for the same
event group.  With multiple timelines loaded, Form 2 fires once per timeline;
each re-subscription cancels the previous one, leaving only the last timeline's
playhead active.  The user scrubs on the first timeline → no events arrive.

**Workaround**: do not rely on playhead-event subscriptions for scrub detection.
Use poll-based position reading from the poll thread instead (see
`_poll_and_broadcast_frame`).

## `request_receive` has a 100-second default timeout — bound poll-thread reads

**This is the single most important gotcha for the sync poll thread.**

Every xStudio Python property that reads/writes an actor (`playhead.playing`,
`playhead.position`, `bm.set_annotation`, `bm.annotation_data`, `bm.detail`,
`vp.colour_pipeline.exposure.value()`, `container.type`, …) is a synchronous
`connection.request_receive(...)` bounded **only** by
`connection.default_timeout_ms`, which defaults to **100 000 ms (100 s)**.

If the target actor is **stale** — e.g. a playhead or viewport actor that was
destroyed during a source-view switch, or a bookmark actor that is busy under a
rapid partial-annotation stream — the call blocks the **poll thread** for the
full 100 s.  Symptom: xStudio's UI stays responsive (it uses the *new* live
actor), but sync silently stops — the poll thread is stuck, so `manager.tick()`
never runs and incoming RabbitMQ messages pile up unprocessed.  The last log
line is whatever poll-thread step entered the dead actor (`apply_patch …`,
`Updated annotation bookmark …`, etc.).

### A Python-thread timeout does **not** work

The blocking happens inside a C++ `link.dequeue_message_with_timeout` call that
**holds the GIL**.  A `threading.Thread(...).join(timeout=…)` around it can never
fire, because the worker thread never releases the GIL for the poll thread to
wake up.  The timeout **must** be enforced at the C++ level via
`default_timeout_ms`.

### The fix: `bounded` / `bounded_timeout` (in `utils.py`)

`bounded_timeout(connection, ms)` is a context manager that temporarily lowers
`default_timeout_ms`; `bounded(ms)` is the decorator form for whole methods.  A
healthy actor replies in a few ms, so a 2 s bound never trips in normal use, but
a dead actor raises promptly and the poll thread keeps running.

```python
@bounded(_PLAYHEAD_TIMEOUT_MS)      # 2000 ms
def apply_pending_seek(self): ...

with bounded_timeout(self.plugin.connection, 2000):
    playing_changed = (playing != ph.playing)   # was a potential 100 s hang
```

On a playhead timeout, `apply_playback_state` drops `active_playhead` and
re-acquires the live one via `current_playhead()` (which queries the
global-playhead-events actor, **not** the dead playhead).  A skipped annotation
render is harmless — the final `INSERT_CHILD` re-renders the full state.

### Rule for future revisions

- **Bound** any *poll-thread* read/write of a **playhead, viewport, or bookmark**
  actor — these go stale during source-view switches / annotation streaming.
  Currently bounded: `read_xs_display_state`, `apply_playback_state`,
  `apply_pending_seek`, `current_playback_state`,
  `resolve_and_broadcast_selection`, `apply_selection`,
  `_reacquire_active_playhead` (playback); `apply_remote_annotation`,
  `refresh_annotation_bookmark`, `broadcast_local_bookmark`,
  `flush_pending_annotations`, `hot_scan_active_annotation`,
  `load_snapshot_annotations`, `broadcast_live_stroke_from_json` (annotation).
- **Do NOT bound** the structural methods in `structure_sync.py` /
  `timeline_build.py` (`poll_*`, `apply_remote_move_child`, `do_load_timelines`,
  `build_otio_*`).  They touch **playlist/timeline** actors that *persist*
  through source-view switches (not stale-prone), and they contain
  `load_otio` / `to_otio_string` / `create_playlist`, which can legitimately
  take several seconds — a 2 s bound would abort valid slow operations.  If a
  freeze ever ends on a structural step, bound the *specific* non-`load_otio`
  call there, not the whole method.
- **Do NOT bound** the xStudio-thread event handlers (`on_position_event`,
  `on_global_playhead_event`, `on_selection_event`).  They run on xStudio's
  dispatch thread, not the poll thread, and a dead playhead fires no events so
  they only ever see live actors.

## Poll-based scrubbing with echo guard

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

## Selection Sync Feedback Loop & Playhead Fallback Prevention

Scrubbing or playing a `Timeline` sequence updates the active media under the playhead, firing `show_atom` events. To prevent these from being incorrectly broadcast as new selection changes (which forces the peer viewport to reload the single-clip view):

- We query the active viewport container via `viewport_active_media_container_atom()` to determine if a `Playlist` or a `Timeline` is currently shown in the viewport.
- We cache these boolean states as `self._viewport_container_is_playlist` and `self._viewport_container_is_timeline` inside the `_poll_and_broadcast_selection()` loop.
- In `_on_global_playhead_event()`, we check the cached `self._viewport_container_is_playlist` state to discard media changes when viewing a Timeline.
- We disable `playhead_selection` fallback inside `_poll_and_broadcast_selection()` for Timeline mode, ensuring only explicit clicks in the timeline track trigger selection broadcasts.

## Index-Based Timeline Sequence Polling

When checking for newly added clips on the master in `_poll_sequence_new_media()`, xStudio's `to_otio_string()` does not export custom `"sync"` metadata, meaning clips lack stable GUIDs.

- Do not compare clip names using a set (which ignores duplicate-named clips or end-of-timeline additions).
- Perform a sequential index-based alignment loop comparing `fresh_clips` from the xStudio export with the manager's `stored_clips`.
- Use a robust `_clips_match(c1, c2)` helper that compares clip names and target media URLs to identify insertions, additions, and deletions cleanly.

## Annotation trigger: `show_atom` + periodic fallback scan

`annotation_atom` events from the `AnnotationsUI` plugin events group do **not**
fire in the tested builds.  `show_atom` fires when a **new** bookmark is created,
but does **not** fire when the user adds a second stroke to an existing bookmark
on the same frame.

Therefore `_on_global_playhead_event` sets `_annotation_pending_time` when
`show_atom` arrives (fast path, ~250 ms debounce for new bookmarks), **and**
`_flush_pending_annotations` also runs a periodic fallback scan every
`ANNOTATION_SCAN_INTERVAL` (0.5 s) so that strokes added to existing bookmarks
are caught even when no event fires.

## `annotation_data` structure

`bm.annotation_data` returns:

```python
{"plugin_uuid": "…", "Data": {"pen_strokes": […], "captions": […], …}}
```

The canvas dict lives under `"Data"`, **not** at the top level:

```python
canvas = ann_data.get("Data", ann_data)   # fallback covers format changes
```

## Coordinate system: xStudio ↔ OTIO/RV

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

## Multiple strokes per frame — delta tracking

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

## Display state sync — xStudio

### Reading zoom and pan via `serialise_atom`

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

### xStudio zoom convention vs. RV

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

### "Pan" and "Zoom" module attributes are boolean toggles

`vp.get_attribute("Zoom")` and `vp.get_attribute("Pan")` return **boolean mode toggles** (enter/exit zoom-drag or pan-drag mode), defined as `add_boolean_attribute("Zoom", "Zm", false)` in `viewport.cpp`.  They are **not** the current pan/zoom position.  Do not use them to read or set viewport position.

### Writing zoom/pan — `deserialise_atom` crashes xStudio

`deserialise_atom` feeds the full viewport JSON back through `Viewport::deserialise`, which then reconstructs `ColourTriplet` and other complex C++ types from the JSON.  The round-trip through Python's `json.loads` does not preserve the type information those deserializers expect, causing a **signal 11 crash** inside `adl_serializer<ColourTriplet>::from_json`.

**Do not use `deserialise_atom` to write pan/zoom from Python.**

### Pan sync is disabled — coordinate system incompatibility

xStudio's `state_.translate_` (read via `serialise_atom`) is in internal image-space units that are **not** compatible with RV's normalised translation coordinates.  Sending the raw translate values to RV causes a ~50% pan jump when xStudio joins a session.

**`_read_xs_display_state` therefore always returns `pan: None`.**  RV skips null pan fields (see "None pan/zoom in the protocol" above), so no pan is applied in either direction.

### One-way zoom sync and the missing atoms

As a result: xStudio → RV zoom sync works (read via `serialise_atom`, broadcast), but **RV → xStudio zoom/pan sync is not possible** with the current Python API.

The proper fix is to expose `viewport_scale_atom` and `viewport_pan_atom` in `py_atoms.cpp` (two lines, both atoms already exist in `atoms.hpp` and are handled by the viewport actor).  `viewport_scale_atom` already takes/returns a plain `float`; `viewport_pan_atom` would need a `(float, float)` overload or an `Imath::V2f` binding.

### Lazy playhead initialisation

`current_playhead()` raises `RuntimeError: invalid_argument` if xStudio has no media loaded when the plugin connects.  The poll loop retries lazily:

```python
if not self.active_playhead:
    try:
        self.active_playhead = self.current_playhead()
    except Exception:
        return
```

## Receiving annotations from remote peers

Remote annotations arrive as `insert_child` events (action returned by
`manager.tick()`).  If the clip has `annotation_commands` in metadata,
`_apply_remote_annotation` converts the SyncEvent list to xStudio pen-stroke
dicts and calls `bm.set_annotation(strokes=…)` on the relevant bookmark.
`_annotation_bookmarks: dict[(clip_guid, frame), Bookmark]` caches the
bookmark so that subsequent `annotation_commands_added` events can update it
in place rather than creating a duplicate.
