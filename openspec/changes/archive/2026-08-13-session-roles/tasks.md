## 0. Prerequisite

- [x] 0.1 Confirm `peer-identity` is implemented far enough to key on: `SyncManager` resolves an identity, `identity` is carried on `PeerAnnounce` **and** in `_peer_roster()` / `adopt_peers()`, and `session_state.display_name` reads it. `peer_roles` keys on `identity["user"]`, so a half-landed prerequisite silently degrades every role to the session default.

## 1. Role model in `authority.py`

Extend `authority.py` rather than adding a module — a new module means a new entry in `makepackage.csh`'s hand-maintained list, and an omission there leaves the RV plugin connected and inert.

- [x] 1.1 Add the role constants `DRIVER` / `REVIEWER` / `VIEWER`, `ROLES` (ordered, most-permitted first), and `DEFAULT_ROLE = DRIVER`.
- [x] 1.2 Add `ROLE_PERMISSIONS: dict[str, frozenset[str]]` mapping each role to the field groups it may emit, keyed on the same vocabulary `BROADCAST_CATEGORIES` uses plus the two `PLAYBACK_SETTINGS_1.0` sub-groups. Driver = all; reviewer = position, display, annotation; viewer = display only.
- [x] 1.3 Add `role_permits(role, category, *, destructive=False) -> bool`, the single predicate both the broadcast guard and the claim gate consult. `destructive=True` on `annotation` resolves driver-only; every other combination reads the table. An unknown role SHALL resolve permissively (treat as `DEFAULT_ROLE`), never restrictively — the `xs_flat_playlist` `media_exists` default got this wrong once already.
- [x] 1.4 Add `strip_role_fields(state, role) -> dict`, matching the shape of `strip_visibility_fields` / `strip_position_fields`: it removes `VISIBILITY_FIELDS` and/or `POSITION_FIELDS` from a playback payload according to `ROLE_PERMISSIONS`, whole groups only.
- [x] 1.5 Add `role_enforcement_enabled()` reading `ORI_SESSION_ROLES` **per call**, not cached at import, matching `ownership_enforcement_enabled()`. Default on — the policy default (`driver`) is the real off switch (D6); this switch exists for bisecting a suspected role bug.
- [x] 1.6 Add `python/tests/otio_sync/test_role_model.py`: the full matrix as a table-driven test (one case per proposal row), the destructive-annotation case, unknown role resolving permissively, and `strip_role_fields` never emitting a partial field group (the `clip_guid`-without-`view_mode` case).

## 2. Role policy and assignment

- [x] 2.1 Add role policy state to `SyncManager`: `_default_role` (init `DEFAULT_ROLE`) and `_peer_roles: dict[str, str]` keyed on `identity["user"]`. Expose `self_role` read-only and `role_for_peer(guid)`.
- [x] 2.2 Add `resolve_own_role()` applying D3's order — `_peer_roles[my user]`, else `_default_role` — called at session start before any broadcast can leave, and re-called when policy arrives (2.4). Resolve permissively when policy has not yet arrived.
- [x] 2.3 Accept `default_role` / `peer_roles` as optional `SyncManager.__init__` arguments so `sync_test` can start a session with a declared policy without a UI.
- [x] 2.4 Add `adopt_role_policy(policy)` — the single named adoption operation, mirroring `adopt_host()` / `adopt_ownership()`. A `None` or absent policy SHALL leave local policy unchanged (never clear it). Re-resolve own role after adoption and re-announce if it changed.
- [x] 2.5 Ensure `drop_peer()` removes the peer from `_peers` but **not** from `_peer_roles` — the peer table records presence, the role map records what the session decided about a participant, and conflating them breaks reconnection.
- [x] 2.6 Add `python/tests/otio_sync/test_role_policy.py`: memory-before-default; a reconnect under a new GUID with the same `user` restoring the role; one `user` on two GUIDs holding one role; an absent policy leaving an existing one intact; departure not erasing memory.

## 3. Enforcement — broadcast guard and claim gate

