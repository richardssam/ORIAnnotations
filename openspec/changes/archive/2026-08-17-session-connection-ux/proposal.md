## Why

The create/join flow in both RV and xStudio has three rough edges reported from real use: joining a session name that doesn't exist just connects anyway with no feedback; the dialog shows an editable display-name override but not the resolved OS account identity that peers can already see in the Session State panel, so there's no way to confirm which account you're about to connect as; and the RabbitMQ host has no way to be pre-configured per-site short of an environment variable set before launch, which `session-role-config` already established (for the role-policy case) is not a workable way to reach real users.

## What Changes

- Join validates that a session with the given name actually exists on the MQ server before completing the connection, and shows a clear error dialog if it doesn't (rather than silently joining/creating an empty session).
- The create/join dialogs (`SessionDialog.qml` in xStudio, `session_dialog()` in RV) gain a read-only display of the resolved account identity (`identity.local_identity()`'s `user` field) alongside the existing editable display-name ("You:") field, so a user can see which account they're connecting as before they commit.
- A config file, droppable into the plugin folder, can pre-define the RabbitMQ server (host/port) for a site — read before the `ORI_RMQ_HOST` environment variable / `localhost` default, so a site can ship a working default without every user setting an env var.

## Capabilities

### New Capabilities
- `session-join-validation`: Checking that a named session exists before completing a join, and surfacing a clear error when it doesn't.
- `session-config-file`: A plugin-folder config file that pre-defines connection settings (starting with the RabbitMQ host/port), read as a fallback layer beneath explicit dialog input and above the environment-variable/hardcoded default.

### Modified Capabilities
- `ori-session-management`: Adds a requirement that the create/join dialogs display the resolved account identity (`user`) read-only, distinct from and alongside the existing editable display-name override.

## Impact

- `xstudio_plugin/ori_sync/ori_sync_plugin.py` (`connect_to_session`, `_menu_join_session`), `xstudio_plugin/ori_sync/qml/ORISyncPlugin.1/SessionDialog.qml`
- `rvplugin/ori_sync/plugin.py` (`connect_to_session`, `do_join_session`), `rvplugin/ori_sync/utils.py` (`session_dialog`)
- `python/otio_sync_core/identity.py` (`local_identity`, `resolve_identity`) — read, not modified
- New: a config-file loader shared by both hosts (location TBD in design), and a passive existence check against the RabbitMQ session exchange/queue on join
