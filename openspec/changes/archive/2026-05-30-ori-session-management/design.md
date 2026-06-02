## Context

Both plugins currently hardcode `SYNC_SESSION_ID = "otio-sync-demo"` (RV) or default `session_id_attr` to `"otio-sync-demo"` (xStudio) and connect unconditionally on startup. The `message_queue_implementation.py` from the live_review_experiment established a clean 3-state menu pattern (no server → connected to server → in session) using `RV_AMQP_AUTO_JOIN` / `RV_AMQP_DEFAULT_EXCHANGE` env vars and Qt `QInputDialog`. We adapt that pattern for the ORIAnnotations protocol.

The xStudio plugin already has `mq_host_attr` (persisted preference) and `connect_to_session()` / `disconnect()` methods — the infrastructure is in place. The RV plugin has `_setup_sync()` called unconditionally from `__init__`.

`SyncManager.is_master` and the `on_synced` callback already exist in `otio_sync_core`, giving us post-connect master detection for free.

## Goals / Non-Goals

**Goals:**
- `ORI_SESSION=[host:]name` env var triggers auto-connect on startup (both plugins)
- `ORI_RMQ_HOST` env var sets the default host pre-filled in connection dialogs
- Create Session / Join Session / Leave Session menu items in both plugins
- Two-field connection dialog (host + session name)
- Post-connect warning when "Create Session" results in joining an existing session (not master)
- No changes to `otio_sync_core` or the sync protocol

**Non-Goals:**
- RabbitMQ authentication / TLS support (localhost no-auth only for now)
- Session listing / discovery (no registry of active sessions)
- Forcibly evicting an existing master when "Create" is chosen
- xStudio dynamic menu rebuild (items stay visible; callbacks guard against calling when connected)

## Decisions

### ORI_SESSION format: `[host:]session_name`

Parsed by splitting on the first `:`. If no colon, host defaults to `localhost` (or `ORI_RMQ_HOST` if set). IPv6 addresses are a known edge case but not a current requirement; document as unsupported.

**Alternative**: Separate `ORI_RMQ_HOST` and `ORI_SESSION_NAME` env vars. Rejected — one variable is simpler for the common case (auto-join a named session on a known host). `ORI_RMQ_HOST` still exists as a standalone default for the dialog.

### ORI_SESSION auto-connect is always "join" semantics

The env var never triggers the "existing session" warning — it is assumed the user knows the session exists. Only the interactive "Create Session" menu item performs the post-connect master check.

**Alternative**: Separate `ORI_CREATE_SESSION` env var. Rejected — unnecessary complexity for automated startup.

### Post-connect "session already existed" warning for Create

After `connect_to_session()`, register a one-shot `on_synced` callback that checks `manager.is_master`. If not master, display a warning popup. This avoids any pre-probe: the regular WHO_IS_MASTER handshake determines the outcome, and we react to it.

The callback stores whether the current connection was initiated via "Create" (a flag set before connecting) to suppress the warning for "Join" and `ORI_SESSION` auto-connects.

**Alternative**: Pre-probe with a temporary `SyncManager`. Rejected — adds ~2 s latency and requires tearing down and recreating the connection.

### RV menu: static items with dynamic grey state via `defineModeMenu`

The existing RV menu uses static items registered at `init()`. We replace it with a `property menu` pattern matching `message_queue_implementation.py`: a Python property returns a different menu list depending on `_in_session`, and `defineModeMenu` is called whenever that state changes.

Two states:
- Not in session: "Create Session…", "Join Session…", separator, "Add Clip…", "Sync Status"
- In session: "Leave Session (name)" (greyed Create/Join omitted to reduce clutter), separator, "Add Clip…", "Sync Status"

**Alternative**: Always show all items, grey out contextually. Requires state lambdas on every item — more code, less clean.

### xStudio menu: always-visible items, guard in callback

`insert_menu_item` has no state callback. Create/Join callbacks check `self.manager is not None` and show a `popup_message_box("Already connected to '{name}'. Leave first.")`. Leave callback checks `self.manager is None` and does nothing.

### xStudio session dialog: new QML file `SessionDialog.qml`

A simple `XsWindow` with two `XsTextField` rows (MQ Host, Session Name) and Cancel / Connect buttons, following the pattern of `ORIAnnotationsImportDialog.qml`. Invoked via `create_qml_item(...)` with a `python_callback` on Connect.

The dialog is shared between Create and Join — the only difference is which Python method is called on confirm (the dialog passes a `mode` field in the callback data).

### RV session dialog: `QDialog` with two `QLineEdit`s

Created inline in the menu callbacks using PySide6, matching the `QInputDialog.getText` pattern from `message_queue_implementation.py` but with two fields. No separate file needed.

## Risks / Trade-offs

**Post-connect warning race** → The `on_synced` callback fires on the poll thread. Displaying a warning from the poll thread must use `popup_message_box` (xStudio) or `rv.commands.popupMenu` scheduled via timer (RV). Both are fire-and-forget / thread-safe.

**xStudio menu not visually greyed** → Create/Join items are always clickable. Mitigated by the guard message which is immediate and clear.

**`defineModeMenu` requires the exact menu name** → The menu name used in `init()` must match exactly. Using a class constant avoids typos.

**ORI_SESSION with no colon and a session name containing a colon** → Documented as unsupported. Session names should be alphanumeric + hyphens.

## Open Questions

- Should `ORI_RMQ_HOST` also override the persisted `mq_host_attr` preference at startup, or only pre-fill the dialog? (Current decision: override on startup so the preference reflects what was used.)
- Should "Leave Session" in RV prompt for confirmation, or disconnect immediately? (Current decision: immediate, matching the existing `disconnect()` behaviour in xStudio.)