- [x] 3.1 Add `SyncManager._enforce_role(state, status)`, matching `_enforce_position`'s signature and status contract, and call it in `broadcast_playback_state` **ahead of** `_enforce_visibility` and `_enforce_position`. Returning `SUPPRESSED` must keep meaning "sent, with fields stripped".
- [x] 3.2 Gate the non-playback categories at the top of each `broadcast_*` whose category `role_permits` forbids — structure and annotation methods — via one shared helper rather than a copy of the check per method. Preserve `broadcast_add_annotation`'s existing exception (callers need the clip GUID back).
- [x] 3.3 Add a keyword-only `destructive: bool = False` to `broadcast_replace_annotation_commands` and route it into the role check (settled Open Question, 2026-08-12: `BROADCAST_CATEGORIES` is keyed on method name and the clear path shares a method with ordinary edits, so a category cannot express this row). Document that the caller declares *intent*, not authority.
- [x] 3.4 Gate `claim_category()` on `role_permits` (D8), beside the existing `ownership_enforcement_enabled()` no-op. A refused claim SHALL return without emitting `CLAIM_OWNERSHIP` and SHALL NOT release an existing owner.
- [x] 3.5 Verify by test that a role-stripped broadcast never reaches `_refresh_lease_confirmed` — the specific reason D2 orders role before category.
- [x] 3.6 Add `python/tests/otio_sync/test_role_enforcement.py`, asserting on the **sent envelope** rather than on whether a send occurred (the `test_broadcast_authority.py` pattern): reviewer keeps position and loses visibility in one message; viewer emits nothing session-visible; display survives for every role; a viewer's scrub emits no claim; a driver's claim is unaffected by a viewer's local activity; every assertion inert under `default_role: driver`.
- [x] 3.7 Extend `test_no_plugin_gates_a_broadcast_on_being_host` (or add its sibling) to assert no plugin gates a broadcast on **role** either.

## 4. Wire propagation

- [x] 4.1 Add a `role` field to `PeerAnnounce` in `protocol_messages.py` with `doc_field` documentation, omitting the key when unset, and carry it from `announce_peer()` / store it in `_h_peer_announce`.
- [x] 4.2 Add `role` to `_peer_roster()` and `adopt_peers()`. Absence in a roster SHALL resolve to `_default_role` at read time, not be written into the table as a value — so a later announcement carrying a real role is not shadowed.
- [x] 4.3 Add a `session_roles` section to `StateSnapshot` carrying `default_role` and `peer_roles`, omitted when the session declares no policy, and route receipt through `adopt_role_policy()`. Follow the `host_guid` / `broadcast_ownership` convention exactly.
- [x] 4.4 Add `python/tests/otio_sync/test_role_propagation.py`: a peer learned only from a roster is as role-identifiable as one learned from an announcement; a roster with no roles does not produce an empty driver set; a snapshot with no `session_roles` cannot clear a declared policy; both paths agree.

## 5. Elections

- [x] 5.1 Add a role filter to `authority.elect_host_guid` alongside the existing capability check, applying `HOST_PREFERENCE` ranking and the GUID tie-break among survivors. The function stays a pure function of the peer table. It needs the session default to resolve an absent role — pass it in rather than reading manager state.
- [x] 5.2 Add `has_eligible_driver(peers, default_role) -> bool` in `authority.py` — the one predicate both hosts' "Become Controller" gating and the panel's driverless indicator read, so neither computes it locally.
- [x] 5.3 Rank drivers first in `elect_self_as_master`'s candidate evaluation at the **existing** discovery timeout. Add no new wall-clock deferral, and keep the operation the single owner of every transition it entails. Preference only — a session with no driver still elects a master, and that master's role is unchanged.
  - **Implemented as a tie-break on conflicting claims, not as a deferral in `elect_self_as_master`.** That method has no candidate evaluation to rank: it always elects, and every caller (both hosts' discovery timeouts, `tick`'s failover) treats the call as having done so. Declining there would leave a session masterless whenever the preferred peer was not watching — the freeze direction D6 exists to avoid — so the ranking (`authority.master_rank`) is applied in `_h_i_am_master`, where a ranking can actually decide something: a driver that is already master declines a non-driver's competing claim and re-asserts. No new wall clock, a master always exists, and it is inert while every peer is a driver (the ranking's own tie).
- [x] 5.4 Confirm `_drain_host_elections()` re-checks eligibility at drain time so a role arriving between enqueue and drain is honoured; add a test for exactly that window.
- [x] 5.5 Add `elect_role_to_driver()` for D7 self-elevation: set own role to `driver`, re-announce, request a host election. It SHALL refuse when `has_eligible_driver` is true, so the gate lives in core and neither plugin can relax it independently.
- [x] 5.6 Extend `python/tests/otio_sync/test_host_election.py`: a viewer with the preferred app loses to a driver on a non-preferred app; preference still decides among drivers; an unknown role stays eligible under a `driver` default; a table with no drivers elects no host and reports the condition; two simultaneous self-elevations converge on one host.

