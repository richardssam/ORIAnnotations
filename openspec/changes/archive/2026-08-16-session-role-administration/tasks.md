## 1. Core — administration permission

- [x] 1.1 Add `ADMINISTRATION = "administration"` to `authority.py` beside the existing role permission groups, with a comment stating it gates a whole message rather than a stripped field group (design D3).
- [x] 1.2 Grant `ADMINISTRATION` to `driver` only in `authority.ROLE_PERMISSIONS`; leave `reviewer` and `viewer` without it.
- [x] 1.3 Add `authority.role_may_administer(role)` as a thin, documented wrapper over `role_permits(role, ADMINISTRATION)`, so call sites read as intent rather than as a table lookup.
- [x] 1.4 Extend `tests/otio_sync/test_role_model.py`: `driver` may administer, `reviewer` and `viewer` may not, an absent/unknown role resolves permissively via `normalise_role`.
- [x] 1.5 Add tests asserting the group is inert on the broadcast path — no `broadcast_*` name maps to `ADMINISTRATION` through `role_group_for()`, and `strip_role_fields()` output is unaffected by it (design D3 risk).

## 2. Core — the SET_PEER_ROLE message

- [x] 2.1 Add `SetPeerRole` to `protocol_messages.py`: `@register @dataclass`, `SCHEMA = "LiveSession.1"`, `EVENT = "SET_PEER_ROLE"`, `doc_field`s for `user`, `role`, `issuer_guid`, with `to_payload` / `from_payload` written explicitly like `PeerDepart`.
- [x] 2.2 Write the class docstring to carry the two non-inferable properties the `protocol-message-docs` spec requires: it is broadcast and merged by every peer, and it is applied by its target which then re-announces.
- [x] 2.3 Add `("LiveSession.1", "SET_PEER_ROLE")` to `manager.NON_DISPLAY_EVENTS` so a grant is never attributed as a display change.
- [x] 2.4 Extend `tests/otio_sync/test_protocol_messages.py` with a round-trip test for `SetPeerRole` and a check that it is registered under its `(schema, event)` key.

## 3. Core — issuing and receiving a grant

- [x] 3.1 Add `SyncManager.set_peer_role(user, role) -> bool`: refuse when this peer's role lacks `ADMINISTRATION`, when `user` is empty, or when `role` is not recognised; log every refusal with its reason.
- [x] 3.2 In `set_peer_role`, apply the grant locally via `adopt_role_policy({"peer_roles": {user: role}})` **before** sending `SetPeerRole`, because the network layer discards this peer's own broadcasts (design D4). Comment the ordering.
- [x] 3.3 Register `("LiveSession.1", "SET_PEER_ROLE"): self._h_set_peer_role` in the dispatch table and implement the handler as an `adopt_role_policy` call with a grant-specific log line (design D2). Add no second merge path.
- [x] 3.4 Verify by test that `adopt_role_policy` already delivers the grant semantics unchanged: additive merge, own-role re-resolution, re-announce only when this peer's own role moved, host-election request.
- [x] 3.5 Add `tests/otio_sync/test_role_administration.py` covering: a driver's grant is emitted; a reviewer's and a viewer's are not; the target applies it and announces; a non-target merges without announcing; a grant naming the role the target already holds emits no announcement.
- [x] 3.6 Test that a grant reaches the master's memory without a routing hop — grant issued by a non-master peer, then a joiner's `STATE_SNAPSHOT` carries the granted role.
- [x] 3.7 Test the reconnection case end to end: participant granted `driver` in a `viewer`-default session, peer dropped and rejoined under a new GUID, resolves to `driver`.
- [x] 3.8 Test that demoting the elected host to a role forbidding visibility re-elects host onto another eligible driver, and that demoting the last driver leaves the session driverless and self-elevation available.
- [x] 3.9 Test that a grant does not change `default_role`, and that self-elevation stays refused while an eligible driver exists.
- [x] 3.10 Test the compatibility path: a `SET_PEER_ROLE` payload delivered to a manager with no handler for it (or an unregistered event) is ignored without raising and without changing any role.

## 4. Core — create-time default role

