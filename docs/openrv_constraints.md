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
