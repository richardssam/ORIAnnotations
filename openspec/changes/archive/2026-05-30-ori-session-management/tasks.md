## 1. Shared Utilities — Environment Variable Parsing

- [x] 1.1 Add `_parse_ori_session(env_val)` helper in RV `plugin.py` that splits `[host:]name` on the first colon, returning `(host, session_name)` with `localhost` as default host
- [x] 1.2 Add equivalent `_parse_ori_session(env_val)` helper in xStudio `ori_sync_plugin.py` (or a shared module if practical)
- [x] 1.3 Verify that `ORI_RMQ_HOST` is read and used as fallback host when the session string has no host component (both plugins)

## 2. RV Plugin — Session Connect/Disconnect Methods

- [x] 2.1 Remove hardcoded `SYNC_SESSION_ID = "otio-sync-demo"` constant from `plugin.py`
- [x] 2.2 Add `connect_to_session(host, session_name)` method: creates `SyncManager` + `RabbitMQNetwork`, calls `start_session()`, stores `_current_session_name` and `_current_host`, calls `_rebuild_menu()`
- [x] 2.3 Add `disconnect_from_session()` method: stops poll thread, shuts down network, sets `sync_manager = None`, clears `_current_session_name`, calls `_rebuild_menu()`
- [x] 2.4 Move shared-timeline bootstrap (`SYNC_DEMO_TRACK_UUID` setup) into `connect_to_session()` so it only runs when actually connecting

## 3. RV Plugin — Dynamic Menu

- [x] 3.1 Add `_in_session` property returning `bool(self.sync_manager is not None)`
- [x] 3.2 Add `MENU_NAME = "OTIOSync"` class constant (used as the `defineModeMenu` key — must match the name passed to `self.init()`)
- [x] 3.3 Add `_build_menu()` method returning the correct menu list for the current state: disconnected (Create, Join, separator, Add Clip disabled, Sync Status) or connected (Leave Session (name), separator, Add Clip, Sync Status)
- [x] 3.4 Add `_rebuild_menu()` method that calls `rv.commands.defineModeMenu(self.MENU_NAME, self._build_menu(), True)`
- [x] 3.5 Replace the static `menus = [...]` in `__init__` with the initial disconnected menu via `_build_menu()`
- [x] 3.6 Call `_rebuild_menu()` at the end of `connect_to_session()` and `disconnect_from_session()`

## 4. RV Plugin — Session Dialogs and Menu Callbacks

- [x] 4.1 Add `_session_dialog(title)` method: shows a PySide6 `QDialog` with two `QLineEdit`s (MQ Host pre-filled from `ORI_RMQ_HOST` or `"localhost"`, Session Name blank), returns `(host, name)` or `(None, None)` on cancel
- [x] 4.2 Add `do_create_session(event=None)` callback: calls `_session_dialog("Create Session")`, connects, then registers a one-shot `on_synced` callback that checks `manager.is_master` and shows a warning via `rv.commands.popupMenu` or `QtWidgets.QMessageBox` if not master
- [x] 4.3 Add `do_join_session(event=None)` callback: calls `_session_dialog("Join Session")` and connects (no post-connect warning)
- [x] 4.4 Add `do_leave_session(event=None)` callback: calls `disconnect_from_session()`

## 5. RV Plugin — Startup Auto-Connect

- [x] 5.1 In `__init__`, after plugin init, check `ORI_SESSION`: if set, parse with `_parse_ori_session`, call `connect_to_session(host, name)` using `QtCore.QTimer.singleShot(0, ...)` to defer until after RV UI is ready
- [x] 5.2 If `ORI_SESSION` is not set, leave `sync_manager = None` and show disconnected menu

## 6. xStudio Plugin — Remove Unconditional Auto-Connect

- [x] 6.1 Remove the `connect_to_session()` call (and its surrounding try/except) from `__init__`
- [x] 6.2 In `__init__`, read `ORI_SESSION`: if set, parse host and name; if `ORI_RMQ_HOST` is set and no host in `ORI_SESSION`, use `ORI_RMQ_HOST`; call `connect_to_session(host, name)` deferred (after `connect_to_ui()`)
- [x] 6.3 Override `mq_host_attr` preference with the resolved host from `ORI_SESSION` / `ORI_RMQ_HOST` at startup so the stored preference reflects what was used

## 7. xStudio Plugin — Session Menu Items

- [x] 7.1 Add `insert_menu_item` call for "Create Session…" under `"Session|Connect"` path, callback `_menu_create_session`
- [x] 7.2 Add `insert_menu_item` call for "Join Session…" under `"Session|Connect"` path, callback `_menu_join_session`
- [x] 7.3 Add `insert_menu_item` call for "Leave Session" under `"Session|Connect"` path, callback `_menu_leave_session`
- [x] 7.4 Implement `_menu_create_session`: open `SessionDialog.qml` via `create_qml_item`; on confirm with `mode="create"`, call `connect_to_session(host, name)` via worker queue; register post-connect master check
- [x] 7.5 Implement `_menu_join_session`: open `SessionDialog.qml` via `create_qml_item`; on confirm with `mode="join"`, call `connect_to_session(host, name)` via worker queue
- [x] 7.6 Implement `_menu_leave_session`: if `self.manager is None`, call `popup_message_box` with "Not currently in a session"; otherwise call `disconnect()` via worker queue

## 8. xStudio Plugin — Post-Connect "Create" Warning

- [x] 8.1 Add `_pending_create_check: bool` flag, set to `True` before connecting via "Create Session…", `False` for all other connect paths
- [x] 8.2 In `connect_to_session()`, if `_pending_create_check` is set, register a one-shot `on_synced` callback (or equivalent status check after STATE_SYNCED) that calls `popup_message_box` with the "session already existed" warning if `not manager.is_master`
- [x] 8.3 Clear `_pending_create_check` after the check fires (regardless of outcome)

## 9. New QML — SessionDialog

- [x] 9.1 Create `xstudio_plugin/ori_sync/qml/OriSync.1/SessionDialog.qml`: `XsWindow` with two `XsTextField` rows (MQ Host, Session Name), Cancel and Connect buttons, `python_callback("do_session_connect", {host, name, mode})` on Connect
- [x] 9.2 Add `do_session_connect(data)` method to `ORISyncPlugin` that dispatches to `_menu_create_session` or `_menu_join_session` based on `data["mode"]`
- [x] 9.3 Ensure the QML folder is declared in the plugin `__init__` so the new QML file is discoverable (update `qml_folder` arg if needed)

## 10. Verification

- [x] 10.1 Test RV: launch without `ORI_SESSION` — menu shows Create/Join greyed Add Clip; verify no connection attempted
- [x] 10.2 Test RV: launch with `ORI_SESSION=localhost:test-session` — verify auto-connect and menu switches to Leave state
- [x] 10.3 Test RV: use Create Session with a fresh exchange — verify master role and no warning
- [x] 10.4 Test RV: use Create Session when another instance is already master — verify "session already existed" warning appears
- [x] 10.5 Test xStudio: launch without `ORI_SESSION` — verify plugin starts disconnected, Create/Join items visible
- [x] 10.6 Test xStudio: launch with `ORI_SESSION=localhost:test-session` — verify auto-connect
- [x] 10.7 Test xStudio: use Create Session with a fresh exchange — verify master role and no warning
- [x] 10.8 Test xStudio: use Create Session when another instance is master — verify warning popup
- [x] 10.9 Test cross-plugin: xStudio creates session, RV joins via Join Session dialog — verify full sync
