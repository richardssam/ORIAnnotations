## 1. Core identity module

- [x] 1.1 Add `python/otio_sync_core/identity.py` with `local_identity() -> dict` returning `{"user", "first_name", "last_name", "host", "source"}`: `getpass.getuser()`, `socket.gethostname()`, and a best-effort personal name from `pwd.getpwnam(user).pw_gecos` (first comma-delimited segment only; empty on any failure). `source="local"`.
- [x] 1.2 Add `identity_from_override(text) -> dict` for a user-supplied identity: splits a display string into first/last, keeps the machine-resolved `user`/`host`, sets `source="override"`. Whitespace-only input returns no override.
- [x] 1.3 Add `resolve_identity()` that applies the env overrides `ORI_SYNC_USER` / `ORI_SYNC_NAME` over `local_identity()` — the deterministic path for `sync_test` — and returns `source="override"` when either is set.
- [x] 1.4 Add `normalise(identity) -> dict | None` used on receipt: drops unknown keys, coerces to `str`, and returns `None` for an all-empty identity so an absent identity is never stored as a present-but-blank one.
- [x] 1.5 Add `python/tests/otio_sync/test_identity.py` covering: gecos parsing (full name, empty, comma stanza), env override precedence, override keeps `user`/`host`, and `normalise` collapsing empties to `None`.

## 2. Resolution at session start

- [x] 2.1 Accept an optional identity in `SyncManager.__init__` (defaulting to `identity.resolve_identity()`), store it on the manager, and expose it read-only. Resolve once — no call to `identity` on any per-message path.
- [x] 2.2 Make a failure inside identity resolution non-fatal to session start: log and continue with no identity.
- [x] 2.3 Add a manager test asserting identity is resolved exactly once across many `announce_peer()` calls.

## 3. Wire propagation

- [x] 3.1 Add an `identity` field to `PeerAnnounce` in `protocol_messages.py`, including `to_payload` / `from_payload`, with `doc_field` documentation stating the fields are optional and self-declared. Omit the key entirely when there is no identity.
- [x] 3.2 Carry identity in `SyncManager.announce_peer()` and store it into `_peers` in `_h_peer_announce`, passing received values through `identity.normalise` so a blank identity never overwrites a known one.
- [x] 3.3 Add identity to `SyncManager._peer_roster()` and to `adopt_peers()`, following the roster's existing rule that liveness stamps stay local. Absence in the roster leaves any already-known identity intact.
- [x] 3.4 Add `python/tests/otio_sync/test_peer_identity_propagation.py`: a peer learned only from a roster carries the same identity as one learned from an announcement; an announce/roster carrying no identity does not clear a known one; a peer with no identity is still added to the table.

## 4. Shared projection

- [x] 4.1 In `session_state.py`, add `display_name(peer) -> str` deriving `"First Last"` → `user` → `app`, and expose `display_name`, `user`, `host`, and `source` on each peer entry of `session_state_snapshot`. Keep the projection read-only — no new fields on `SyncManager`.
- [x] 4.2 Extend `python/tests/otio_sync/test_session_state_snapshot.py` with the fallback chain (personal name, account-only, neither) and with a peer carrying no identity projecting to its application name.

## 5. Packaging and test-harness visibility

- [x] 5.1 Add `otio_sync_core/identity.py` to the vendoring list in `rvplugin/ori_sync/makepackage.csh`, **in this commit** — `__init__.py` swallows `ImportError`, so an omission leaves the RV plugin connected and inert.
- [x] 5.2 Rebuild the package, reinstall via `rvplugin/ori_sync/reinstall.csh`, and confirm from the RV startup banner which copy was loaded.
- [x] 5.3 Expose the local peer's `display_name` in both inspector hooks (`sync_test/python/sync_test/openrv_hook.py`, `xstudio_hook.py`) and add it to the runner's `ignore_keys`, alongside the existing `is_host` / `host_guid` — it differs between peers by construction.

## 6. OpenRV host

- [x] 6.1 Add a third field, "You:", to `session_dialog()` in `rvplugin/ori_sync/utils.py`, pre-filled with the resolved display name; return it alongside host and name. Update both call sites in `plugin.py` (`do_create_session`, `do_join_session`) for the widened return.
- [x] 6.2 Pass the override through `connect_to_session` into the `SyncManager` construction; an unedited field passes no override.
- [x] 6.3 Render `display_name` as the peer row's primary label in `python/otio_sync_core/qml/SessionStatePanel.qml`, with the application as secondary and the GUID row moving under Debug Mode.
- [x] 6.4 Show `user` and `host` in the panel only when Debug Mode is active; expose them through `ui_model.py`'s role names and `_FIELDS` map.

