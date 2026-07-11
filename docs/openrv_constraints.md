# RV plugin — non-obvious constraints

## Frame numbering

RV uses **1-based** frame numbers. OTIO track time is **0-based**.

- On broadcast: `value = current_frame - 1`
- On apply: `target_frame = int(protocol_value) + 1`

## Getting clip duration from RV

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

## `rv.commands.fps()` returns 24 at init time

The session fps is not reliably correct until media headers are fully read. Always read the media's own fps from `media.fps` on the `RVFileSource` node:

```python
media_fps = rv.commands.getFloatProperty(f"{file_source_node}.media.fps")[0]
if media_fps and media_fps > 0:
    fps = media_fps
```

## Display state sync — RV

The plugin broadcasts and applies `DISPLAY_SETTINGS` messages containing `pan`, `zoom`, `exposure`, and `channel`.  Several non-obvious constraints apply.

### The two `RVDisplayColor` nodes

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

### Channel isolation — `channelFlood`, not `channelOrder`

The r/g/b/a key bindings change `color.channelFlood` (int), **not** `color.channelOrder` (string, used for channel reordering permutations like GBRA):

```python
_RV_FLOOD_TO_CH = {0: "RGBA", 1: "R", 2: "G", 3: "B", 4: "A"}
_RV_CH_TO_FLOOD = {"RGBA": 0, "R": 1, "G": 2, "B": 3, "A": 4}
```

### Pan and zoom — `rv.extra_commands`, not node properties

Pan and zoom are viewer-level transforms, not properties on any DAG node.  Use:

```python
import rv.extra_commands
zoom = rv.extra_commands.scale()           # float, 1.0 = fit-to-window
rv.extra_commands.setScale(float(zoom))
pan  = rv.extra_commands.translation()    # plain tuple (x, y)
rv.extra_commands.setTranslation((x, y))  # plain tuple — rv.rvtypes.Point does NOT exist
```

`RVDisplayGroup` has no transform component; attempting to set transform2D properties on it raises `invalid property name`.

### Exposure — per-source `RVColor` node, 3-element array

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

### `None` pan/zoom in the protocol

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

## Annotation persistence must happen on all peers

In `manager.py` `_process_message`, `_persist_annotation_to_timeline` must be called for **all** received `ANNOTATION` messages, not just when `self.is_master`. The master persists its own strokes inside `broadcast_annotation` (before sending), so there is no double-persist: self-sent messages are filtered by `source_guid` in `RabbitMQNetwork` before reaching `_process_message`.

## Building and installing the RV plugin

```bash
cd rvplugin/ori_sync
bash reinstall.csh          # produces otiosyncdemo-0.1.rvpkg
```

`makepackage.csh` vendors `pika` (from `~/.pyenv/…/site-packages/pika`) and zips `plugin.py`, `PACKAGE`, `pika/`, and `otio_sync_core/` into the `.rvpkg`. After rebuilding you must reinstall the package in OpenRV's Package Manager and **restart RV**.

The `otio_sync_core` library bundled inside the `.rvpkg` is a **copy** of `python/otio_sync_core/`. Any change to the library files requires a package rebuild.

Logs are written to `rvplugin/ori_sync/host.log` (set `ORI_SYNC_LOG_FILE` env var to the desired path, or see `_make_otio_logger` in `plugin.py`).

## Annotation deletion: `clear-paint` / `clear-all-paint` internal events

RV's Annotate mode ("Clear Frame" / "Clear All Frames on Timeline") does **not** go through `graph-state-change` in a way `annotation_sync.on_graph_state_change` can see — it soft-deletes strokes (`softDeleted = 1` + empties the frame's `.order` property) and then fires a dedicated internal event via `sendInternalEvent`, bound the same way any other RV event is (`self.init([("clear-paint", ...), ...])`):

```python
("clear-paint", self.annotation.on_clear_paint, "Broadcast Annotation Clear"),
("clear-all-paint", self.annotation.on_clear_paint, "Broadcast Annotation Clear All"),
```

Both events carry the **identical payload shape** — `event.contents()` is a pipe-joined list of the deleted stroke/text/shape uuids only (`"uuid1|uuid2|..."`), regardless of how many frames or sources were affected. There is no node or frame name in the payload; resolve each uuid against the local `sync_manager`'s Annotations tracks instead (`annotation_clip_guid_for_stroke_uuid`) rather than trying to re-derive RV's frame/node context.

### The empty-REPLACE trap

