## Why

xStudio's discovery-timeout task runs on its own daemon thread and mutates the `SyncManager` directly (`register_timeline`, `is_master`, `master_guid`, `broadcast_master_response`, `_set_status`) concurrently with the poll thread's `manager.tick()` — violating the plugin's documented threading model and the spec'd invariant that only the poll thread touches the manager after startup. This is a latent race, not a style issue. Separately, the same four-line self-election ritual is duplicated in three places (xStudio discovery timeout, xStudio `state_request_timeout` handling, RV `_init_as_master`), each reaching into manager privates — and the upcoming `session-roles` Phase 2 needs role-aware election, which should build on one API rather than three copies.

## What Changes

- `SyncManager` gains a single election operation, `elect_self_as_master()`, encapsulating the election state transitions (set `is_master`/`master_guid`, broadcast `I_AM_MASTER`, transition status to `STATE_SYNCED`). Plugins stop mutating `is_master`, `master_guid`, and `_set_status` directly for election.
- xStudio's discovery-timeout thread no longer touches the manager: on timeout it enqueues a `self_elect` command; the poll thread drains it and performs timeline registration plus election, restoring the single-writer invariant.
- xStudio's `state_request_timeout` handler and RV's `_init_as_master` migrate to the new API (RV's migration is mechanical — RV is single-threaded on the Qt main thread, so it has no race; it just sheds the duplicated ritual).
- No behavioral change intended: election timing, message sequence, and wire format are unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `otio-sync-core`: new requirement — self-election SHALL be performed via a single manager operation that owns the election state transitions; callers SHALL NOT mutate election state (`is_master`, `master_guid`, sync status) directly.
- `xstudio-plugin-module-structure`: the existing "Threading invariant preserved" requirement gains coverage for discovery — the discovery-timeout path SHALL NOT access the `SyncManager` from the timeout thread; self-election SHALL be routed through `_cmd_queue` and executed on the poll thread.

## Impact

- **Code**: `otio_sync_core/manager.py` (new method), `xstudio_plugin/ori_sync/ori_sync_plugin.py` (`_discovery_timeout_task`, `_execute_command`, `_handle_manager_event`), `rvplugin/ori_sync/plugin.py` (`_init_as_master`, `_handle_action`).
- **Protocol**: none — same messages in the same order.
- **Downstream**: `session-roles` Phase 2 (role-aware master election) builds on `elect_self_as_master()` instead of extending three duplicated rituals; this change should land first.
- **Testing**: the existing sync test suite's discovery/election scenarios (solo start self-election, two-peer master/client, state-request timeout) must pass unchanged; RV package requires `reinstall.csh` before in-app verification.
