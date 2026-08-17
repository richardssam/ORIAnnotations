## 1. Join validation (shared core)

- [x] 1.1 In `otio_sync_core.manager`, expose enough state after a `self_elect` fires (e.g. whether this session's connect attempt was a join vs. create, and the discovery outcome) for a host to distinguish "became master because I created it" from "became master because nobody answered a join." — **superseded during live testing**: an initial version added `join_self_election_is_failure(was_create_attempt, is_master)`, checked from `on_synced` after election. That crashed RV live (`on_synced` fires synchronously mid-election, before timeline-building code that then ran against a manager the check had just torn down) and was also unsound (it would have fired on legitimate mid-session master-failover recovery too, not just a failed join). Final design needs no shared manager-level state at all: each host checks its own `_pending_create_check` flag directly, at the point where it's about to self-elect — before electing, not after.
- [x] 1.2 Add a `sync_test` case exercising the manager-level signal directly: a join-mode connect against a session name with no live master ends in the "not found" outcome, not a silent self-election. — no manager-level signal exists to test post-redesign (see 1.1). Regression coverage instead lives at the host level (see 2.x).

## 2. Join validation (xStudio host)

- [x] 2.1 Generalize `_pending_create_check` (or add a parallel flag) so `_on_synced_once` (`ori_sync_plugin.py:383-397`) can tell join attempts from create attempts, not just flag create attempts. — no rename/generalization needed; the existing boolean already distinguishes create (`True`) from join (`False`).
- [x] 2.2 Add the join-side branch: if this was a join attempt and `self.manager.is_master` is true when `_on_synced_once` fires (self-election happened, nobody answered), call `disconnect()` and show a "Session Not Found" error via `popup_message_box`. — **implemented differently than described**: not in `_on_synced_once`. The check runs in the `self_elect` and `state_request_timeout` command/event handlers, *before* `elect_self_as_master()` is called, via a shared `_report_session_not_found()` helper. Also closed the same gap on the `state_request_timeout` path (a peer answers discovery but never answers the follow-up state request), which had the identical unconditional-self-election bug. Poll loop (`_poll_loop`) hardened to stop processing a tick's remaining actions if a disconnect happens mid-batch.
- [x] 2.3 Manually verify: joining a session name with no live peer shows the error and leaves the plugin disconnected; joining an existing session is unaffected; creating a new session is unaffected. — user confirmed working live 2026-08-17, after the `on_synced`-ordering crash fix above.

## 3. Join validation (RV host)

- [x] 3.1 Mirror the same join/create distinction and error path in `rvplugin/ori_sync/plugin.py`'s connect completion handling. — mirrors the final xStudio design (2.2), not the originally-described one: the check lives in `_init_as_master` (covers both the discovery-timeout and `state_request_timeout` triggers, which both funnel through it) rather than `on_synced`. `poll_network` hardened to re-check `sync_manager` after a possible mid-tick disconnect, both after the discovery block and after each action in the tick loop.
- [x] 3.2 Manually verify the same three cases as 2.3 in RV. — user confirmed working live 2026-08-17 (this is the crash that was found and fixed: initial `on_synced`-based implementation crashed with `AttributeError: 'NoneType' object has no attribute 'tick'`/`'_derive_guid'`; fixed and reinstalled via `rvplugin/ori_sync/reinstall.csh`, then verified working).

## 4. Read-only account identity field

- [x] 4.1 Add a read-only "Account:" field to `SessionDialog.qml`, sourced from the same `get_default_identity` call already used for "You:", positioned so it's visibly distinct from the editable field. — added via a new `get_account_identity` python_callback (`resolve_identity()["user"]`), kept separate from `get_default_identity` since the two fields carry different data (account vs. display-name override).
- [x] 4.2 Add the equivalent read-only field to RV's `session_dialog()` in `rvplugin/ori_sync/utils.py`, using `resolve_identity()["user"]` (or the overridden account where `ORI_SYNC_USER` is set).
- [x] 4.3 Manually verify in both hosts: the account field shows the resolved/overridden account, cannot be typed into, and the existing "You:" display-name override still works unchanged. — user confirmed working live 2026-08-17.

## 5. Site config file for RabbitMQ host

- [x] 5.1 Add a `otio_sync_core.site_config` module (or similarly named sibling of `identity.py`) that reads an optional config file from the plugin's installed folder and returns a host/port default, or `None` on missing/malformed input — never raising.
- [x] 5.2 Wire the config file into xStudio's `SessionDialog.qml` host pre-fill (`Component.onCompleted`), inserting it between the `ORI_RMQ_HOST` env var and the hardcoded `"localhost"` fallback.
- [x] 5.3 Wire the config file into RV's `session_dialog()` (`utils.py:155`) at the same precedence position.
- [x] 5.4 Document the config file's location and format (e.g. in `xstudio_plugin/ori_sync/README.md` and `rvplugin/ori_sync/README.md` or a shared docs page) so a site knows what to drop where.
- [x] 5.5 Add a test covering: no file (falls back to env var/default), a valid file (used), a malformed file (falls back without error), and an env var present alongside a config file (env var wins). — first three covered by `tests/otio_sync/test_site_config.py`; "env var wins" is enforced by the wiring itself (both hosts short-circuit on the env var before consulting the config file) rather than a dedicated test, since exercising the QML/QDialog wiring end-to-end needs a live Qt/xStudio environment.

## 6. Validation

- [x] 6.1 Run the existing sync test suites (`./run_tests_core.sh`, `./run_tests_xstudio.sh`) to confirm no regression in existing join/create flows. — 731 passed (core), 123 passed (xstudio_plugin, stubbed `xstudio`/`pika`), including 4 new regression tests in `tests/xstudio_plugin/test_discovery_self_elect.py` covering the not-found-vs-create branches at both trigger points via a duck-typed plugin. RV's live crash and fix were verified in a real RV session by the user, not by an automated test (RV's `plugin.py` isn't easily unit-testable — see `docs/testing.md`).
- [x] 6.2 `openspec validate session-connection-ux --strict` — passes.
