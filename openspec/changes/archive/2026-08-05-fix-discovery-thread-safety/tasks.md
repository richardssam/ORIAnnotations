## 1. Manager election API

- [x] 1.1 Add `SyncManager.elect_self_as_master(broadcast: bool = True)` in `python/otio_sync_core/manager.py`, in the "Master Election & Session State" section next to `broadcast_master_response`: set `is_master = True`, `master_guid = self_guid`, send `I_AM_MASTER` when `broadcast`, then `_set_status(STATE_SYNCED)` — in that order, so `on_synced` callbacks observe a fully-elected manager.
- [x] 1.2 Document the method (docstring) and update the `SyncManager` class docstring's lifecycle note (`manager.py:124`) so it points at `elect_self_as_master()` instead of describing callers doing the election themselves.
- [x] 1.3 Migrate the manager-internal master-failover path (`manager.py:1719-1727` in `tick()`) to call `elect_self_as_master()`, keeping `_last_who_is_master_time = None` at the call site.

## 2. xStudio: move election off the discovery thread

- [x] 2.1 Reduce `_discovery_timeout_task` (`xstudio_plugin/ori_sync/ori_sync_plugin.py:496`) to sleep, check `self.manager and self.manager.status == STATE_DISCOVERING`, and `self._cmd_queue.put(("self_elect", {}))` — no `register_timeline`, no `build_otio_timelines`, no election, no `_set_status`.
- [x] 2.2 Add a `self_elect` branch to `_execute_command` (`ori_sync_plugin.py:642`) that re-checks `self.manager and self.manager.status == STATE_DISCOVERING`, registers `self.builder.build_otio_timelines()` timelines, then calls `self.manager.elect_self_as_master()`; the re-check makes a late command a silent no-op after a peer's `I_AM_MASTER` or a `leave_session`.
- [x] 2.3 Migrate the `state_request_timeout` branch of `_handle_manager_event` (`ori_sync_plugin.py:805`) to `self.manager.elect_self_as_master()`, keeping the timeline registration ahead of it.
- [x] 2.4 Confirm no remaining `is_master =`, `master_guid =`, or `_set_status(` assignment in `xstudio_plugin/` (grep), and that `STATE_SYNCED` is only read there, not assigned.

## 3. OpenRV: shed the duplicated ritual

- [x] 3.1 Rewrite `_init_as_master` (`rvplugin/ori_sync/plugin.py:356`) as `self.sync_manager.elect_self_as_master(broadcast=False)` followed by `self._deferred_master_init()`, preserving the late announce.
- [x] 3.2 Leave the `broadcast_master_response()` at the end of `_deferred_master_init` (`plugin.py:406`) in place, and comment it as the deferred half of the election so it is not later mistaken for a stray broadcast.
- [x] 3.3 Confirm no remaining direct election-state assignment in `rvplugin/` (grep for `is_master =`, `master_guid =`, `_set_status(`).

## 4. Tests

- [x] 4.1 Add `tests/otio_sync/test_master_election.py` covering `elect_self_as_master`: end state (master flag, `master_guid == self_guid`, `STATE_SYNCED`, exactly one `I_AM_MASTER` on the fake network), `broadcast=False` applying local state with no message sent, and re-election on an already-`SYNCED` master not re-firing `on_synced`.
- [x] 4.2 Add an ordering assertion: an `on_synced` callback registered before election observes `is_master` and `master_guid` already set.
- [x] 4.3 Add a test that the `I_AM_MASTER` envelope produced by `elect_self_as_master` is identical to the one `broadcast_master_response()` produces (wire-compatibility guard).
- [x] 4.4 Test the xStudio `self_elect` command handler in isolation (no live xStudio): drained while `STATE_DISCOVERING` → registers and elects; drained after status left `STATE_DISCOVERING` or with `manager is None` → no-op, no broadcast.
- [x] 4.5 Run the existing unit suite (`python -m pytest tests/otio_sync`) and confirm no regressions, in particular `test_protocol_messages.py`'s `I_AM_MASTER` handling.

## 5. Integration verification

- [x] 5.1 Reinstall the RV package (`rvplugin/<pkg>/reinstall.csh`) before any in-app RV check — RV loads the installed copy, not the repo source.
- [x] 5.2 Run the sync test suite's discovery/election scenarios (solo start self-election, two-peer master/client) for both host orderings and confirm each session ends with exactly one master.
- [x] 5.3 Verify solo xStudio start: after `DISCOVERY_TIMEOUT` the peer is `SYNCED` as master with its timelines registered, and the log shows the election on the poll thread rather than the discovery thread.
- [x] 5.4 Verify a joining peer against an RV master started with an OTIO import in flight, confirming the deferred announce still lands after the timelines exist and the joiner receives a non-empty snapshot.
