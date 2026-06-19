## Context

The `sync_test` framework replays a recorded session against live OpenRV/xStudio clients and checks they stay in sync. Today the only expectation it can validate is a frame number reconstructed from `PLAYBACK_SETTINGS SET` events, compared against each client's lightweight `/state`. This catches frame drift but is blind to structural desync (wrong clip set, wrong order, missed insert/remove).

Key facts established during exploration:
- The recorder ([recorder.py](../../../sync_recorder/recorder.py)) is passive. It requests **one** `STATE_SNAPSHOT` at startup via `WHO_IS_MASTER → STATE_REQUEST → STATE_SNAPSHOT` (gated by `_snapshot_captured`) and never reduces state again.
- A real captured snapshot is rich. From `sync_test/recordings/reorder.jsonl`: `playback_state = {playing, current_time:{value:79, rate:24}, timeline_guid}`, `display_state = {pan, zoom, exposure, channel}`, `active_timeline_guid`, and full `timelines` with `tracks → children` clips (including an `Annotations` track). Two timelines there share clips in **different order** — exactly the desync frame checks miss.
- The master maintains this state live: `self.playback_state` ([manager.py:1253](../../../python/otio_sync_core/manager.py)), `self.display_state` ([manager.py:1274](../../../python/otio_sync_core/manager.py)), `_timelines`. Snapshots are *targeted* (`target_guid`), broadcast on the fanout — so a snapshot for any joiner is visible to the recorder.
- **Every client runs the same reducer.** A joining peer's manager builds `_timelines` + `playback_state` + `display_state` from the snapshot and subsequent deltas. So a client can emit the *same* `StateSnapshot` shape the master does — making validation symmetric (snapshot-vs-snapshot).
- **GUIDs are stable across record→replay.** The player seeds clients by replaying the recorded snapshot ([player.py `_process_network_requests`](../../../sync_recorder/player.py)) preserving `metadata.sync.guid`; later `INSERT_CHILD`s carry their GUIDs verbatim. So the diff can be GUID-keyed, not name/position-keyed.
- The player today keeps only the **last** snapshot: `load_recording` does `self._recorded_snapshot = envelope` for every `STATE_SNAPSHOT` line, overwriting.

## Goals / Non-Goals

**Goals:**
- Capture authoritative master state at settle points during recording, opt-in and bounded so a live session is not perturbed.
- Validate live clients against that state structurally (timeline set, active timeline, clip order, frame, display) — not just frame.
- Define one canonical projection shared by record-side and replay-side so RV and xStudio agree on "in sync."
- Leave default recorder/replay behaviour unchanged.

**Non-Goals:**
- Replaying mid-stream snapshots as session traffic (they are the expectation, not events).
- Reimplementing the manager's reducer inside the recorder (we ask the process that already knows).
- Byte-exact OTIO equality (cross-app representation differs legitimately).
- Pixel/visual comparison; media-content validation.

## Decisions

### Hybrid capture: passive-heavy, active as backstop
Passive capture (record any `STATE_SNAPSHOT` on the wire) is free and never perturbs the session, but only fires when a peer happens to join. Active re-request fires at the settle points we care about but injects traffic into the live session and pulls a potentially large payload (the `reorder` snapshot is 3 full timelines). So:
- Passive capture is **always on** once recording starts.
- Active capture is **opt-in** via `capture_periodic_state` and only fires after `min_silence` seconds of true stream silence, never more often than `min_interval`, and is suppressed if a passive snapshot arrived within the window. The recorder caches the master GUID from the initial handshake and sends `STATE_REQUEST` directly, re-discovering via `WHO_IS_MASTER` only on timeout.

Rationale: the trigger predicate ("a position followed by ≥ delay of silence") is the *same* one `derive_checkpoints` already uses to pick checkpoints — so snapshots land exactly where a checkpoint would.

### Snapshots are expectations, not replayed traffic
The player keeps a **time-ordered list** of recorded snapshots. The *first* still answers joiners' `STATE_REQUEST` during replay (unchanged seeding behaviour). Mid-stream snapshots are never sent as events — they are read only by the validator, keyed by `time_offset`.

### One canonical projection, co-located with the snapshot schema
The diff is **not** raw OTIO equality — that would false-positive on every benign cross-app difference (per-machine media URLs, OCIO/color metadata, available ranges, timestamps), which is why the existing `compare_states` already ignores `{playing, media_path, media_exists, frame}` and normalizes clip names. Instead a single `project_state(snapshot) -> CanonicalState` reduces both sides to:
- timeline set keyed by GUID; `active_timeline_guid`
- per timeline: ordered list of `(clip_guid, normalized_name)` per track, track identity by name/role
- frame from `playback_state.current_time.value` (compared with tolerance), `playing` dropped
- display: view/display + annotation toggles; pan/zoom/exposure dropped (device-centric, per the color-output-is-a-hint precedent)
- dropped entirely: media `target_url`, color/OCIO metadata, `available_range`, all `*timestamp*`

It lives with the `StateSnapshot` definition (near [protocol_messages.py](../../../python/otio_sync_core/protocol_messages.py)) so both the recorder/runner and the client integrations import the same rules. The keep/normalize/drop list **is** the definition of "in sync" and is the real deliverable.

### Symmetric comparison via the client's own reducer
The inspector gains `get_full_state` returning `project_state` over the client manager's current snapshot. Validation then supports both:
- **oracle**: client projection vs recorded-snapshot projection at `t_offset` (is it the *right* state)
- **consensus**: client-vs-client projections (did everyone converge)

GUID stability makes the diff a keyed structural comparison rather than a fuzzy match.

## Risks / Trade-offs

- **Perturbation budget.** Active requests inject `STATE_REQUEST` and pull full snapshots. Mitigation: `min_silence` floor + `min_interval` ceiling + passive-arrival suppression; default off.
- **Cross-app false positives.** RV and xStudio represent timelines differently. Mitigation: the projection's drop/normalize list; start strict on structure (GUIDs, order) and lenient on representation, tune against real recordings.
- **Frame availability.** `playback_state.frame` is present only if the host app passed its playback state into `send_state_snapshot`. Verified present in current recordings; the validator treats a missing frame as "not asserted," same as today.
- **Projection drift.** If RV and xStudio integrations diverge on the projection, consensus breaks. Mitigation: single shared function, no per-app copies.

## Migration / Compatibility

Default recorder behaviour is unchanged (active capture off). Recordings without periodic snapshots validate via the existing frame checkpoints. No protocol message is added; older peers are unaffected.

## Open Questions

- Exact defaults for `min_silence` / `min_interval` (start ~1.5s / ~5s, tune against recordings).
- Whether annotation *content* (stroke geometry) belongs in the projection or stays out of v1 (lean: out — assert presence/count, not geometry).
- Track-identity key when names collide (two `Default` timelines exist in `reorder.jsonl`) — disambiguate by timeline GUID, already keyed.
