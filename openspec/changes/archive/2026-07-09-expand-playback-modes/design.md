## Context

`PLAYBACK_SETTINGS_1.0`'s `looping: bool` is synced between OpenRV and xStudio via `python/otio_sync_core/protocol_messages.py::PlaybackSettingsSet`. Both native engines actually implement a 3-way play mode, not a bool, and both already know how to bounce/reverse on their own once told to:

- **OpenRV**: `rv.commands.playMode()` / `setPlayMode()` take `PlayLoop=0`, `PlayOnce=1`, `PlayPingPong=2` (`src/lib/app/py_rvui/rv_commands_setup.py`).
- **xStudio**: the playhead's native `"Loop Mode"` string attribute takes `"Play Once"`, `"Loop"`, `"Ping Pong"` (`include/xstudio/playhead/enums.hpp`: `LM_PLAY_ONCE`, `LM_LOOP`, `LM_PING_PONG`; string map in `include/xstudio/playhead/playhead.hpp`).

Today, both sides' `playback_sync.py` collapse their native 3-way value down to a bool (`== 0`/`== "Loop"`) before broadcasting, and expand the bool back to only two of the three native values on receive (`0 if looping else 1`). Ping-pong is unreachable over the wire even though both engines support it natively and reverse-play requires no new engine-level work.

Separately, `rvplugin/ori_sync/plugin.py::on_rv_play_stop` has a restart-on-boundary fallback (added in `d18ec21`, the same commit that originally fixed xStudio's hardcoded `looping: False`): if the peer said to loop and RV's frame is at `frameEnd()`, it manually calls `play()` again rather than trusting RV's own native loop to continue. Its necessity has not been re-verified since `setPlayMode()` started being applied on every receive — it may have been compensating for a transient issue at the time rather than a fundamental gap in RV's native loop.

## Goals / Non-Goals

**Goals:**
- Make `playback_mode: "play-once" | "loop" | "ping-pong"` the wire representation, replacing `looping: bool`, so ping-pong is reachable and each host's native mapping is a direct 3-way translation instead of a lossy 2-way one.
- Remove the `on_rv_play_stop` restart fallback and empirically re-verify that native `setPlayMode()` alone is sufficient for loop (and ping-pong) to continue correctly across a peer-driven session.
- Migrate the 18 existing `.jsonl` test recordings that embed `"looping"` to the new field via a one-off script, so existing `sync_test` recordings keep working without hand-editing.

**Non-Goals:**
- Building any bounce/reverse-direction logic ourselves — both native engines already do this once set to their respective ping-pong mode. This change is purely about not losing that information on the wire.
- A backward-compatible dual-read shim for `looping` — this is an intentional clean break (dev-only protocol, no external consumers to preserve).
- Touching the vestigial OTIO `SyncEvent.SyncPlayback` schemadef (`otio_event_plugin/schemadefs/SyncEvent.py`) — confirmed unreferenced anywhere else in the codebase; a separate, currently-dead schema.
- Surfacing playback mode in `sync_test`'s `/state` RPC inspectors — neither `openrv_hook.py` nor `xstudio_hook.py` currently exposes loop state at all, so there's no existing test coverage to preserve. Adding that coverage would be a reasonable follow-up but isn't required to ship this change.

## Decisions

### D1: Wire values are `"play-once"`, `"loop"`, `"ping-pong"` (lowercase-hyphenated)

Matches the existing `view_mode: "sequence" | "source"` convention already used elsewhere in `PLAYBACK_SETTINGS_1.0`, rather than mirroring xStudio's own human-cased native strings (`"Play Once"`, `"Loop"`, `"Ping Pong"`) or RV's enum names (`PlayLoop`, etc.) verbatim. Each host's `playback_sync.py` owns its own translation table between these wire values and its native representation — this file, not a shared central table, is where mapping duplication should live, mirroring how each host already owns its own unit-conversion constants (`RV_WIDTH_SCALE`, xStudio's `aspect_half`, etc.).

*Alternative considered*: use xStudio's own attribute strings as the wire values directly, avoiding a translation table on that side. Rejected — RV would still need a translation table either way (its native representation is an int enum, not these strings), so there's no side that avoids translation entirely, and matching the existing `view_mode` naming convention keeps the protocol's own vocabulary consistent.

### D2: Clean break, no dual-read shim

`PlaybackSettingsSet.from_payload` will only read `playback_mode`; a message carrying the old `looping` key alone will simply leave `playback_mode` as `None` (falling through to whatever default the receiving side's `apply_playback_state` uses when the field is absent — same as any other unset field today). This is acceptable because:
- The protocol has no version negotiation and no external consumers outside this repo.
- The only concrete carriers of the old field are 18 test recordings and one test fixture, both fixed by the migration script / a one-line edit.

*Alternative considered*: accept both `looping` and `playback_mode` in `from_payload`, translating `looping` to `"loop"`/`"play-once"` when `playback_mode` is absent. Rejected per explicit user preference — not worth the permanent complexity for a two-commit-old test fixture set.

### D3: Migration script, not hand-edited recordings

A short one-off script walks each `sync_test/recordings/*.jsonl`, and for every JSON object containing a literal `"looping": true` or `"looping": false`, replaces that key with `"playback_mode": "loop"` or `"playback_mode": "play-once"` respectively (both in top-level `PLAYBACK_SETTINGS_1.0` `SET` payloads and nested `STATE_SNAPSHOT.playback_state` objects). No recording ever captured ping-pong (it didn't exist on the wire when they were recorded), so every existing value maps unambiguously — there is no ambiguous case requiring a judgment call.

### D4: Remove the restart hack now, re-verify empirically during implementation

The hack is removed as part of this change (not left in place "just in case"), and task-level verification must actually exercise loop *and* ping-pong across a live peer session before this change is considered done. If removing it reveals loop no longer continues reliably, that's new information about `setPlayMode()`'s behavior worth understanding on its own terms — not a reason to silently re-add the old bool-shaped hack, since it can't be adapted to ping-pong as written (it unconditionally treats reaching `frameEnd()` as "restart from the top," which is only correct for loop, not ping-pong's "reverse direction").

## Risks / Trade-offs

- **[Removing the hack could regress loop playback if `setPlayMode()` alone isn't sufficient]** → Mitigated by explicit live verification (loop *and* ping-pong, not just loop) as a task before this change ships, per D4. If a gap is found, the fix should address the actual mechanism (e.g. timing of when `setPlayMode` is applied relative to RV's own boundary check), not reintroduce a mode-specific manual restart.
- **[Migration script could mis-transform a recording if `"looping"` appears in an unexpected shape]** → All 18 occurrences found so far are plain JSON booleans in the two known payload shapes (D3). The script should assert it only ever replaces `"looping": true/false` literals and fail loudly (not silently skip) if it encounters `"looping"` in any other form, so a discrepancy is caught rather than producing a silently-wrong recording.
- **[Clean break means any recording made between now and the migration script running is invalid]** → Low risk in practice; recordings are regenerated routinely as part of normal `sync_test` development, and the migration script is part of this same change.
