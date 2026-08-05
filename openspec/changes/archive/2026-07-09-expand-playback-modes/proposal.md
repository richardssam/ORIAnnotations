## Why

`PLAYBACK_SETTINGS_1.0` currently syncs playback mode as a `looping: bool`, but both native engines already support a third mode neither side of the sync layer exposes: OpenRV has `PlayLoop`/`PlayOnce`/`PlayPingPong` (`rv.commands.playMode()`), and xStudio's playhead has an equivalent native `"Loop Mode"` attribute with values `"Play Once"`/`"Loop"`/`"Ping Pong"`. Both engines already bounce forward/backward on their own once set to ping-pong/rock-and-roll — nothing needs to be built to make playback actually reverse. The only gap is that the sync protocol flattens this pre-existing 3-way native concept into a bool, so a peer can never be told "ping-pong."

## What Changes

- **BREAKING**: `PlaybackSettingsSet.looping: bool | None` is replaced by `playback_mode: str | None` with values `"play-once"`, `"loop"`, `"ping-pong"`. No dual-read compatibility shim — this is a clean break.
- OpenRV's `playback_sync.py` maps `rv.commands.playMode()` (0/1/2) to/from the three wire strings directly, instead of collapsing to a bool.
- xStudio's `playback_sync.py`'s `_get_loop_mode()` maps its native `"Loop Mode"` attribute string directly to the three wire strings, instead of collapsing to a bool.
- Remove the `on_rv_play_stop` restart-on-boundary fallback hack in `rvplugin/ori_sync/plugin.py` (added in commit `d18ec21` as insurance around the original loop-mode fix) and re-verify loop behavior still works relying solely on `setPlayMode` — this hack predates any of the three-way work and its necessity was never re-confirmed after `setPlayMode` started being applied on receipt.
- Add a one-off migration script to update the 18 existing `.jsonl` test recordings that embed the literal `"looping": true/false` key (in both standalone `PLAYBACK_SETTINGS_1.0` messages and `STATE_SNAPSHOT.playback_state`) to the new `"playback_mode"` key — every existing recorded value maps unambiguously to `"loop"` or `"play-once"` (no recording ever captured ping-pong, since it didn't exist on the wire).
- Update `tests/otio_sync/test_protocol_messages.py`'s `"looping": False` fixture to the new field.

## Capabilities

### New Capabilities
(none — this extends an existing wire message, not a new capability)

### Modified Capabilities
- `otio-sync-core`: "Settings Messages Declare Fields but Tolerate Extras" enumerates `PLAYBACK_SETTINGS_1.0`'s known fields as `playing`, `current_time`, `looping`, `timeline_guid`, `sync_timestamp` — `looping` is replaced by `playback_mode` (`"play-once"` | `"loop"` | `"ping-pong"`).

## Impact

- `python/otio_sync_core/protocol_messages.py`: `PlaybackSettingsSet` field rename + value semantics change.
- `python/otio_sync_core/manager.py`: docstrings referencing `looping`.
- `rvplugin/ori_sync/playback_sync.py`: send/receive mapping to `rv.commands.playMode()`/`setPlayMode()`.
- `rvplugin/ori_sync/plugin.py`: remove the `on_rv_play_stop` restart fallback; `_cur_looping` state tracking becomes `_cur_playback_mode` (or equivalent).
- `xstudio_plugin/ori_sync/playback_sync.py`: `_get_loop_mode()` becomes a 3-way string mapper instead of a bool check.
- `tests/otio_sync/test_protocol_messages.py`: fixture update.
- `sync_test/recordings/*.jsonl` (18 files): migrated via a one-off script, not hand-edited.
- Not touched: the vestigial OTIO `SyncEvent.SyncPlayback` schemadef (`otio_event_plugin/schemadefs/SyncEvent.py`) — confirmed unreferenced anywhere else in the codebase, out of scope for this change.