## 6. Projection and test inspector

- [x] 6.1 Replace `session_state.peer_role()`'s `Host`/`Client` placeholder with the session role, keeping `is_master` / `is_host` as the separate flags they already are. Update its docstring — it currently names this change as the thing it is waiting for.
- [x] 6.2 Add `driverless` (from `has_eligible_driver`) to `session_state_snapshot`, so both panels report the condition from one derivation.
- [x] 6.3 Expose role, elected host, and the driverless flag in both inspector hooks (`sync_test/python/sync_test/openrv_hook.py`, `xstudio_hook.py`), and add the per-peer ones to the runner's `ignore_keys` beside the existing `is_host` / `host_guid` — they differ between peers by construction.
- [x] 6.4 Extend `python/tests/otio_sync/test_session_state_snapshot.py` for the role field, the driverless flag, and the projection staying read-only.

## 7. Packaging

- [x] 7.1 No new core module should exist (§1 extends `authority.py`). If one was added anyway, add it to the vendoring list in `rvplugin/ori_sync/makepackage.csh` **in the same commit** — `__init__.py` swallows `ImportError`, so an omission leaves the RV plugin connected and inert.
- [x] 7.2 Rebuild, run `rvplugin/ori_sync/reinstall.csh`, and confirm from the RV startup banner which copy was loaded before any in-RV testing.
  - Rebuilt and reinstalled; the installed `authority.py` under `~/Library/Application Support/RV/Python/` verified to carry the role model. The banner check itself still wants an RV launch.

## 8. OpenRV host

- [x] 8.1 Add "Become Controller" to the OTIO Sync menu in `rvplugin/ori_sync/plugin.py`, calling `elect_role_to_driver()`. Enabled only when `has_eligible_driver` is false, `DisabledMenuState` otherwise; rebuilt through the existing `defineModeMenu` path so it tracks connection state like every other item.
- [x] 8.2 Show the session role on the peer row in `python/otio_sync_core/qml/SessionStatePanel.qml`, and surface the driverless condition as a visible panel state — not only as a disabled menu item (the report is the requirement, not the affordance).
- [x] 8.3 Expose `role` and `driverless` through `ui_model.py`'s role names and `_FIELDS` map.
- [x] 8.4 Confirm no RV broadcast path acquired a role branch: `annotation_sync.py`, `playback_sync.py`, `sequence_sync.py`, `display_sync.py` keep calling `broadcast_*` and `claim_category()` unconditionally. The one permitted change is `on_clear_paint` passing `destructive=True` — intent, not authority.

## 9. xStudio host

- [x] 9.1 Add "Become Controller" as a direct child of the top-level "Session" menu (beside Create/Join/Leave/Session State/Resync — not nested), calling `elect_role_to_driver()`, enabled only in the driverless condition.
- [x] 9.2 Show the session role and the driverless condition in `xstudio_plugin/ori_sync/qml/ORISyncPlugin.1/SessionStatePanel.qml`, reading from the `Session State` attribute the poll thread already pushes — no new `python_callback`.
- [x] 9.3 Confirm no xStudio controller gained a role branch on a broadcast path, per the module structure's state-ownership rule.

## 10. Protocol documentation

- [x] 10.1 Document the `role` field on `PEER_ANNOUNCE` and on each `STATE_SNAPSHOT` peer roster entry, stating that an absent role means the session default — generated from `doc_field`, with no manual generator edit.
- [x] 10.2 Document the `session_roles` section of `STATE_SNAPSHOT`, including that an omitted section means "no declared policy" rather than an empty one.
- [x] 10.3 State in the generated reference that role is enforced send-side and is not validated on receipt, so an implementer does not assume filtering that does not happen.

## 11. Verification

