## Why

Isolating a clip in OpenRV can be undone ~150ms later by the host, which pulls the view back to the sequence. Observed 2026-08-10 with `seq_B`: it appeared, then vanished and was replaced.

Two defects compose into it, and each is wrong on its own:

1. **OpenRV broadcasts the view it was showing a moment ago.** A frame-changed broadcast can win the race against the view-change handler, which is what updates `_cur_view_mode`/`_cur_clip_guid`. The log shows the one-millisecond gap:

   ```
   11:20:50.963 SEND playback ... view=sourceGroup000004 tl=- displayed=source mode=sequence clip=
   11:20:50.964 view-change sourceGroup000004 (RVSourceGroup)
   ```

   `displayed=source` is what OpenRV was showing; `mode=sequence` is what it told the session. As a follower its view fields are stripped, so only the position escapes — but as host it would ship an actively wrong view instruction.

2. **xStudio re-broadcasts a remote-induced selection as a local one.** Applying that position moved xStudio's playhead, which fired a `show_atom`. xStudio already recognises the pattern and tags it — `[PROVENANCE remote-induced? source=f9dcd756 PLAYBACK_SETTINGS_1.0/SET settling+0.05s age=0.05s]` — and then broadcasts it anyway as a local selection. OpenRV obeys, and the user's isolation is gone.

The second is the one that moved the user's view, and xStudio has already computed the signal needed to prevent it.

## What Changes

- OpenRV: resolve the broadcast `view_mode`/`clip_guid` from the view being displayed at send time, so a broadcast can never describe a view the application has already left.
- xStudio: do not broadcast a selection change as a local action when its own provenance tracking attributes it to a remote apply it just performed.
- Both: keep the existing behaviour for genuine local view changes — this narrows what counts as local, it does not stop reporting local isolations.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `openrv-sync-plugin`: a broadcast's `view_mode`/`clip_guid` must describe the view being displayed when the message is sent, not the last view the plugin recorded.
- `xstudio-clip-selection-sync`: a selection change attributed to a remote apply is not re-broadcast as a local selection.

## Impact

- **Code**: `rvplugin/ori_sync/playback_sync.py` (`_broadcast_playback`); `xstudio_plugin/ori_sync/playback_sync.py` (the `[SEL]` show_atom path that already computes provenance).
- **Risk**: suppressing too much would stop a genuine local isolation from reaching peers — the failure mode this must not introduce, and what the scenarios guard.
- **Prior work**: surfaced while verifying `fix-source-view-timeline-guid`, which fixed the *timeline* label on the same message. The `displayed=` field that made defect 1 visible was added there.
