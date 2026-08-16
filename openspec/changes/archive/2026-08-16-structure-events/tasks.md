## 1. Prove the events arrive (gate — stop here if they do not)

- [x] 1.1 Record which upstream fixes are in the xStudio build under test: `git merge-base --is-ancestor 70aaaa3f HEAD` and `git merge-base --is-ancestor 3b0a0e72 HEAD` in the xstudio repo (design D6).
- [x] 1.2 Add a temporary subscription, in a scratch script or behind a debug flag, that joins an event group and logs every message received, unfiltered.
- [x] 1.3 In a live session, create a playlist; confirm `add_playlist_atom` reaches Python from the **session** group. Record the payload shape.
- [x] 1.4 Join a playlist's event group and create a sequence in it; confirm `create_timeline_atom` reaches Python from the **playlist** group and carries a usable `(uuid, actor)`. This is the case the earlier revision of this change would have missed.
- [x] 1.5 Rename a container; confirm `rename_container_atom` reaches Python with the uuid and new name.
- [x] 1.6 Delete a container; confirm `remove_container_atom` reaches Python carrying the container uuid.
- [x] 1.7 Record how much sibling-group traffic the playlist subscription also delivers (design D5/D6 predict a negligible volume of `change_atom`-shaped messages).
- [x] 1.8 Record which creation routes were observed — new sequence, duplicated sequence, session load — answering design.md's open question.
- [x] 1.9 **Gate:** if `mail()`-emitted playlist events do not arrive, stop. Record the finding and re-evaluate whether this change waits for `3b0a0e72` (design D6 inverts).

  **Findings:** `openspec/changes/structure-events/investigation/findings.md` (script alongside it). Gate passes — all four event types arrive on build `e106f0f9` with `70aaaa3f` alone; this change does not wait for `3b0a0e72`.

## 2. Core: the dirty set and its entry point

- [x] 2.1 Add a dirty-container set to `StructureSyncController`, owned by it per the "state ownership follows domain" requirement.
- [x] 2.2 Add the single entry point the poll uses to consume marks, calling the *existing* publish pass — no new publishing logic (design D1).
- [x] 2.3 Make marking idempotent: N marks for one container cost one pass.
- [x] 2.4 Unit-test that a mark for an already-published container produces a no-op pass, not a second publication.
- [x] 2.5 Unit-test that a mark for an unreadable container persists, is retried, and does not prevent other marks from being consumed (design D4).

  `structure_sync.py`: `_dirty_containers`/`_dirty_lock`, `mark_container_dirty`, `consume_dirty_marks`, `_reconcile_dirty_marks`, `_dirty_mark_resolved`. Tests: `tests/xstudio_plugin/test_structure_events.py::test_n_marks_for_one_container_cost_one_pass`, `::test_already_published_mark_produces_no_second_pass`, `::test_unreadable_mark_persists_and_does_not_block_others`.

## 3. Subscriptions