- [x] 4.1 In `SyncManager.__init__`, add an explicit `seed_creator: bool = False` parameter; when `True` and `default_role` names a role other than `driver`, seed `_peer_roles[identity["user"]] = DRIVER` before the existing `resolve_own_role()` call. (Revised from inferring this off `default_role` alone — that broke 23 pre-existing tests that use `default_role=` to pin a peer's own role with no creation semantics intended; see design D5.)
- [x] 4.2 Document in that code why seeding requires the explicit flag and never fires off `ORI_SESSION_DEFAULT_ROLE` alone — the env var is read by joiners too, and seeding on it would make every env-configured peer a driver (design D5).
- [x] 4.3 Extend `tests/otio_sync/test_role_policy.py`: constructing with `default_role="viewer", seed_creator=True` leaves this peer a `driver` and the session not driverless; `default_role="viewer"` alone leaves this peer a `viewer`; `ORI_SESSION_DEFAULT_ROLE=viewer` alone (even with `seed_creator=True`) leaves this peer a `viewer`.
- [x] 4.4 Test that the constructor argument wins over the environment variable when both declare a default and `seed_creator=True`.

## 5. Projection

- [x] 5.1 Add `may_administer_roles` to `session_state_snapshot`, derived from `authority.role_may_administer(manager.self_role)`. Add no per-peer field — rows already carry `user`.
- [x] 5.2 Confirm and keep the projection read-only: it reads the manager and returns plain data, and gains no write path (`session-state-ui` spec).
- [x] 5.3 Extend `tests/otio_sync/test_session_state_snapshot.py` for the new key across all three roles, and for a peer entry with an empty `user` (the case the panel must not offer a control for; already covered by the existing `test_snapshot_derives_display_name_with_fallback` peer row).

## 6. OpenRV host

- [x] 6.1 Add an optional default-role combo to `rvplugin/ori_sync/utils.py::session_dialog`, shown only when the caller asks for it; return it alongside host/name/identity.
- [x] 6.2 Show the combo from `do_create_session` and not from `do_join_session`; pass the selected value through `connect_to_session` to the `SyncManager` constructor (with `seed_creator=True`, design D5), omitting it entirely on the join path.
- [x] 6.3 Add a `@Slot(str, str)` `setPeerRole` to `ui_model.SessionStateModel` that calls `manager.set_peer_role`, and a `mayAdministerRoles` property backed by the new snapshot key.
- [x] 6.4 Add a per-row role control to `python/otio_sync_core/qml/SessionStatePanel.qml`, enabled only when `mayAdministerRoles` is true and the row carries a non-empty `user`.
- [x] 6.5 Confirm before issuing a grant that would leave the session with no eligible driver; make the confirmation a view-level courtesy that never gates the core call. (Implemented as a two-click inline confirm on any demotion away from `driver` — the panel has no cheap way to know it's specifically the *last* driver, so it asks on every such demotion rather than sometimes.)
- [x] 6.6 Verify no new `otio_sync_core` module was introduced, so `rvplugin/ori_sync/makepackage.csh`'s vendoring list needs no edit; if one was, add it in the same commit (design D8). (Confirmed: `authority.py`, `manager.py`, `protocol_messages.py`, `session_state.py`, `ui_model.py`, `qml/SessionStatePanel.qml` were all already vendored; `rvplugin/ori_sync/utils.py` and `plugin.py` are zipped separately by the same script.)

## 7. xStudio host

- [x] 7.1 Add a default-role combo to `SessionDialog.qml`, visible only when `dialog.mode === "create"`, and include `default_role` in the `do_session_connect` payload.
- [x] 7.2 Forward `default_role` through `do_session_connect` and `_session_connect_worker` into `connect_to_session` (with `seed_creator=True`, design D5) and the `SyncManager` constructor; omit it on join.
- [x] 7.3 Add a `set_peer_role` plugin method reachable from `python_callback`, calling `manager.set_peer_role` directly — the same threading convention `_menu_become_controller` already uses (design D7).
- [x] 7.4 Add a per-row role control to `xstudio_plugin/ori_sync/qml/ORISyncPlugin.1/SessionStatePanel.qml`, gated on `state.may_administer_roles` and a non-empty row `user`, dispatching via `python_callback` on click only. Leave the display path attribute-bound.
- [x] 7.5 Confirm before a grant that would leave the session driverless, matching the OpenRV panel's wording. (Same two-click inline-confirm-on-any-demotion-from-driver approach as the OpenRV panel.)

## 8. Documentation

- [x] 8.1 Add a `SetPeerRole` entry to the `protocol_messages` section of the unified `docs/config.yml` with a category and an example payload.
- [x] 8.2 Regenerate the protocol reference and confirm the message appears with its schema, event, fields, and both the broadcast/merge and target-applies statements.
- [x] 8.3 Update the role sections of any component docs that state the policy is environment-only, so they describe the create-time declaration and grants instead. (No doc claimed environment-only; `docs/introduction.md`'s "Who may send what" section — the one place role wire-mechanics are documented for implementers — got a short addition on `SET_PEER_ROLE` grants: broadcast, merged by every peer, applied by its target.)

## 9. Verification

- [x] 9.1 Run `./run_tests_core.sh` and `./run_tests_xstudio.sh` and record the results, noting the known flaky test from `docs/testing.md` if it appears. (724/724 core, 101/101 xstudio; no flake observed.)
- [x] 9.2 Live-verify in a two-peer session: create with `default_role: viewer`, confirm the creator is a driver and the joiner a viewer, grant the joiner `driver`, confirm both panels show it and the joiner can then drive. (Verified twice: first at the protocol/manager level over a real RabbitMQ broker (two live `SyncManager`+`RabbitMQNetwork` processes, no `FakeNetwork`), then end-to-end through the actual apps — xStudio created `samtest` with default role `viewer`, RV joined under a distinct identity (`sam2`) and correctly resolved to `viewer`, and a grant issued from xStudio's Session State panel landed on RV within ~25ms and flipped it to `driver`, confirmed in both `xstudio_host.log` and `rv_client.log`.)
- [x] 9.3 Live-verify the reconnection case: with the grant in place, restart the granted peer and confirm it returns as a driver. (Verified via the protocol-level script: peer dropped, rejoined under a new GUID, resolved to the granted `driver` role from session memory. Not separately repeated through the GUI restart path, but 9.2's GUI pass exercised the same `adopt_role_policy` code the reconnection case depends on.)
- [x] 9.4 Live-verify the no-policy case is unchanged: a session created with `default_role: driver` behaves exactly as before, and no role control changes anything observable. (Verified via the protocol-level script, and incidentally via the GUI too — an early xStudio create-session attempt left the combo at its default "Driver" selection, and the session behaved exactly as an unrestricted session should.)
- [x] 9.5 Reinstall the RV package (`rvplugin/ori_sync/reinstall.csh`) before any RV verification, since RV loads the installed copy rather than the repo source. (Rebuilt and reinstalled; `otiosyncdemo-1.2.rvpkg` now carries every touched core module.)

**Bugs found and fixed during live GUI verification, adjacent to this change's own code:**
- xStudio's `SessionDialog.qml` was instantiated via the same `"SessionDialog {}"` snippet for both Create and Join, so `dialog.mode` never left its `"join"` default — the create-only default-role picker could never appear regardless of which menu item was used. Fixed by giving Create and Join distinct snippets (`SESSION_CREATE_DIALOG_QML` / `SESSION_JOIN_DIALOG_QML` in `xstudio_plugin/ori_sync/utils.py`), each setting `mode` explicitly.
- The dialog's Connect payload sent `"you": null` whenever the identity field was left at its default (the common case) — a `null` in a QML→Python `python_callback` dict silently broke argument marshalling, so Connect appeared to do nothing (dialog closed, nothing logged, no Python method ever entered). Fixed by sending `""` instead, which `do_session_connect` already treats identically to absent.
- `identity_from_override()` (`python/otio_sync_core/identity.py`) built its identity from `local_identity()` directly, so `ORI_SYNC_USER`/`ORI_SYNC_NAME` environment overrides were silently discarded the moment any text was typed into a host's identity field — the two override mechanisms didn't compose. Fixed by basing it on `resolve_identity()` instead, so an environment-set account identity persists under a typed display name.

None of these three are in the spec/design/tasks scope above — they were pre-existing, unrelated to session-role-administration's own logic — but they blocked verifying it live, so they're recorded here rather than in a separate change.