## 7. xStudio host

- [x] 7.1 Add an identity field to `xstudio_plugin/ori_sync/qml/ORISyncPlugin.1/SessionDialog.qml`, pre-filled from the core-resolved identity, and carry it through the connect path into `_session_connect_worker` and the manager.
- [x] 7.2 Render `display_name` as the peer row's primary label in `xstudio_plugin/ori_sync/qml/ORISyncPlugin.1/SessionStatePanel.qml`, with `user`/`host` behind the existing Debug Mode toggle. Read from the `Session State` attribute the poll thread already pushes — no new `python_callback`.
- [x] 7.3 Confirm xStudio derives no identity of its own: the only identity source in `xstudio_plugin/` is the value returned by the core.

## 8. Documentation

- [x] 8.1 Ensure the generated protocol reference documents the identity fields on both `PEER_ANNOUNCE` and the `STATE_SNAPSHOT` peer roster, that they are optional, and that they are self-declared and unverified — these come from the `doc_field` docstrings written in 3.1/3.3, so verify by regenerating rather than by editing output.
- [x] 8.2 Confirm no generated text describes the derived display name as a wire field.

## 9. Verification

- [x] 9.1 Run `./run_tests_core.sh` and `./run_tests_xstudio.sh` (two interpreters — use the repo `python`, not `python3.11`).
- [x] 9.2 Live two-app check: connect OpenRV and xStudio, confirm each names the other in its panel, that Debug Mode reveals account and machine, and that an overridden identity on one side appears on the other marked as user-entered.
- [x] 9.3 Mixed-version check — **waived, not run.** No older builds are in play, so there is no peer that omits the fields. The degradation path is covered by unit tests instead (`test_peer_with_no_identity_is_still_added`, and `test_peer_without_app_name_is_labelled_unknown` for the projection). Re-run this if a session ever has to interoperate with a build predating the identity fields.
- [x] 9.4 Late-joiner check: let one peer go quiet past a heartbeat, join a third peer, and confirm the quiet peer is named from the roster without waiting for its next announcement — the defect §3.3 exists to prevent. **Passed.** Still valid after the review fixes: those changed only `adopt_peers`' already-known-peer branch, and this scenario goes through the newly-learned branch.

## 10. Follow-ups (decide, do not silently defer)

- [x] 10.1 `sync_viewer`: give it a fixed identity (display name "Sync Viewer", `source="local"`) so an observer is labelled rather than anonymous, consistent with its existing `capabilities=[]` declaration.
- [x] 10.2 Leave `email` uncarried — it is added with the authenticated provider that would populate it (design.md Open Questions). Record the decision rather than adding an always-empty field.

## 11. Review follow-ups (applied 2026-08-12)

- [x] 11.1 `adopt_peers` merges rather than replaces: a peer already in the table has only a *missing* identity filled in. Rewriting the entry blanked `capabilities` from a partial roster — taking that peer out of host election — and restamped `last_seen` from another machine's view of who is present.
- [x] 11.2 `identity.normalise` judges emptiness on the identifying fields only. `source` is provenance and is always stamped, so a peer whose user and host both failed to resolve normalised to a present-but-blank identity that then overwrote a good one.
- [x] 11.3 `test_identity.py` moved from `python/tests/` (never collected — `run_tests_core.sh` runs `tests/otio_sync/`) and given the same `sys.path` bootstrap its siblings use, so it does not depend on another module having run first.
- [x] 11.4 `pwd` imported lazily: it is POSIX-only, `manager` imports `identity` unconditionally, and `__init__` swallows `ImportError` — a module-scope import would leave the plugin connected and inert rather than failing loudly.
- [x] 11.5 `resolve_identity()` no longer mutates `local_identity()`'s return value (surfaced by 11.3 — benign today, breaks the moment a provider caches).
- [x] 11.6 `SyncManager(identity_override={})` no longer suppresses identity; `_peer_roster()` copies the identity dict as it already copies `capabilities`.
- [x] 11.7 Protocol docs state the fields are **unverified** as well as self-declared, on both `PEER_ANNOUNCE` and the `STATE_SNAPSHOT` roster; regenerated.
- [x] 11.8 Package rebuilt and reinstalled after the above; both the `.rvpkg` and the installed copy carry the fixes.

Accepted and not fixed: the "Unknown" dialog pre-fill when nothing resolves; the RV panel delegate height (`rowHeight * 3`, now ~8px of headroom with a third debug row); pylint noise in `identity.py` (broad excepts, f-string logging).
