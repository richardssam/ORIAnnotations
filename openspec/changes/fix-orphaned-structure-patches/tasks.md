## 1. Establish the mechanism

- [x] 1.1 Log the decision every structural message takes on the receiving peer — applied, ignored as a known GUID, buffered while joining, or dropped for an unresolvable parent.
  - Added as `DIAG(...)` lines in `manager.apply_patch`, `_h_add_timeline`, `apply_snapshot`'s replay loop, and `patcher.apply_patch`'s `InsertChild` branch.
- [x] 1.2 Run `openrv_hosts_selection` and record which branch discards the messages.
  - Result: **only** `DIAG drop: INSERT_CHILD parent a6482222 not in object_map (5 objects held)`, ×7. No buffering, no stale-drop, and the `_h_add_timeline` GUID guard never fired.
- [x] 1.3 If the cause is **not** the `_h_add_timeline` GUID guard, stop and revise design.md D2 before writing any fix.
  - It was not. Both earlier hypotheses were ruled out by instrumentation, and proposal.md, design.md and both spec files have been rewritten around the mechanism that is actually supported: OpenRV re-initialises its tracks after the first media add, the rebuilt Media track gets a **fresh sync GUID** (`fd37603f` → `a6482222`), and every subsequent `INSERT_CHILD` addresses a parent no peer holds.

## 2. Rename the change

- [x] 2.1 Rename to `fix-orphaned-structure-patches`. The fault is at the *sending* peer and in shared core, so neither "rv-follower" nor "media-materialisation" describes it; the name would otherwise be written into the spec history at archive time.
- [x] 2.2 Update the `blocked_by` reference in `sync_test/sync_tests.yaml` to match.

## 3. Stable structural identity (the fix)

- [x] 3.1 Carry the existing track's sync GUID across track re-initialisation in `sequence_sync`, so an object peers already hold keeps its name (design.md D1).
  - `_derive_track_guids` derives from `rv_track:<seq_name>:<track_name>`, mirroring the timeline GUID derived immediately below it from `rv_sequence:<seq_name>` — the tracks had simply been left out of a rule the timeline already followed. Applied in both native builders (`_init_timelines_from_sequences`, `_init_single_timeline`) before `register_timeline`, since `ensure_guid_and_map` preserves a GUID that is already set.
- [x] 3.2 Verify against the same evidence that found the defect: the `insert_child broadcasting: parent=…` GUID must not change across the `init tracks for …` line in the master's log.
  - It no longer does: `parent=c7b6b324` for index=0 *and* index=1–7, spanning the `init tracks for defaultSequence` line. Was `fd37603f` → `a6482222`. Zero `DIAG drop` lines on either peer.
- [x] 3.3 Re-run `openrv_hosts_selection` **repeatedly**, not once — master and host are elected per launch, so one green run does not establish it.
  - Three consecutive passes after five consecutive failures.
- [x] 3.4 Check whether xStudio's structure path has the same re-initialisation behaviour (design.md Open Questions). It builds structure differently, so this needs checking rather than assuming.
  - It does not. Both xStudio track-construction paths in `timeline_build.py` already derive the track GUID as `sha1(f"{tl_guid}:{kind}:{index}:{name}")`. OpenRV's native builder was the only place not following the rule — which also confirms D1 chose the house convention rather than inventing one.
- [x] 3.5 Unit-test the invariant rather than the implementation: a rebuild yields the same GUIDs, two peers agree on them, and — as a negative control — an insert broadcast after an underived rebuild really is orphaned on the receiver.
  - `tests/otio_sync/test_structure_identity.py`, 7 tests.

## 4. Make the failure loud

- [ ] 4.1 Promote the §1 drop instrumentation from debug logging to a reported failure, through the existing `mirror_failure` channel rather than a second one (design.md D3).
- [ ] 4.2 Surface it in the inspector's `/state` so it is observable without reading application logs — the property whose absence let eight lost messages go unnoticed.
- [ ] 4.3 Remove the §1 buffer/discard tracing, which has served its purpose; keep the drop reporting.
- [ ] 4.4 Do **not** add a replay or retry queue. Under §3 the condition stops arising, and a queue that silently succeeds later is the behaviour this change removes.

## 5. Send-side guard

- [ ] 5.1 Refuse, or at minimum report, a broadcast patch whose parent this peer has never published (design.md D2). Report-only first.
- [ ] 5.2 Escalate to refusal only once the full suite is green with reporting in place — a guard that is wrong in the conservative direction would suppress legitimate patches.

## 6. Delta-buffer replay comparison

- [ ] 6.1 Fix `apply_snapshot`'s replay comparison, which reads `sync_timestamp` from `payload["payload"]` while the field lives one level deeper in `command.payload`, so it is always `0` and every buffered delta is discarded.
- [ ] 6.2 Land it as its own commit with its own test. It did not cause this defect and must not ride along unexamined — it is on the join path, where a wrong fix is expensive to diagnose.

## 7. Close out

- [x] 7.1 Remove `status: known_broken` / `blocked_by` from `openrv_hosts_selection`.
- [ ] 7.2 Run the full sync suite and confirm no regression in the RV↔RV structural tests (`delete_media_openrv`, `delete_media_openrv_noscript`, `otio_import_rv_to_rv`).
- [ ] 7.3 Record whether the master/host split noted in `host-owned-visibility` recurred during this work, so that change inherits evidence rather than a rumour.
