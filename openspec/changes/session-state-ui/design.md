## Context

The SyncManager holds a comprehensive state for the entire session (peers, capabilities, leases) but currently has no cross-platform user interface to visualize it. See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- Provide a unified view of session participants, master, host, and broadcast leases.
- Write the state derivation exactly once so both hosts report identical session state.
- Look native in xStudio while providing a cohesive dark mode for OpenRV without requiring extensive stubs.

**Non-Goals:**
- Implementing the enforcement logic for `session-roles`. This UI provides the scaffolding to view roles and eventually assign them, but the core protocol changes for role validation are out of scope for this UI effort.
- Re-writing the existing web-based `sync_viewer` UI. `sync_viewer` will remain HTML/JS; this change targets the native desktop hosts.

## Decisions

**1. Share the state projection, not the view** *(revised — the original decision was to share the QML file; see "Why the shared-QML decision was reversed")*
- *Rationale*: `otio_sync_core/session_state.py` exposes `session_state_snapshot(manager) -> dict`, a Qt-free projection of the manager. OpenRV's Qt models and any future xStudio panel both read it. The semantics that could drift between hosts — role derivation, lease ownership, peer ordering — live there once and are covered by the ordinary pytest suite, which no QML file ever will be. Each host then writes only layout, in its own idiom.
- *Alternatives*: One shared `SessionStatePanel.qml` loaded by both hosts. Rejected — see below.

**1b. Why the shared-QML decision was reversed**
- OpenRV's plugin runs PySide6 in-process, so a Python model can be injected as a QML context property. xStudio's plugin is *not* Qt-Python: `create_qml_item()` hands a QML **string** to xStudio's own engine, and Python↔QML is `python_callback`. A PySide6 `QAbstractListModel` cannot be injected there at all, so the two hosts can never share the binding mechanism — only a file.
- Sharing the file alone bought little: the panel is ~180 lines of pure layout, with no domain logic to protect. Meanwhile it cost a style singleton whose every property was a `typeof XsStyleSheet !== 'undefined'` conditional, and it fought Goal 3 ("look native in xStudio") directly.
- The first attempt confirmed it in practice: xStudio wrapped the shared file in a `Loader`, which does not inherit the wrapper's properties (a QML file gets its own component scope), so the panel's bindings resolved to `undefined` — while the peer/lease derivation got reimplemented host-side anyway. Exactly the drift the sharing was meant to prevent.

**1c. An observability surface reads the core; it never extends it**
- *Rationale*: The first implementation wanted a "local vs shared view" indicator, the manager had no such field, and the path of least resistance was to add `local_timeline_guid`/`local_clip_guid` to `SyncManager` and to change `playback_state` from replace to merge on receipt — protocol hot-path semantics changed to feed a debug panel. That is the failure mode this rule exists to prevent.
- *Consequence*: `session_state_snapshot` is strictly read-only and returns plain data. State the manager does not hold comes from the **host** instead: `SessionStateModel` takes a `local_view_provider` callable that the RV plugin implements by reading its own view node.

**2. Qt Data Model (`PeerListModel`) — OpenRV only**
- *Rationale*: A `QAbstractListModel` subclass in Python wraps `session_state_snapshot` (not the manager directly). The QML `ListView` binds to this model. It is a thin adapter: role names map one-to-one onto snapshot keys, so an added field is a one-line change here and nothing else.
- *Reactivity Strategy*: Because `SyncManager` does not currently emit events for every internal state change (like a peer heartbeat or lease countdown), the model will use a simple `QTimer` to poll the manager at 2Hz. This completely isolates the UI from the network polling threads, avoiding lock contention and keeping the core API simple.
- *Alternatives*: Refactoring `SyncManager` to emit a large surface area of Qt or Python callbacks. Rejected as it risks slowing down the core processing loop and tightly couples the core to the UI.

**3. Theming (`OtioSyncStyle.qml`)** *(revised)*
- *Rationale*: A plain `QtObject` palette for the OpenRV panel. The conditional `typeof XsStyleSheet !== 'undefined'` form went away with decision 1: an xStudio-native panel reads `XsStyleSheet` directly, so nothing needs to straddle both. Status colours stay hardcoded to match `sync_viewer`, so the same state reads the same everywhere.
- *Alternatives*: Writing dummy `xStudio 1.0` stubs for OpenRV. Rejected — no longer needed once the hosts stop sharing a view.

**4. xStudio reaches the projection through a plugin attribute, not `python_callback`**
- *Rationale*: `python_callback` **blocks xStudio's Qt main thread** — `do_session_connect` already spawns a thread precisely to escape it — so a panel polling it at 2Hz would block the UI at 2Hz for as long as it stayed open. Instead the plugin publishes `json.dumps(session_state_snapshot(...))` on a `Session State` attribute from the poll thread it already runs (the same thread that already writes `status_attr`), and the panel binds to it with `XsModuleData`/`XsAttributeValue`. Nothing crosses a thread boundary at read time and the panel needs no timer at all — QML re-renders when the value changes.
- *Push policy*: every 0.5s, matching the OpenRV panel's period, and only when the serialised payload differs from the last one. An unchanged `set_value` would wake every binding on the attribute twice a second for nothing.
- *Alternatives*: `python_callback` polling (rejected — blocks the UI thread); a C++-side model (rejected — far more machinery than a debug panel warrants).

**5. Each host writes its own view**
- *Rationale*: With the projection shared, the remaining per-host code is layout. OpenRV's panel is PySide6/QQuickView; xStudio's is an `XsWindow` in `qml/ORISyncPlugin.1/`, instantiated through the plugin's existing `create_qml_item` + `qml_folder` mechanism exactly like `SessionDialog`. Each looks native in its host, and neither can drift on what the session state *is*.
- *Trade-off*: the two layouts must be kept roughly in step by hand. Accepted — see the risk below.

## Risks / Trade-offs

- **Risk**: The 2Hz polling timer in `PeerListModel` might introduce a maximum 500ms visual lag between a lease transferring and the UI updating.
  *Mitigation*: This is acceptable for a debug and management UI. It is not used for real-time video sync, so a slight visual delay is better than complex event wiring.
- **Risk**: The two hosts' panels drift visually, since they no longer share a layout file.
  *Mitigation*: Accepted deliberately — "looks native in its host" is worth more here than pixel identity across hosts, and the *state* they display cannot drift because it comes from one projection.
- **Risk**: `session_state_snapshot` rebuilds the peer list on every poll rather than diffing.
  *Mitigation*: Peer counts are in single digits and the projection is plain dict-building; `PeerListModel` compares snapshots and only resets the model when the guid sequence actually changes.
