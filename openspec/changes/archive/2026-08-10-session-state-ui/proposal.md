## Why

As the OTIO Sync Protocol has evolved to support broadcast ownership (leases) and eventually session roles, the state of a session is becoming increasingly complex. It is currently difficult to know who is driving a session, who holds leases for position, display, or structure, and what roles connected peers hold. A unified, cross-platform Session State UI is needed across OpenRV and xStudio to expose this state both for debugging and for future session owners to manage roles.

## What Changes

- Add `otio_sync_core/session_state.py` exposing `session_state_snapshot(manager) -> dict`: a Qt-free, read-only projection of peers, roles, elected master/host, and broadcast leases. This is what the hosts share.
- Implement a PySide6 `QAbstractListModel` data bridge over that projection, polled at 2Hz, feeding the OpenRV QML panel.
- Add `SessionStatePanel.qml` and a dark `OtioSyncStyle.qml` palette for the OpenRV panel.
- Add a "Debug Mode" toggle to the UI that exposes raw GUIDs and lease mechanics for developers, while providing a clean "Collaborators" view for end users.
- Add "Force Resync"/"Resync Session" to both hosts' session menus, and flatten xStudio's `Session|Connect` submenu into `Session`.
- Add a native xStudio panel (`XsWindow`, styled from `XsStyleSheet`) over the same projection, fed by a `Session State` plugin attribute the poll thread pushes — not by `python_callback`, which blocks xStudio's Qt main thread.

## Capabilities

### New Capabilities
- `session-state-ui`: A shared read-only projection of session state — connected peers, their roles, and broadcast leases — plus the OpenRV panel that renders it. The projection is host-agnostic; views are per-host.

### Modified Capabilities
- `openrv-sync-plugin`: the "Sync Status" menu item becomes "Session State…" (opens the panel rather than logging a line), and the connected menu gains "Force Resync".
- `otio-sync-demo-plugin`: the static menu items present in both connection states are now "Add Clip to Timeline…" and "Session State…".
- `rv-plugin-module-structure`: the menu-callback roster replaces `do_show_status` with `do_show_session_state` and adds `do_resync`.

## Impact

- **otio_sync_core**: Will gain the Qt-free `session_state` projection, new QML assets, and a PySide6 UI model module.
- **OpenRV Plugin**: Will add a menu item or button to launch the shared QML panel.
- **xStudio Plugin**: Will add a menu item or button to launch the shared QML panel.
- **sync_viewer**: Unaffected directly, though it already implements a basic version of this state tracking via HTML/JS.
