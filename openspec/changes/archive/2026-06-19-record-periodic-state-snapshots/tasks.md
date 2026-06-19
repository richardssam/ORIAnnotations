# Tasks

## 1. Canonical state projection (sync-state-projection)
- [x] 1.1 Implement `project_state(snapshot_dict) -> CanonicalState`: timelines keyed by GUID, active timeline, per-track ordered `(clip_guid, normalized_name)`, frame from `playback_state.current_time.value`, display view/display + annotation toggles. Co-locate with the `StateSnapshot` schema so client integrations can import it. _(otio_sync_core/state_projection.py; annotations captured as the Annotations track's clips)_
- [x] 1.2 Implement the drop/normalize rules: drop `target_url`, color/OCIO metadata, `available_range`, `*timestamp*`, pan/zoom/exposure; exclude `playing` from equality; reuse the existing clip-name normalization. _(projection reads only kept fields, so dropped data is never consulted)_
- [x] 1.3 Implement `diff_states(expected, actual, frame_tolerance)`: GUID-keyed; report missing/extra timeline, missing/extra clip, reorder, active-timeline mismatch, frame-beyond-tolerance; treat a missing frame as "not asserted."
- [x] 1.4 Unit tests using `sync_test/recordings/reorder.jsonl` snapshot as fixtures (equal-vs-reordered, dropped representation fields, unasserted frame). _(tests/otio_sync/test_state_projection.py — 8 tests pass)_

## 2. Recorder hybrid capture (sync-recorder-state-capture)
- [x] 2.1 Broaden the `tick()` snapshot watch to record any `STATE_SNAPSHOT` (any `target_guid`) as an event; keep the initial-handshake snapshot satisfying `_snapshot_captured`. _(tick already records every received payload; added explicit any-target snapshot-arrival tracking)_
- [x] 2.2 Add `capture_periodic_state` (default off) plus `min_silence` / `min_interval` params; track last-message time and last-active-request time.
- [x] 2.3 Active trigger: on silence ≥ `min_silence`, interval ≥ `min_interval`, and no passive snapshot in the window, send `STATE_REQUEST` to the cached master GUID. _(`_drive_periodic_capture`; verified with a fake-network driver)_
- [x] 2.4 Cache master GUID from the initial handshake; re-issue `WHO_IS_MASTER` only on active-request timeout. _(cached from any I_AM_MASTER; timeout → drop cache + rediscover)_
- [x] 2.5 Wire `--periodic-state` (and threshold flags) into the recorder CLI `main()`.

## 3. Player snapshot storage (sync-test-state-validation)
- [x] 3.1 `load_recording`: retain all `STATE_SNAPSHOT` events as a `time_offset`-ordered list; keep the first as the join-handshake answer (replace the current overwrite). _(verified on reorder.jsonl + missing_media.jsonl)_
- [x] 3.2 Confirm mid-stream snapshots are never sent as playback events. _(snapshots `continue` past `self.events`; verified no STATE_SNAPSHOT leaks into events)_

## 4. Client full-state inspection (sync-test-state-validation)
- [x] 4.1 Add a `get_full_state` callback to the inspector returning the manager's `StateSnapshot`-shaped dict. _(InspectionServer `/full_state` route + `get_full_state_callback`; added `SyncManager.export_state()` as the network-free source — verified `export_state()` → `project_state()` round-trip)_
- [x] 4.2 Wire `get_full_state` through the OpenRV hook. _(in-process: plugin calls `register_manager`; hook's `get_openrv_full_state` returns `manager.export_state()` on RV's main thread)_
- [x] 4.3 Wire `get_full_state` through the xStudio hook (bound any stale-actor reads per the request_receive timeout note). _(xStudio plugin has NO SyncManager — reconstructs StateSnapshot shape from xStudio's own `timeline_to_otio_string` export with bounded reads. Best-effort; needs live xStudio verification, incl. whether per-clip sync GUIDs survive the OTIO export)_

## 5. Runner structural validation (sync-test-state-validation)
- [x] 5.1 `derive_state_checkpoints(jsonl)`: one checkpoint per periodic snapshot, carrying its `time_offset` and projected expectation. _(verified on reorder.jsonl)_
- [x] 5.2 `validate_state_checkpoint(...)`: fetch each client's full state, project, diff against expectation; fail with a readable report. _(plus `fetch_full_state` hitting `/full_state`; skips error/unsupported apps)_
- [x] 5.3 Integrate state checkpoints into `run_test` alongside frame checkpoints; fall back cleanly when a recording has no periodic snapshots. _(empty `state_checkpoints` ⇒ block is a no-op)_
- [x] 5.4 Optional client-vs-client consensus check over projections. _(`compare_full_states`; runs after the oracle check passes — verified it catches reorder divergence)_

## 6. Validation & docs
- [~] 6.1 Re-record one existing test (e.g. `reorder`) with `--periodic-state` and confirm state checkpoints catch an injected desync (e.g. drop an `INSERT_CHILD` on one client). _(Deterministic stand-in landed: tests/otio_sync/test_state_validation_integration.py drives recording → derive → validate, matching client passes, dropped-clip client fails with "missing clip", consensus catches reorder. LIVE re-record against RV/xStudio + broker still needed to confirm the real `/full_state` plumbing end-to-end.)_
- [~] 6.2 Confirm a live review session is not perturbed: measure active `STATE_REQUEST` count stays within the `min_interval` bound. _(Deterministic stand-in landed: tests/otio_sync/test_recorder_periodic.py asserts no requests during continuous activity, rate-limiting by min_interval, and none when default-off. LIVE measurement against a real session still pending.)_
- [x] 6.3 Document the projection's keep/normalize/drop rules so RV and xStudio integrations stay aligned. _(docs/sync_state_projection.md; module docstring is the canonical in-code reference.)_
