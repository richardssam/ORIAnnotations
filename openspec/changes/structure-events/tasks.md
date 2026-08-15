## 1. Prove the events arrive (gate — stop here if they do not)

- [ ] 1.1 Record which upstream fixes are in the xStudio build under test: `git merge-base --is-ancestor 70aaaa3f HEAD` and `git merge-base --is-ancestor 3b0a0e72 HEAD` in the xstudio repo (design D6).
- [ ] 1.2 Add a temporary subscription, in a scratch script or behind a debug flag, that joins an event group and logs every message received, unfiltered.
- [ ] 1.3 In a live session, create a playlist; confirm `add_playlist_atom` reaches Python from the **session** group. Record the payload shape.
- [ ] 1.4 Join a playlist's event group and create a sequence in it; confirm `create_timeline_atom` reaches Python from the **playlist** group and carries a usable `(uuid, actor)`. This is the case the earlier revision of this change would have missed.
- [ ] 1.5 Rename a container; confirm `rename_container_atom` reaches Python with the uuid and new name.
- [ ] 1.6 Delete a container; confirm `remove_container_atom` reaches Python carrying the container uuid.
- [ ] 1.7 Record how much sibling-group traffic the playlist subscription also delivers (design D5/D6 predict a negligible volume of `change_atom`-shaped messages).
- [ ] 1.8 Record which creation routes were observed — new sequence, duplicated sequence, session load — answering design.md's open question.
- [ ] 1.9 **Gate:** if `mail()`-emitted playlist events do not arrive, stop. Record the finding and re-evaluate whether this change waits for `3b0a0e72` (design D6 inverts).

## 2. Core: the dirty set and its entry point

- [ ] 2.1 Add a dirty-container set to `StructureSyncController`, owned by it per the "state ownership follows domain" requirement.
- [ ] 2.2 Add the single entry point the poll uses to consume marks, calling the *existing* publish pass — no new publishing logic (design D1).
- [ ] 2.3 Make marking idempotent: N marks for one container cost one pass.
- [ ] 2.4 Unit-test that a mark for an already-published container produces a no-op pass, not a second publication.
- [ ] 2.5 Unit-test that a mark for an unreadable container persists, is retried, and does not prevent other marks from being consumed (design D4).

## 3. Subscriptions

- [ ] 3.1 Add the environment switch (default on, read per call, matching `ownership_enforcement_enabled`'s style) gating all subscription and marking.
- [ ] 3.2 Join the session event group at sync start, beside the existing bookmarks subscription in `ori_sync_plugin.py`, via `join_event_group` — joined once, never left (design D5).
- [ ] 3.3 Join each known playlist's event group at sync start.
- [ ] 3.4 Join a new playlist's event group on `add_playlist_atom`.
- [ ] 3.5 Have the structural poll re-attempt the join for every playlist it enumerates, so a missed or failed join self-heals within one cycle.
- [ ] 3.6 Log persistent join failure per playlist, not once — the failure is per-container.
- [ ] 3.7 Test that joining an already-joined playlist is safe and does not duplicate handlers.
- [ ] 3.8 Confirm no code path calls unsubscribe/leave for these groups; detaching a handler is the only removal (design D5).

## 4. Handlers

- [ ] 4.1 `create_timeline_atom` handler: record identity, mark dirty, enqueue, return. No manager access, no content read, no publish (design D3).
- [ ] 4.2 `add_playlist_atom` handler: join the new playlist's group, mark dirty, enqueue, return.
- [ ] 4.3 `rename_container_atom` handler: enqueue the rename for the poll thread to apply.
- [ ] 4.4 `remove_container_atom` handler: record the removed container uuid, enqueue, return.
- [ ] 4.5 Ignore unrecognised message types cheaply, without reading xStudio state — required on this build, where an owner's groups share a listener (design D6).
- [ ] 4.6 Test each handler does not touch `SyncManager` — assert against a manager double that fails if any attribute is accessed.
- [ ] 4.7 Test that a slow publish on the poll thread does not block the handler's caller.

## 5. Poll: consume marks, keep detecting

- [ ] 5.1 Have the structural poll consume the dirty set at the start of its pass, in addition to its existing enumeration.
- [ ] 5.2 Leave the existing detection intact — it is the backstop, not a transitional step (design D2, reversing the earlier revision's removal).
- [ ] 5.3 Test that with every subscription absent, structure is still detected and published exactly as before.
- [ ] 5.4 Test that structure existing *before* the subscriptions were established is still detected and published.
- [ ] 5.5 Test that the same change discovered by event on one run and by poll on another publishes identically.

## 6. Removal and rename onto their events

- [ ] 6.1 Route deletion through `remove_container_atom`, using the container uuid the event carries rather than reading the stored actor's identity.
- [ ] 6.2 Keep `poll_deleted_playlists` as the backstop, with its bounded identity read and its "a timed-out read is not a deletion" rule intact.
- [ ] 6.3 Route rename through `rename_container_atom`, keeping `poll_playlist_renames` as backstop.
- [ ] 6.4 Test that a removal discovered by event broadcasts exactly one `REMOVE_TIMELINE`, and a following poll pass broadcasts none.
- [ ] 6.5 Test that a removal whose event never arrives is still caught by the poll.

## 7. Echo suppression

- [ ] 7.1 Confirm the existing `_structural_mutation_suppress_until` guard and `_sync_playlists` registry check drop marks caused by applying a peer's change (design D7).
- [ ] 7.2 Test that applying a peer's new timeline produces no broadcast, despite the local events it emits.
- [ ] 7.3 Test that a genuine local change is still published when it follows a remote apply outside the suppression window.

## 8. Verification

- [ ] 8.1 Run `./run_tests_core.sh` and `./run_tests_xstudio.sh`; record results (see `docs/testing.md` for the rv-stub collision and the known flaky test).
- [ ] 8.2 Live two-peer test: create a sequence *inside an existing playlist* on one peer, measure discovery-to-broadcast latency, and compare against the poll-only baseline with the switch off. This is the proposal's failure case.
- [ ] 8.3 Live test with the poll thread deliberately stalled and confirm a sequence created during the stall is still discovered.
- [ ] 8.4 Live test of the bulk case — load a session with several playlists and sequences — confirming no event storm, no duplicate publications, and no unpublished container.
- [ ] 8.5 Sample free memory/swap alongside the live runs, per the project's standing note that swap-induced latency has twice mimicked timing bugs here.
- [ ] 8.6 Confirm the switch off restores today's behaviour end to end.
- [ ] 8.7 Relax the poll interval and re-run 8.2-8.4 at the new value (design D2, migration step 5).
- [ ] 8.8 Update `docs/xstudio_constraints.md` with the two-level subscription, the never-leave rule, and the threading rule for handlers.
- [ ] 8.9 Record in the change what should be simplified once `3b0a0e72` merges: leave playlist groups on deletion, drop the defensive filtering from 4.5.
