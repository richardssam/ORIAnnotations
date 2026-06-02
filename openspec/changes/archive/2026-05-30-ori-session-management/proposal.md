## Why

Both the RV and xStudio sync plugins currently hardcode the session name (`otio-sync-demo`) and always auto-connect on startup, giving users no way to choose or switch sessions. Teams working across multiple reviews need to select which session to join, and studio pipelines need a way to pre-configure the target host and session via environment variables.

## What Changes

- Add `ORI_SESSION=[host:]session_name` environment variable: when set, both plugins auto-connect to the specified session on startup (replacing the hardcoded default)
- Add `ORI_RMQ_HOST` environment variable: sets the default RabbitMQ host pre-filled in connection dialogs
- Add **Create Session** menu item to both plugins: prompts for host + session name; warns if a session is already running (user joined as peer rather than starting fresh)
- Add **Join Session** menu item to both plugins: prompts for host + session name; connects as a peer expecting an existing master
- Add **Leave Session** menu item to both plugins: disconnects from the current session
- Remove hardcoded `SYNC_SESSION_ID = "otio-sync-demo"` from the RV plugin
- Gate xStudio plugin auto-connect on `ORI_SESSION` being set (currently always auto-connects)
- Add a two-field session dialog (host + session name) to the xStudio plugin as a new QML file

## Capabilities

### New Capabilities

- `ori-session-management`: Session lifecycle UI — Create/Join/Leave menu items, env var–driven auto-connect, two-field connection dialog, and post-connect "session already existed" warning for Create

### Modified Capabilities

- `openrv-sync-plugin`: Session ID is no longer hardcoded; host and session are now runtime-configurable via env vars or menu
- `otio-sync-demo-plugin`: xStudio plugin no longer auto-connects unconditionally; connection is gated on `ORI_SESSION`

## Impact

- `rvplugin/openrv_sync_plugin/plugin.py`: remove hardcoded session constant, add connect/disconnect methods, dynamic menu rebuild via `defineModeMenu`
- `xstudio_plugin/ori_sync/ori_sync_plugin.py`: gate auto-connect, add menu items, wire new QML dialog
- New file: `xstudio_plugin/ori_sync/qml/OriSync.1/SessionDialog.qml`
- No changes to `otio_sync_core` — `manager.is_master` and `on_synced` callback already support the post-connect warning
- No protocol changes; this is purely UI and startup behaviour