- [x] 11.1 Run both suites on their own interpreters (`./run_tests_core.sh`, `./run_tests_xstudio.sh`) — note the `rv` stub collision documented in `docs/testing.md`, which makes some tests pass alone and fail in a full run.
- [x] 11.2 Add a `sync_test` scenario with a declared policy: one driver, one reviewer, one viewer. Assert the reviewer scrubs but cannot change the shot, and that the viewer changes nothing the driver sees.
  - Added as `managed_session_roles` (driver + viewer; the harness runs two apps, so the reviewer tier is covered by `test_role_enforcement.py` rather than a third instance). Needed three pieces of harness first: per-app environment in `spawner.launch`, a `roles`/`users` declaration that becomes **one identity-keyed policy** rather than a per-peer default (a joiner adopts the master's policy, so a per-peer default would not survive the first snapshot), and two runner actions — `expect_role` (prove the policy took effect, or the suppression assertions are vacuous) and `expect_no_propagation` (assert on what the *other* peers did not do). **Written, not yet executed** — it needs live RV and xStudio.
- [x] 11.3 Soak with a **viewer that is actively interacting**, not merely present. The D8 claim-gate composition is invisible when non-drivers sit still, which is how it would otherwise reach a screening. Watch for position sync stalling for a lease duration after a viewer touches its playhead.
  - Run 2026-08-13 12:09–12:11, xStudio driver+host (`7ed82688`) / xStudio viewer (`8c2a8ddd`), both roles confirmed on the wire in `PEER_ANNOUNCE`. The viewer scrubbed, isolated clips, reordered the bin and deleted media throughout.
  - **D8 holds.** The viewer logged `claim_category: refused position` and `refused structure`; every `lease[position]` line in the host log names the host's own guid. No stall, no unconfirmable lease, no contest with the driver's claim.
  - Total inbound at the host from the viewer across ~90 s: 12 `PEER_ANNOUNCE`, 1 `WHO_IS_MASTER`, 1 `STATE_REQUEST`, 1 `DISPLAY_SETTINGS_1.0/SET` and its `CLAIM_OWNERSHIP`. Zero playback, structure or annotation. Display is the one group a viewer may emit, so that `SET` is the contract, not a leak.
  - The host's receive-side `no position fields` guard never fired: role-emptied playback messages were dropped before send, leaving that guard as defence in depth rather than the load-bearing check.
  - **Out-of-scope gap surfaced, deliberately not fixed here.** Enforcement is send-side only, so the viewer's *local* delete still took effect (`broadcast_remove_child: suppressed` at 12:11:01.975, then `flat playlist deleted media: 'seq_A' removed` two milliseconds later). The two peers diverged permanently; when the host later selected into the deleted material the viewer logged ten `RECV playback state: mismatched timeline_guid — ignoring (not playing)` over two seconds. The refusal to guess is correct, but nothing re-requests a snapshot to heal the split. Roles govern emission, never local mutation — the spec should say so, and the repair path (resync on mismatch, or disabling destructive actions for non-drivers in the UI) belongs with the role admin interface.
- [x] 11.4 Soak once with `default_role: driver` (the shipping default) and confirm zero behavioural difference from today — no strips, no refused claims, unchanged host election.
  - Repeated xStudio↔xStudio soaks 2026-08-13 12:24–16:38, both peers resolving to `driver`. Final run: **0** `stripped fields not permitted to role`, **0** `claim_category: refused`, **0** `may not emit`, on both peers. Host election ran the role-filtered candidate path and returned what the pre-roles ordering returned.
  - **The stronger evidence is what the soaks found instead.** Five defects surfaced across the day, every one of them pre-existing and none reachable from the role model: an unbounded per-frame `attr.name` read killing xStudio's dispatcher; the broadcast playback mode read back from an in-flight async write; a flat playlist's own timeline resolving to an unpublished guid; the same async read-back re-writing Loop Mode once per message; and the visibility seat assigned by GUID coin flip. A change that altered behaviour under its own default would not have left this much unrelated breakage as the only thing to find.
  - Caveat on sequencing: the run used to demonstrate this also carries the host-election fix (master breaks the tie). That fix is orthogonal to roles — it changes *which* eligible peer is elected, never whether the role filter applies — but the two are not independently attested by a single soak.
- [x] 11.5 Sample free memory and swap beside the soak runs; swap-induced latency has twice mimicked a timing race here and produced a false diagnosis.
  - Sampled after the 16:38 run: **~128 MB free** (8217 pages × 16 KB), swap **10.9 GB of 12 GB used**. Heavily swapped throughout the day's testing.
  - Read the timing observations accordingly: the `[POLL-SLOW] … took 2.0s` entries and the `Dequeue timeout` on `read_xs_display_state` are consistent with memory pressure rather than a sync-layer race, and none of the five defects above was diagnosed from timing alone — each was confirmed against a counted or structural fact in the logs.
