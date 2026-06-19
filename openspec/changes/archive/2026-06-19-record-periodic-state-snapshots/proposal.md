## Why

The sync recorder is a passive tap: it captures the raw message *stream* but never reduces it into state. The only real state it ever holds is the **single** `STATE_SNAPSHOT` it requests at startup via the join handshake. After that it records events blindly — so when the `sync_test` framework replays a recording, the only expectation it can check is a frame number reconstructed from the last `PLAYBACK_SETTINGS SET` ([derive_checkpoints](../../../sync_test/python/sync_test/runner.py)), compared against each client's lightweight `/state` (`clip`, `frame`, `playing`).

That validation is blind to **structural desync**. A client that missed an `INSERT_CHILD` and holds the wrong timeline still reports the right frame and passes. The `reorder` recording proves the gap: two timelines with identical clips in *different order* — frame-only checkpoints cannot tell them apart, but the captured snapshot already encodes the difference.

Meanwhile the authoritative reduced state exists the whole time: the master maintains `playback_state`, `display_state`, and `_timelines`, and a real captured snapshot carries all of it (`playback_state.current_time.value` = frame, `display_state` = viewport, full `timelines` with tracks/clips, `active_timeline_guid`). We just stop asking for it after startup.

## What Changes

- **Recorder — hybrid periodic snapshot capture** (opt-in, for the test framework):
  - **Passive (always on):** record *any* `STATE_SNAPSHOT` seen on the fanout, not only those targeted at the recorder. Joins by real peers become free state captures.
  - **Active (opt-in via new param):** at a detected silence gap, re-issue `STATE_REQUEST` to the cached master GUID (skipping `WHO_IS_MASTER` after the initial handshake) to capture a snapshot at settle points, bounded by a minimum interval and a minimum-silence floor so a live review session is not perturbed.
- **Recording storage:** captured snapshots already land in the JSONL with their `time_offset`. The player must keep them as a **time-ordered list** while still exposing the *first* for the join handshake (today it overwrites to keep only the last).
- **Shared canonical state projection:** a single function that reduces a `StateSnapshot`-shaped dict to a comparable skeleton (timeline set by GUID, active timeline, per-timeline ordered clip GUIDs + normalized names, frame ± tolerance, display view/annotation toggles) and drops representation-only fields (media URLs, OCIO/color metadata, available ranges, timestamps). Co-located with the snapshot schema so the OpenRV and xStudio integrations agree on what "in sync" means.
- **Client full-state inspection:** the inspector exposes a `get_full_state` that returns the client manager's own `StateSnapshot`-shaped projection (the client already runs the same reducer), enabling snapshot-vs-snapshot comparison.
- **Test-side structural validation:** the runner derives **state checkpoints** from the recording's periodic snapshots and validates each live client's projected full state against the expected projection (GUID-keyed diff), in addition to the existing frame checkpoints.

## Capabilities

### New Capabilities
- `sync-recorder-state-capture`: Hybrid passive + active periodic `STATE_SNAPSHOT` capture in the recorder, the opt-in active-request trigger and perturbation bounds, and the time-ordered snapshot storage/replay contract.
- `sync-state-projection`: The canonical state-projection function and field rules (keep/normalize/drop) shared by record-side and replay-side, plus the GUID-keyed snapshot diff semantics.
- `sync-test-state-validation`: How the test runner derives state checkpoints from periodic snapshots, fetches each client's projected full state via the inspector, and reports structural desync.

### Modified Capabilities
<!-- The existing frame-based checkpoint validation is retained unchanged and runs alongside the new state checkpoints. -->

## Impact

- **Recorder** ([sync_recorder/recorder.py](../../../sync_recorder/recorder.py)): new `capture_periodic_state` opt-in, broadened snapshot watch, cached master GUID, silence/interval bounds.
- **Player** ([sync_recorder/player.py](../../../sync_recorder/player.py)): `load_recording` keeps a time-ordered snapshot list; first snapshot still answers the join handshake; mid-stream snapshots are **not** replayed as events (they are the expectation, not traffic).
- **Inspector** ([sync_test/python/sync_test/inspector.py](../../../sync_test/python/sync_test/inspector.py)) and the RV/xStudio hooks: a `get_full_state` callback returning the manager's projected snapshot.
- **Runner** ([sync_test/python/sync_test/runner.py](../../../sync_test/python/sync_test/runner.py)): `derive_state_checkpoints` + `validate_state_checkpoint` alongside the existing frame path.
- **Protocol**: no new message types; reuses `STATE_REQUEST` / `STATE_SNAPSHOT`.
- **Compatibility**: default behaviour is unchanged (active capture off); existing recordings without periodic snapshots still validate via frame checkpoints.