Broadcasting a clip's surviving (possibly empty) `annotation_commands` via the existing `REPLACE_ANNOTATION_COMMANDS` message reuses `_apply_annotation_replace` on the receive side — but `rv_paint_applier.apply_specs`'s reconcile mode infers *which kinds it may prune* from what's actually present in the incoming spec list. An **empty** spec list is read as "no opinion, prune nothing" (this is deliberate — a text-only edit must not be read as "delete every pen stroke"), so a full clear silently does nothing if routed through the normal reconcile path. `_apply_annotation_replace` special-cases a fully-empty incoming `annotation_commands` list by wiping the frame's `order` property directly, bypassing `apply_specs` entirely for that case.

### RV pen strokes need their own uuid persisted onto RV's `.uuid` property

RV's native annotate_mode auto-assigns *some* `.uuid` to every pen stroke (used for its own undo bookkeeping), completely independent of the uuid this plugin broadcasts on the wire. Historically nothing needed to read that property back, so the send path never wrote its own uuid there for pens (unlike text/shape, which always did) — the two identifier spaces were simply disjoint. `clear-paint`/`clear-all-paint` report *RV's* uuid, so without writing our broadcast uuid onto the same property, `annotation_clip_guid_for_stroke_uuid` can never resolve a deleted pen stroke back to its OTIO clip. Both `_construct_annotation_events` (send path) and `_apply_annotation` (receive path, for strokes that originated remotely) now persist the broadcast uuid onto `<node>.<pen component>.uuid`, mirroring what text/shape already did. Overwriting RV's own auto-assigned value here is safe — RV's undo bookkeeping only cares that *some* uuid is present, not whose.

### A single stroke gesture can mint several different uuids if you're not careful

Two separate traps discovered here, both silent before this change (since nothing used to read the uuid back) and both now fixed in `on_graph_state_change`'s pen-tracking (the `is_pen` branch):

1. **Per-tick uuid churn.** The pre-existing code consumed `_next_stroke_uuid` (set once at `paint.nextId`) on the *first* `.points` change of a gesture, then fell back to `str(uuid.uuid4())` — a fresh random uuid — on every subsequent tick of the *same* drag, since `_next_stroke_uuid` was already `None`. Harmless when nothing persisted it; now that RV's `.uuid` property is written on every broadcast, an intermediate tick's uuid could get permanently committed as a phantom duplicate "stroke" the moment a later tick overwrote RV's property with a newer value — unresolvable by any future clear. Fixed: reuse `_pending_stroke`'s existing uuid when it matches the same `(node, component)` instead of minting a new one.
2. **Premature pen-up mid-gesture.** Even with (1) fixed, RV's own pen-up detection (bound to `pointer-1--release`/`pointer--leave`/`pointer--control--leave`) was observed firing *mid-drag* on the same pen component — splitting what the user experiences as one continuous stroke into two separately-committed uuids (`_flush_pending_stroke` resets `_pending_stroke` to `None`, so the next tick sees no match and, without further care, mints a fresh uuid again). Fixed: when `_pending_stroke` doesn't match, check RV's own `<node>.<component>.uuid` property first and reuse it if already set — only fall back to a genuinely new uuid when the component has never been broadcast at all.

Both are required together: multi-stroke, multi-frame "Clear All Frames on Timeline" tests failed with only fix (1) applied (some strokes still had two different uuids across their lifetime), and passed once (2) was added too.

## Annotation visibility: `paint.show` is per-source, synced as global

RV's "Show Drawings" toggle is `<RVPaint node>.paint.show` — scoped to one media source, not the whole session. It's synced as a single `annotations_visible` field in the shared `display_settings` blob (same broadcast path as exposure/channel), and on receive is applied to **every** `RVPaint` node, not just the one that changed — this is a deliberate scope-widening to match xStudio's own toggle, which is session-wide, not per-source. See `annotation-lifecycle-sync` capability spec for the accepted tradeoff.

Reading the *current* value back (`_read_annotations_visible`) must resolve the specific currently-viewed `RVPaint` node via `rv.commands.metaEvaluateClosestByType(rv.commands.frame(), "RVPaint")` — the same resolution `_find_paint_node_for_media` uses elsewhere — rather than scanning `nodesOfType("RVPaint")` for "any node that has the property set." Once a remote peer's broadcast has been applied session-wide (every node gets `.paint.show` written), multiple nodes can hold different values simultaneously; picking an arbitrary one that happens to have the property set can silently read a stale value instead of the node the local user is actually toggling, making `_broadcast_display_state`'s change-detection never fire.
