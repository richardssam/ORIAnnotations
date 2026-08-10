## 1. Shared state projection

- [x] 1.1 Create `python/otio_sync_core/session_state.py` with `session_state_snapshot(manager) -> dict` and `peer_role(peer)`. Qt-free, read-only, plain data.
- [x] 1.2 Cover it in `tests/otio_sync/test_session_state_snapshot.py`: peer ordering (self first), master/host flags, lease ownership per channel, missing app name, and that the returned dict does not alias manager state.
- [x] 1.3 Add `otio_sync_core/session_state.py` to `rvplugin/ori_sync/makepackage.csh` (the vendor list is hand-maintained; a missing module makes the RV plugin silently inert).

## 2. OpenRV panel

- [x] 2.1 Create `python/otio_sync_core/qml/OtioSyncStyle.qml` as a dark palette for the OpenRV panel. (Originally a conditional `XsStyleSheet` singleton — dropped with the shared-QML decision; see design.md 1b/3.)
- [x] 2.2 Create `python/otio_sync_core/qml/SessionStatePanel.qml` with a `ListView` for peers and a Debug Mode toggle.
- [x] 2.3 Create `python/otio_sync_core/ui_model.py` as a thin Qt adapter over `session_state_snapshot`.
- [x] 2.4 Implement `PeerListModel(QAbstractListModel)` polling at 2Hz, resetting only when the peer guid sequence changes and emitting `dataChanged` otherwise.
- [x] 2.5 Implement `SessionStateModel(QObject)` exposing `status`, `masterGuid`, `masterAppName`, `isHost`, `isDebug`, and `isSplitView`.
- [x] 2.6 Feed `isSplitView` from a host-supplied `local_view_provider` rather than from new `SyncManager` fields (design.md 1c).
- [x] 2.7 Add the "Session State..." and "Force Resync" menu items to the OpenRV plugin and launch the panel from them.
- [x] 2.8 Verify in OpenRV: panel renders and shows the session. (The split-view indicator was not separately exercised — it needs the two hosts deliberately on different sequences. `_local_view` was repointed at `_displayed_timeline_guid` after the last observation, so that path is the one to watch if the indicator ever looks wrong.)

## 3. xStudio

- [x] 3.1 Flatten the `Session|Connect` submenu into `Session` and add "Resync Session".
- [x] 3.2 Publish `session_state_snapshot` as JSON on a `Session State` plugin attribute (group `ori_sync_state`), pushed from the existing poll thread at 0.5s and only when the payload changed. Chosen over `python_callback` polling, which blocks xStudio's Qt main thread.
- [x] 3.3 Build `qml/ORISyncPlugin.1/SessionStatePanel.qml` as a native `XsWindow` styled from `XsStyleSheet`, binding the attribute via `XsModuleData`/`XsAttributeValue` — no timer in the panel — with a peer list and a Debug Mode toggle.
- [x] 3.4 Add the "Session State..." menu item and open the panel via `create_qml_item`.
- [x] 3.5 Verify in xStudio: menu placement and panel rendering confirmed working (2026-08-10).

## 4. Regression guard

- [x] 4.1 Confirm the change touches no sync-protocol semantics: `manager.py` and `rvplugin/ori_sync/playback_sync.py` are unmodified relative to `main`.
