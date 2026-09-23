## Why

When the xStudio host double-clicks a sequence while viewing the bin, the client doesn't follow. It gets told to isolate a single clip instead, then ignores the host's scrubs because their `timeline_guid` doesn't match. This was observed live on 2026-09-23 (`xstudio_host.log` / `xstudio_client.log`, 10:49:50). There are two defects:

1. `handle_show_atom_media` decides `view_mode` from `_viewport_container_is_playlist` / `_viewport_container_is_timeline`. Only the selection poll (`resolve_and_broadcast_selection`) refreshes those flags. The `show_atom` for the newly opened sequence was processed 28 ms *before* the next poll, so it saw the bin's stale `container=playlist`. It then broadcast `view_mode: "source"` for the clip under the playhead (car `4485d9f2`, forced to frame 0, looping).
2. When the poll then saw `container=Timeline`, it had no path to announce a sequence view. It broadcasts `view_mode: "sequence"` only on a Pinned Source Mode False→True transition, and PSM stays True when you go from the bin to a sequence. Every later `show_atom` was correctly labelled `timeline`, but each was suppressed as "playing through sequence". The wrong view was never corrected.

## What Changes

- The viewed-container type (playlist vs timeline) is refreshed when the viewport binds a new playhead, which is the first event of a container switch. It is no longer refreshed only by the selection poll, so the `show_atom` that follows sees the current container.
- The selection poll broadcasts `view_mode: "sequence"` when the viewed container changes to a Timeline while Pinned Source Mode is True and this peer isn't already in sequence view. The broadcast goes through the same echo check as the existing PSM True branch. This covers container switches that no PSM transition reports, and backstops a `show_atom` that was missed or suppressed.
- No wire or protocol change. Receivers already switch on a real `view_mode`/`timeline_guid` transition.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `xstudio-clip-selection-sync`: adds a requirement that opening a sequence in xStudio (bin → Timeline container switch) is broadcast as `view_mode: "sequence"` for that timeline, not as a clip isolation, even though Pinned Source Mode doesn't change.

## Impact

- `xstudio_plugin/ori_sync/playback_sync.py`: `handle_viewport_playhead`, `resolve_and_broadcast_selection`, and possibly a small helper that classifies a container.
- xStudio host → any peer (xStudio or OpenRV) receiving the view. There are no OpenRV plugin or `otio_sync_core` changes.
- Related to the parked unpublished-sequence-view issue (`get_local_viewed_timeline_guid` falling back to a local container uuid), but distinct from it. Here the sequence *was* published; the view announcement was wrong.