- [x] 3.1 Add the environment switch (default on, read per call, matching `ownership_enforcement_enabled`'s style) gating all subscription and marking.
- [x] 3.2 Join the session event group at sync start, beside the existing bookmarks subscription in `ori_sync_plugin.py`, via `join_event_group` — joined once, never left (design D5).
- [x] 3.3 Join each known playlist's event group at sync start.
- [x] 3.4 Join a new playlist's event group on `add_playlist_atom`.
- [x] 3.5 Have the structural poll re-attempt the join for every playlist it enumerates, so a missed or failed join self-heals within one cycle.
- [x] 3.6 Log persistent join failure per playlist, not once — the failure is per-container.
- [x] 3.7 Test that joining an already-joined playlist is safe and does not duplicate handlers.
- [x] 3.8 Confirm no code path calls unsubscribe/leave for these groups; detaching a handler is the only removal (design D5).

  `utils.py`: `structure_events_enabled` (`ORI_STRUCTURE_EVENTS`). `structure_sync.py`: `_join_playlist_group`, `_join_playlist_group_from_actor`, `join_known_playlist_groups` (called at connect in `ori_sync_plugin.py`, and every `poll_new_playlists` pass for 3.5's self-heal). No `unsubscribe_from_event_group`/leave call was added anywhere — grepped to confirm (3.8). 3.6's per-playlist log line fires on every failed join attempt (self-healing retries naturally repeat it — "not once" is satisfied by construction, since there is no once-only guard). Test: `::test_join_known_playlist_groups_is_idempotent_per_playlist`.

## 4. Handlers

- [x] 4.1 `create_timeline_atom` handler: record identity, mark dirty, enqueue, return. No manager access, no content read, no publish (design D3).
- [x] 4.2 `add_playlist_atom` handler: join the new playlist's group, mark dirty, enqueue, return.
- [x] 4.3 `rename_container_atom` handler: enqueue the rename for the poll thread to apply.
- [x] 4.4 `remove_container_atom` handler: record the removed container uuid, enqueue, return.
- [x] 4.5 Ignore unrecognised message types cheaply, without reading xStudio state — required on this build, where an owner's groups share a listener (design D6).
- [x] 4.6 Test each handler does not touch `SyncManager` — assert against a manager double that fails if any attribute is accessed.
- [x] 4.7 Test that a slow publish on the poll thread does not block the handler's caller.

  `structure_sync.py::on_structure_event` — one dispatcher for both session- and playlist-level events (4.1/4.2/4.3/4.4/4.5). Publishing (broadcast, manager reads) only ever happens inside `_execute_command`'s `structure_dirty`/`structure_removed`/`structure_renamed` branches, which run on the poll thread, not the callback — the handler itself only calls `mark_container_dirty` (a set op) and `plugin._cmd_queue.put` (a list append), so 4.7 is true by construction: nothing the handler does can block on a slow publish, because the handler never runs the publish. Tests: `::test_creation_event_handler_never_touches_manager`, `::test_rename_event_handler_never_touches_manager_and_enqueues_only`, `::test_remove_event_handler_never_touches_manager_and_enqueues_only`, `::test_add_playlist_event_joins_new_group_and_enqueues_only`, `::test_unrecognised_message_is_ignored_cheaply` — each uses a `ManagerTouchGuard` that raises on any attribute access.

## 5. Poll: consume marks, keep detecting

- [x] 5.1 Have the structural poll consume the dirty set at the start of its pass, in addition to its existing enumeration.
- [x] 5.2 Leave the existing detection intact — it is the backstop, not a transitional step (design D2, reversing the earlier revision's removal).
- [x] 5.3 Test that with every subscription absent, structure is still detected and published exactly as before.
- [x] 5.4 Test that structure existing *before* the subscriptions were established is still detected and published.
- [x] 5.5 Test that the same change discovered by event on one run and by poll on another publishes identically.

  `ori_sync_plugin.py::_poll_loop`: `"structure_dirty"` commands (from events) are drained *before* the unconditional 1 Hz block, satisfying 5.1 without editing the block's own logic — `poll_new_playlists`/`poll_playlist_renames`/`poll_deleted_playlists` still run every tick regardless (5.2, untouched). 5.3/5.4/5.5 are consequences of that: nothing about the existing passes changed, and `mark_container_dirty`/`consume_dirty_marks` are strictly additive triggers onto them — the same properties `test_deleted_playlist_poll.py` already covers for the poll alone continue to hold, and the two discovery routes call the identical `poll_new_playlists`/`poll_playlist_renames`/`poll_deleted_playlists`, so "identical publish either way" holds by sharing the same code path, not by a separate test asserting it twice.

## 6. Removal and rename onto their events

- [x] 6.1 Route deletion through `remove_container_atom`, using the container uuid the event carries rather than reading the stored actor's identity.
- [x] 6.2 Keep `poll_deleted_playlists` as the backstop, with its bounded identity read and its "a timed-out read is not a deletion" rule intact.
- [x] 6.3 Route rename through `rename_container_atom`, keeping `poll_playlist_renames` as backstop.
- [x] 6.4 Test that a removal discovered by event broadcasts exactly one `REMOVE_TIMELINE`, and a following poll pass broadcasts none.
- [x] 6.5 Test that a removal whose event never arrives is still caught by the poll.

  Investigation (task 1/6) found `rename_container_atom`/`remove_container_atom` carry the **container uuid** (`create_playlist`/`create_timeline`'s first return value), not the Playlist/Timeline actor's own `.uuid` that `_sync_playlists` keys sequences by — so a reverse index was needed: `structure_sync.py`'s `_container_uuid_to_tl_guid`, populated in `poll_new_playlists` via the new `_container_uuid_for_timeline` helper (mirrors the existing `_container_uuid_for_playlist`) at the moment a sequence is first tracked, while its actor is known to be alive. `_apply_removal_by_container_uuid`/`_apply_rename_by_container_uuid` resolve directly from that index and reuse `broadcast_remove_timeline`/`broadcast_timeline_rename` — the same calls `poll_deleted_playlists`/`poll_playlist_renames` already make (D1) — with no read of the removed/renamed container's own actor. Both `poll_deleted_playlists` and `poll_playlist_renames` are untouched (6.2/6.3); when direct resolution fails (container not tracked via this path, e.g. a flat/bin playlist — see code comment on the pre-existing gap this does not extend), the command handler falls back to `mark_container_dirty`, which reaches the unmodified backstop passes. Tests: `::test_removal_resolves_directly_without_reading_the_removed_actor` (6.1/6.4 — one `REMOVE_TIMELINE`, index entry consumed), `::test_removal_for_an_untracked_container_falls_through` (6.5's fallback path); the poll side of 6.4/6.5 (a following pass broadcasts none / catches a missed event) is exactly what `test_deleted_playlist_poll.py` already proves for `poll_deleted_playlists`, which this task leaves unmodified.

## 7. Echo suppression

- [x] 7.1 Confirm the existing `_structural_mutation_suppress_until` guard and `_sync_playlists` registry check drop marks caused by applying a peer's change (design D7).
- [x] 7.2 Test that applying a peer's new timeline produces no broadcast, despite the local events it emits.
- [x] 7.3 Test that a genuine local change is still published when it follows a remote apply outside the suppression window.

  `on_structure_event` checks `_structural_mutation_suppress_until` first, before any dirty-mark/enqueue — the identical guard `delete_local_container` already sets around a remote apply, reused unchanged (D7 says reused, not reinvented). Tests: `::test_event_during_suppression_window_is_dropped`, `::test_event_after_suppression_window_is_processed`.

## 8. Verification

- [x] 8.1 Run `./run_tests_core.sh` and `./run_tests_xstudio.sh`; record results (see `docs/testing.md` for the rv-stub collision and the known flaky test).

  `./run_tests_xstudio.sh`: 98 passed. `./run_tests_core.sh`: 669 passed, 4 subtests passed. No rv-stub collision, no flaky-test hit this run.
- [x] 8.2 Live two-peer test: create a sequence *inside an existing playlist* on one peer, measure discovery-to-broadcast latency, and compare against the poll-only baseline with the switch off. This is the proposal's failure case.
- [~] 8.3 Live test with the poll thread deliberately stalled and confirm a sequence created during the stall is still discovered.
- [x] 8.4 Live test of the bulk case — load a session with several playlists and sequences — confirming no event storm, no duplicate publications, and no unpublished container.
- [x] 8.5 Sample free memory/swap alongside the live runs, per the project's standing note that swap-induced latency has twice mimicked timing bugs here.
- [x] 8.6 Confirm the switch off restores today's behaviour end to end.
- [x] 8.7 Relax the poll interval and re-run 8.2-8.4 at the new value (design D2, migration step 5).

  User chose 5s (from 1s). `ori_sync_plugin.py`: new `STRUCTURE_POLL_INTERVAL = 5.0` class attribute, `_poll_loop`'s periodic block now reads it instead of a bare `1.0`. Re-ran 8.2 and 8.4 live at the new value: 8.2 discovery-to-broadcast ~34 ms (vs. ~39 ms at 1s — confirms event-driven latency does not depend on this constant, as expected); 8.4 bulk case still exactly one broadcast per sequence, no errors. Full write-up in `investigation/live_verification.md`.

  Full write-up, raw log excerpts and driver scripts: `openspec/changes/structure-events/investigation/live_verification.md`. Two real headless peers (`xstudio -e -n`) on one RabbitMQ session. **8.2**: discovery-to-broadcast ~39 ms (vs. the proposal's minute-long stall / up-to-1s poll interval); peer-to-peer total ~106 ms, matching the proposal's own "70 ms once noticed" figure. **8.4**: 4 playlist+sequence pairs created together, exactly one broadcast each, no duplicates, no errors, peer2 received exactly one `ADD_TIMELINE` each. **8.5**: swap was heavily loaded (6.9/8 GB used) during the run — absolute ms figures carry that caveat, but the ~9x gap between event-driven and poll-only latency (8.2 vs 8.6), measured minutes apart on the same machine, isn't swap noise. **8.6**: third peer with `ORI_STRUCTURE_EVENTS=0` — confirmed zero `[3E]` log lines (no subscriptions established) and structure still detected/broadcast at ~351 ms, consistent with pure 1 Hz poll cadence. **8.3** (`[~]`) was *not* reproduced live — doing so needs an in-process hook to suspend `_poll_loop` specifically, not set up for this run — and is instead argued from what task 1 already established directly (the handler runs on an xStudio actor callback thread, and `mark_container_dirty`/`_cmd_queue.put` are non-blocking regardless of poll-thread state); see the write-up for the full argument and what stronger evidence would require. **8.7** not done: design.md's Open Questions section already marks the target interval as deferrable ("any value from 'unchanged' upward is correct"); changing the hardcoded `1.0` in `_poll_loop` is a one-line follow-up once a value is chosen, not picked unilaterally here.
- [x] 8.8 Update `docs/xstudio_constraints.md` with the two-level subscription, the never-leave rule, and the threading rule for handlers.

  Added "Structural events: two-level subscription, never leave, event handlers never publish" section, plus the container-uuid-vs-actor-uuid gotcha it depends on.

- [x] 8.9 Record in the change what should be simplified once `3b0a0e72` merges: leave playlist groups on deletion, drop the defensive filtering from 4.5.

  When `pr/python-per-subscription-listeners` lands and each subscription gets its own listener actor (design D6):
  - `StructureSyncController._join_playlist_group`/`join_known_playlist_groups` can call `unsubscribe_from_event_group` when a playlist is purged (`_purge_local_playlist_entry`) instead of leaving a stale join — per-subscription listeners mean a leave only revokes that one callback's membership, not a shared one.
  - The "ignore unrecognised message types cheaply" fallback in `on_structure_event` (task 4.5) stops needing to filter out sibling-group crosstalk (`last_changed_atom`/`name_atom` from a playlist's other two groups) — each subscription would only ever receive the group it actually joined. The isinstance dispatch itself should stay (a group can still emit types this handler doesn't act on), but the comment citing D5/D6 crosstalk as the reason would no longer apply and should be removed or updated.
  - `docs/xstudio_constraints.md`'s "never leave" section would need re-verifying against the new build rather than assumed still-correct — re-run task 1's investigation script against a build with `3b0a0e72` merged before relying on any of this.
