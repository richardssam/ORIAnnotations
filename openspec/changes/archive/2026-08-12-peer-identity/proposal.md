## Why

The sync protocol carries no identity. `PEER_ANNOUNCE` sends `peer_guid`, `app`, and `capabilities` — nothing that says *who* is on the other end. The Session State panel `session-state-ui` shipped therefore identifies a peer as an application name and a GUID prefix:

```
openrv (You)        GUID: 7f3a1c02-…
xstudio             GUID: c419e8b7-…
openrv              GUID: 0090c5d3-…
```

In a two-peer debugging session that is tolerable. In the 20–30-person sessions `session-roles` targets, it is unusable for the thing the panel exists to support: a session owner deciding who should be a driver cannot act on a GUID. The same gap already costs us in test triage — every soak log identifies peers by GUID prefix, and matching those to machines is done from memory.

This change adds identity as a peer-table field set, resolved from the local machine today and from a login service later, without the protocol needing to know which.

### Current state

| Landed | Where |
|---|---|
| Peer table with app + capabilities | `SyncManager._peers`, populated by `PEER_ANNOUNCE` |
| Peer roster carried to joiners | `SyncManager._peer_roster()` / `adopt_peers()`, in `STATE_SNAPSHOT` |
| Liveness and departure | `peer-departure` — 5 s heartbeat, 15 s timeout, `PEER_DEPART`, `drop_peer()` |
| A Qt-free projection both hosts render | `otio_sync_core.session_state.session_state_snapshot` |
| Panels over that projection, with a Debug Mode | `session-state-ui` — OpenRV QML panel, xStudio native panel |

So the surface that wants identity exists, is shared, and has somewhere to put the fields a normal user should not see.

## What Changes

- **A provider seam in core.** `otio_sync_core/identity.py` exposes `local_identity() -> dict`, the one place that knows where identity comes from. Today it reads the local machine; a future web login replaces this function and nothing else.
- **Identity fields on the peer table**, propagated by both existing paths — `PEER_ANNOUNCE` and the `STATE_SNAPSHOT` peer roster — so a peer learned from a snapshot is as identifiable as one learned from an announcement.
- **A derived display name**, computed in `session_state_snapshot` rather than sent on the wire, so both hosts render the same string from the same rule.
- **A join-time override**, so a user on a shared or mislabelled machine can say who they are, and so the test harness can drive deterministic identities.
- **Panel display**: the peer row shows the display name and application; `user` and `host` appear under the existing Debug Mode toggle.

### Field set

| Field | Source today | Purpose |
|---|---|---|
| `user` | `getpass.getuser()` | Stable account id — the key an identity-scoped lookup should use |
| `first_name` / `last_name` | `pwd.getpwnam(user).pw_gecos`, best effort | Human-readable name |
| `host` | `socket.gethostname()` | Which machine — distinguishes one user on two seats |
| `source` | `"local"` | Where the identity came from: `"local"`, `"override"`, later `"oidc"` |

`display_name` is **not** a wire field. The projection derives it as `"First Last"`, falling back to `user`, falling back to `app`. Deriving it once means the two hosts cannot drift on how a half-populated identity renders — the failure mode this codebase has hit repeatedly with hand-replicated behaviour.

The field names are chosen to map onto standard identity-provider claims (`given_name`, `family_name`, `preferred_username`) so that swapping `local_identity()` for an authenticated source is a change of provider, not a change of protocol.

## Impact

- **Core**: new `otio_sync_core/identity.py`; `PeerAnnounce` gains an identity section; `_peer_roster()` / `adopt_peers()` carry it; `session_state_snapshot` exposes it and derives `display_name`.
- **Plugins**: both join/create dialogs gain an optional identity override; both panels render the display name, with `user`/`host` behind Debug Mode. No changes to any broadcast or apply path.
- **Packaging**: `identity.py` **must** be added to `rvplugin/ori_sync/makepackage.csh`'s hand-maintained vendoring list in the same commit. `__init__.py` imports inside `try/except ImportError`, so an omission leaves the RV plugin connected but inert — this exact fault shipped once already with `authority.py`.
- **Protocol**: backwards compatible. A peer that sends no identity renders exactly as it does today (app + GUID); an absent identity is never rendered as a blank row.
- **Trust**: identity is self-declared and unverified, consistent with an unauthenticated broker. It identifies cooperating participants; it does not authenticate them.
- **`session-roles`**: this change is a prerequisite for its role-administration UI, and its identity-keyed role memory (see that change's D3) depends on the `user` field landing here.
- **sync_viewer**: may declare an identity so it appears as something other than an anonymous peer; not required.

## Capabilities

### New Capabilities

- `peer-identity`: the identity field set carried per peer, the provider seam that resolves it (local machine today, a login service later), propagation over both the announce and snapshot-roster paths, the join-time override, and the rule that display strings are derived in the shared projection rather than sent.

### Modified Capabilities

- `otio-sync-core`: `PEER_ANNOUNCE` and the `STATE_SNAPSHOT` peer roster carry identity; identity is resolved once at session start rather than per message.
- `session-state-ui`: peers are identified by display name rather than by GUID; `user` and `host` are Debug Mode fields.
- `protocol-message-docs`: identity fields on `PEER_ANNOUNCE` and in the `STATE_SNAPSHOT` peer roster.
- `ori-session-management`: session start resolves identity, and the join flow may override it.
- `openrv-sync-plugin`: identity override on the session dialog; panel renders identity.
- `xstudio-plugin-module-structure`: identity override on the session dialog; panel renders identity.
