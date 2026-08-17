## Context

The join path today has no notion of failure. `connect_to_session` always broadcasts `WHO_IS_MASTER` and starts a 2s discovery timer (`DISCOVERY_TIMEOUT`, `ori_sync_plugin.py:93`); if nothing answers, `_discovery_timeout_task` (`ori_sync_plugin.py:796`) queues `self_elect`, and this peer becomes the session's master — whether the user clicked Create or Join. The **create** path already guards against the opposite mismatch: `_pending_create_check` is captured at connect time, and `_on_synced_once` (`ori_sync_plugin.py:383-397`) pops "Session Already Exists" if a create attempt ends up joining an existing master instead. There is no symmetric check on the join side: a join that times out with no answer just silently becomes a lone master of a brand-new, empty "session" under that name.

Host default resolution today is: value typed in the dialog > `ORI_RMQ_HOST` env var > hardcoded fallback (`"localhost"` in xStudio's QML, `"127.0.0.1"` in RV's `utils.py:155`). Both hosts already resolve identity via `otio_sync_core.identity`: `resolve_identity()["user"]` (or its override) is the account identity; the dialog's editable "You:" field is a separate display-name override layered on top (`identity_from_override`).

## Goals / Non-Goals

**Goals:**
- Detect a join that timed out with no master answering, and fail it visibly instead of self-electing.
- Surface the already-resolved account identity in both dialogs without touching how it's resolved or overridden.
- Add one more layer to host-default resolution (a config file) without changing the existing precedence for the layers already there.

**Non-Goals:**
- Not touching the mid-session master-failover path (`manager.py:3942-3950`, gated on `STATE_SYNCED`) — that's the separate, already-parked "silent departure" problem ([[project_master_failover_silent_departure]]), not the initial-join case this change addresses.
- Not adding a create-side check beyond what exists — create's "already exists" handling is unchanged.
- Not making the account-identity field editable via the config file or anywhere else in this change; it stays a read-only reflection of whatever `resolve_identity()` already produces.
- Not changing RabbitMQ exchange declaration semantics elsewhere in the codebase.

## Decisions

**1. Join-not-found detection reuses the existing discovery/self-election signal, not a new RabbitMQ-level probe.**

A session "existing" here means "a live master answered `WHO_IS_MASTER`" — exactly what the create path already tests via `_pending_create_check`. Mirroring that (rather than adding a `channel.exchange_declare(passive=True)` check) keeps both directions of the mismatch on the same signal and avoids a second failure mode: a fanout exchange can persist server-side with zero live peers, so a passive declare would report "exists" for a session that is not actually joinable, and would need its own timeout/unreachable-server discrimination that the discovery path already has for free.

*Alternative considered:* passive exchange/queue declare against RabbitMQ directly. Rejected — tests server-side residue, not liveness, and duplicates error-discrimination work the discovery timeout already does.

**2. Implementation shape mirrors the create-path check exactly, inverted.**

`_pending_create_check` (renamed/generalized as needed) is already captured per-connect-attempt. Add the join-side branch alongside the existing create-side branch in `_on_synced_once`: `if not _pending and self.manager.is_master:` — this attempt was a join, and self-election fired because nobody answered — disconnect immediately and show "Session Not Found: '<name>'" instead of leaving the peer connected as a lone master. The RV plugin gets the equivalent branch in its own connect completion path (`rvplugin/ori_sync/plugin.py`).

**3. Read-only account-identity field reuses the existing resolution call.**

`SessionDialog.qml`'s `python_callback("get_default_identity", {})` and RV's `resolve_identity()` call already run to seed the editable "You:" field. Add a second, non-editable field beside it in both dialogs, sourced from the same call — no new resolution path, no new wire data. Label it distinctly from "You:" (e.g. "Account:") so it isn't mistaken for the thing a shared-workstation user is supposed to edit.

**4. Config file is a new shared loader in `otio_sync_core`, consulted only for the host pre-fill default.**

Both plugin hosts already import `otio_sync_core.identity` directly, so a sibling module (e.g. `otio_sync_core.site_config`) is reachable from both without duplicating parsing logic. Precedence becomes: typed dialog value > `ORI_RMQ_HOST` env var > config file > hardcoded default. Exact file format/name is deferred to tasks — the spec already requires a missing or malformed file to fail open to today's behavior, so the choice doesn't affect correctness elsewhere.

## Risks / Trade-offs

- **A slow-to-answer master could produce a false "not found"** if its `I_AM_MASTER` lands just after the 2s window → Mitigation: same timeout and tolerance the create-path check already ships with today; this is not a new race, just a new place it's reported.
- **A second place a host value can be stale** (site config file vs. env var vs. dialog memory) → Mitigation: fixed, documented precedence; malformed/missing file is required by spec to fail open silently.
- **A read-only "Account:" field could be confused with "You:"** → Mitigation: distinct labels, matching how the Session State panel already distinguishes them.

## Migration Plan

Purely additive: no persisted state or wire-format changes. Rollback is reverting the touched files (`ori_sync_plugin.py`, `SessionDialog.qml`, `rvplugin/ori_sync/plugin.py`, `rvplugin/ori_sync/utils.py`, the new `site_config` module).
