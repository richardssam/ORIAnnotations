## Why

In large review sessions — 20–30 people in dailies or screenings — any participant can inadvertently change the selection, scrub to a different shot, add or remove media, or draw on screen. The protocol is effectively a megaphone in a library.

| Session size | Current model | Problem |
|---|---|---|
| 2–3 peers | Everyone can do everything | Works fine |
| 5–10 peers | Occasional conflicts | Manageable with coordination |
| 20–30 peers | Any of 30 people can change selection, scrub, draw, or add media | Chaotic and unusable |

This change introduces a three-tier permission model — **driver / reviewer / viewer** — enforced at the broadcast choke point that already exists.

> **Split from the original two-phase proposal on 2026-08-07.** Phase 1 (broadcast ownership / write leases) is now the separate change `broadcast-ownership`. The two were separated because **this change does not depend on it**: the role check composes with *category authority*, and category authority already exists and is enforced for visibility. The role table can land against today's code with zero leases. Bundled together, this unblocked half was hostage to a blocked one — `broadcast-ownership`'s guard-deletion step is gated on `fix-visibility-authority-bypass`, and nothing here is.

### Current state

`host-owned-visibility` (archived 2026-08-06) established the enforcement point and the vocabulary this change plugs into. What already exists in `python/otio_sync_core/authority.py` and `manager.py`:

| Landed | Where |
|---|---|
| A category table mapping each `broadcast_*` method to a category | `authority.BROADCAST_CATEGORIES` — `visibility` / `position` / `annotation` / `structure` |
| `SENT` / `SUPPRESSED` returned from every `broadcast_*` | `authority.SENT`, `authority.SUPPRESSED` (`broadcast_add_annotation` excepted — callers need the clip GUID, and annotation is never gated) |
| Field-group enforcement inside one core method | `SyncManager._enforce_visibility` → `authority.strip_visibility_fields` |
| The invariant that plugins never test authority | `test_no_plugin_gates_a_broadcast_on_being_host` |
| A runtime kill switch, read per call | `ORI_VISIBILITY_AUTHORITY=0` (`authority.enforcement_enabled`) |
| Host election as a pure function of the peer table | `authority.elect_host_guid`, with `HOST_PREFERENCE` ranking and GUID tie-break |
| A peer table with app + capabilities | `SyncManager._peers`, populated by the `PEER_ANNOUNCE` message |
| Authority state carried to late joiners | `StateSnapshot.host_guid`, adopted via `adopt_host()` — omitted when unset, `None` ignored on receipt |

The only distinguished concepts today are **master** (state-sync: holds the canonical snapshot) and **host** (visibility authority). Neither says anything about what a *participant* is permitted to emit — every peer has identical broadcast permissions, bounded only by category authority.

## Three-tier role model

Introduce `driver`, `reviewer`, and `viewer` roles that control which field groups a peer is *allowed* to emit:

- **Driver**: Full control — visibility (when host), content mutations, annotations, position. Equivalent to today's behaviour. Only a driver is eligible for host.
- **Reviewer**: Can annotate (draw, text) and move within what is shown, but cannot change *what* is shown or modify content. Intended for leads/supervisors who need to mark up shots but shouldn't drive the session.
- **Viewer**: Passive observer — receives all state but emits nothing session-visible. The existing `sync_viewer` is a natural viewer. Intended for the majority of a large screening audience.

### Per-field-group permission matrix

**Cut for field-group enforcement.** An earlier draft had one `PLAYBACK_SET` row, which is no longer expressible: `host-owned-visibility` D1 put the authority boundary *inside* that message, between `view_mode`/`clip_guid` and `current_time`/`playing`/`playback_mode`. A single row would have to say either "a reviewer may scrub and change the shot" or "a reviewer may do neither", and the product wants the first without the second. The rows below are the units enforcement actually operates on:

| Message type / field group | Category | Driver | Reviewer | Viewer |
|---|---|---|---|---|
| `PLAYBACK_SET` — `view_mode` / `clip_guid` | visibility | ✅ (host only) | ❌ | ❌ |
| `PLAYBACK_SET` — `current_time` / `playing` / `playback_mode` | position | ✅ | ✅ | ❌ |
| `DISPLAY_SET` (channel, exposure, pan/zoom) | position | ✅ | ✅ | ✅ |
| `ADD_TIMELINE` | structure | ✅ | ❌ | ❌ |
| `REMOVE_TIMELINE` | structure | ✅ | ❌ | ❌ |
| `REPLACE_TIMELINE` | structure | ✅ | ❌ | ❌ |
| `RENAME_TIMELINE` | structure | ✅ | ❌ | ❌ |
| `SET_PROPERTY` | structure | ✅ | ❌ | ❌ |
| `INSERT_CHILD` (structural) | structure | ✅ | ❌ | ❌ |
| `REMOVE_CHILD` | structure | ✅ | ❌ | ❌ |
| `ANNOTATION` (strokes) | annotation | ✅ | ✅ | ❌ |
| `INSERT_CHILD` (annotation track) | annotation | ✅ | ✅ | ❌ |
| `STATE_SNAPSHOT` | — | ✅* | ❌ | ❌ |

\* `STATE_SNAPSHOT` is a master concern, orthogonal to session role — but in practice the master should be a driver.

Three rows are worth calling out:

- **Reviewers may scrub.** An earlier draft denied them `PLAYBACK_SET` entirely, which contradicted the role's own description ("can annotate but cannot navigate or modify content") only because "navigate" conflated position with visibility. A reviewer marking up a shot needs to get to the frame they are marking up. Visibility still moves only when the host moves it.
- **`DISPLAY_SET` is open to everyone, including viewers.** Settled by `host-owned-visibility` §7.1: display state is per-peer and ungated *by role*, because reviewers legitimately toggle channels and exposure locally and nothing in the wrong-clip divergence traced to it. A viewer toggling their own alpha channel is not a session event. Note this is a statement about **roles only** — `broadcast-ownership` D8 does put display state under a lease (on its own channel), so "any role may emit it" and "one peer emits it at a time" both hold. Role decides who may *ever*; the lease decides who is emitting *right now*.
- **Visibility is host-only even for a driver.** Role and category authority compose; a driver who is not the host still does not choose the shot.

## How roles relate to host, master, and ownership

Four concepts coexist once `broadcast-ownership` also lands, and conflating any two of them re-introduces a bug this codebase has already had:

| Concept | Answers | Scope | Determined by |
|---|---|---|---|
| **master** | Who holds the canonical snapshot? | Session | Liveness / discovery timing |
| **host** | Who chooses what everyone looks at? | Category (visibility) | Capability, `authority.elect_host_guid` |
| **lease owner** | Who is broadcasting this category *right now*? | Category (position, structure) | Claim + tiebreak (`broadcast-ownership`) |
| **role** | What is this *participant* permitted to emit at all? | Per peer | Policy + token (this change) |

**The composition rule** — stated here once, since this change introduces the axis that makes composition necessary:

> Role is a **ceiling**, category authority is a **gate**, and a broadcast must pass both. A driver has permission to emit visibility; only the host actually does.

Order is **role first, then category authority** — also the cheaper order, since role is static while a lease check may touch expiry state.

```
Full broadcast guard (landed + this change + broadcast-ownership):

  Can this peer broadcast this field group?

  1. Check ROLE: is this peer's role permitted this field group?   [this change]
     └── NO → strip / suppress (role gate)
     └── YES ↓

  2. Check CATEGORY AUTHORITY:
     ├── visibility  → am I the host?  (landed: _enforce_visibility)
     │                 └── NO → strip view_mode/clip_guid
     └── position /  → do I own the lease?          [broadcast-ownership]
         structure     └── YES → broadcast freely, refresh lease
                       └── NO  → strip fields, return SUPPRESSED
```

Step 2's lease branch is the *only* part that needs `broadcast-ownership`. Without it, position and structure are simply ungated by category — exactly as they are today — and the role gate still does its job. **This change is therefore shippable on its own.**

### Roles reduce contention but never remove it

Note that "reviewers may scrub" makes position contention *survive* into managed sessions — under the earlier matrix, roles alone would have reduced every managed session to a single position writer:

| Configuration | Who resolves position echo? | Who resolves permissions? |
|---|---|---|
| Multi-driver (default, small sessions) | **Ownership** (`broadcast-ownership`) | Everyone can do everything |
| Managed session (1 driver + viewers) | **Roles** make position ownership implicit — one writer | **Roles** (this change) |
| Managed session (driver + reviewers + viewers) | **Ownership** — reviewers scrub too | **Roles** (this change) |
| Any session | Visibility: **host** (already landed, static) | — |

## Enforcement strategy: send-side only

Roles are enforced by **suppressing outbound broadcasts** — the role check lives in the same `SyncManager.broadcast_*` choke point that already strips visibility fields, so both host applications share one implementation. There is no protocol-level message rejection or broker-side filtering.

**Why send-side, not receive-side?** Three enforcement points were considered:

1. **Send-side (chosen)**: The sender checks its own role before emitting. Simple guard clauses in the core. No protocol changes needed for enforcement itself.
2. **Receive-side**: Every receiver validates the sender's role before applying a message. Requires the protocol to carry role information per-message and every peer to enforce it. Much more complex.
3. **Broker-side**: RabbitMQ would reject unauthorized messages. The fanout exchange topology doesn't naturally support per-topic authorization, and this would tie the role model to broker infrastructure.

Send-side enforcement is sufficient because we're not defending against adversaries — we're preventing well-intentioned reviewers from accidentally stepping on each other. A "soft" enforcement at the sender is the right match for this trust model.

The invariant to preserve: **plugins never test their own role.** `host-owned-visibility` §1.4 verified that no plugin gates a broadcast on being host, and guards it with `test_no_plugin_gates_a_broadcast_on_being_host`. Where a plugin genuinely needs to know — a *local intent* branch — it goes through one shared core predicate (`owns_visibility()`), not per-application logic. Any role-dependent local behaviour must follow that shape.

## Local interaction model

When a viewer or reviewer is blocked from broadcasting (e.g. they scrub their playhead), their **local app still reacts normally** — they can explore locally, but their actions don't propagate. When the driver next sends a message, all peers re-sync to the driver's state.

Three options were considered:

1. **Suppress locally too** — Viewer's app is fully locked to the driver. Like a screen share. Requires deep hooks into RV/xStudio to prevent local interaction — impractical.
2. **Let local state diverge, re-sync on next driver message (chosen)** — Less invasive. Viewer can explore locally but snaps back when the driver advances. Natural for a screening context.
3. **Suppress + UI feedback** — Block the action locally and show a toast. Best UX but most per-application work.

Option 2 matches the model `host-owned-visibility` §7.2 already settled for followers: passive snap-back, no re-assert-on-every-message machinery. A manual "re-sync to driver" menu option is a reasonable addition for when a viewer wants to snap back without waiting.

## Session role policy

A session-level configuration controls how new joiners are assigned roles:

- **`default_role`**: The role assigned to peers who join without a token. Defaults to `driver` for backwards compatibility (small sessions work as before). Set to `viewer` for screening mode.
- **`driver_token`**: Optional shared secret. Any peer presenting this token on join is elevated to `driver`. Can be shared out-of-band (e.g. "use this code if you need to drive").
- **`reviewer_token`**: Optional shared secret for reviewer elevation.
- **Runtime changes**: A driver can promote/demote peers live during the session (stretch goal).

### Recovering a driverless session

Restricting host election to drivers creates a state that cannot occur today: no drivers present, therefore no peer eligible for host, therefore visibility frozen with no way back. This happens if `default_role` is `viewer` and the token holder never joins, or the driver drops and rejoins with a new GUID.

The session offers a **"Become controller"** menu action as the designed exit. It sets the peer's role to `driver` and stops there — host follows automatically, because `elect_host_guid` is a pure function of the peer table and resolves onto the new driver at the next election. One action, no second mechanism.

It is offered **only when no eligible driver exists**. That gate is what keeps the role model from being decorative: an always-available "take control" button would make `default_role: viewer` advisory and every token pointless. The waiver is safe exactly where it applies — a session with no driver has no authority worth protecting, nobody to ask, and nothing in flight to disrupt. Outside that state, elevation goes through the token as normal.

Two peers clicking at once is safe: both become drivers, and host election picks one deterministically. Convergence rather than contention, the same property that makes simultaneous host election safe without a claim protocol.

"Controller" is a UI label; the role it grants is `driver`. See design.md D7.

### Driver reconnection

The token model solves a critical edge case: a driver who accidentally disconnects and re-joins their own managed session would otherwise be assigned the default role (`viewer`).

Three approaches were considered:

1. **Stable peer identity** — GUIDs persist across sessions (stored in config). Peers are recognised on reconnect. Clean but introduces identity management concerns (shared machines, per-project identities).
2. **Token-based elevation (chosen)** — The driver token lets anyone reclaim the driver role regardless of GUID. Also enables co-drivers and handoff. Simple and stateless.
3. **Role claim + peer approval** — Rejoining peer requests promotion; existing peers approve. More complex, no clear benefit.

Additionally, the session maintains a `peer_roles` map (`guid → role`) in memory. If a peer reconnects with the **same GUID**, their previous role is restored without needing a token. The token is the fallback for when the GUID changes.

### Where session state lives

Role policy is carried in the `STATE_SNAPSHOT` payload, alongside existing timeline and playback state — the same channel that already carries `host_guid`. When a new peer joins and receives the snapshot, they also receive the role configuration.

Three options were considered:

1. **In the STATE_SNAPSHOT (chosen)** — Natural extension of the existing sync model, and the precedent is established. If the master dies, the new master already has a copy.
2. **Broker-side** — Stored as pinned message or queue metadata. Survives all peers leaving, but adds infrastructure dependency and reduces portability.
3. **External (config file, API)** — Clean separation, but another moving part.

## Role-aware master and host election

**Both** elections become role-aware, and they differ in strength.

*Master election* **prefers** drivers, since the master holds the session config and needs full broadcast capability:

1. **Prefer a driver** — natural fit; they need to send `STATE_SNAPSHOT` and control the session.
2. **If no drivers, promote a reviewer** — becomes master for state-sync but not automatically a driver (role and master status remain orthogonal).
3. **If only viewers, promote one to master** — session effectively freezes from a content perspective. Everyone can see what's there but no one can drive until a driver rejoins.

*Host election* **restricts** to drivers. A non-driver host would hold visibility authority while its role forbade it from emitting visibility — the session's shot would freeze with no peer able to change it, and nothing would report why. `elect_host_guid` therefore filters candidates on `role == driver` in addition to the existing capability check, with `HOST_PREFERENCE` ranking applied among the survivors. Its existing shape accommodates this: it is a pure function of the peer table, so the filter is one predicate and the determinism property is unaffected.

The precedent for keeping a passive peer out of the host role is already in the code: the sync viewer declares `capabilities=[]` specifically so that an observer can never be elected. Role-based exclusion is the same idea generalised.

## Peer role awareness

Peers advertise their role via a **field on the existing `PEER_ANNOUNCE` message**, not a new `PEER_ROLE` message. `PEER_ANNOUNCE` landed with `host-owned-visibility` and already carries `app` and `capabilities` into `SyncManager._peers`, with a settled announce-on-join / answer-once cadence that avoids a storm. Role belongs in that entry beside capability — and *must* be there, because host election reads the peer table and now needs role to filter on it.

This resolves the question of whether `PEER_ROLE` should be a message or a field: the message it would have duplicated now exists, so the field wins by default. It also enables UI features like "25 viewers, 2 reviewers, 1 driver connected" from state the peer table already maintains.

## Capabilities

### New Capabilities
- `session-role-model`: The three-tier role hierarchy — driver, reviewer, viewer roles, the per-field-group permission matrix, the composition rule with category authority (role is a ceiling, category authority a gate), role assignment logic (default policy, token elevation, GUID-based memory, runtime promotion/demotion), and recovery from a driverless session via an explicit self-elevation action gated on that state.
- `session-role-config`: Session-level configuration — default joiner role, driver/reviewer tokens, session state storage in `STATE_SNAPSHOT`, and the driver reconnection flow.

### Modified Capabilities
- `session-visibility-authority`: host election is restricted to drivers, so a passive peer cannot be elected into an authority its role forbids it from exercising.
- `otio-sync-core`: role policy in `STATE_SNAPSHOT`, role-aware master **and host** election, per-peer role tracking on `PEER_ANNOUNCE`, role enforcement at the existing `broadcast_*` choke point.
- `ori-session-management`: session lifecycle (join, leave, reconnect) must account for role assignment and restoration.
- `openrv-sync-plugin`: UI affordances for role indication, token entry, and the driverless-session "Become controller" action.
- `xstudio-plugin-module-structure`: UI affordances for role indication, token entry, and the driverless-session "Become controller" action.
- `protocol-message-docs`: `role` field on `PEER_ANNOUNCE` (not a new `PEER_ROLE` message), role policy section in `STATE_SNAPSHOT` payload, token hash fields.

## Impact

- **Core**: `SyncManager` gains role policy storage, peer role tracking, token verification, role-aware master and host election. `STATE_SNAPSHOT` payload extended with a `session_roles` section, following `host_guid`'s convention — omitted when unset, `None` ignored on receipt, so a peer predating the field cannot clear a session's role policy.
- **Plugins**: Role-based broadcast guards live in core, not plugins. UI affordances only: role indication, connected peer roles, "join as" or token entry, optional "re-sync to driver" action, and a "Become controller" action enabled only when the peer table shows no eligible driver. No changes to receive paths.
- **sync_viewer**: Already a natural viewer, and already declares `capabilities=[]`. May gain UI to show role breakdown.
- **Packaging**: any new core module must be added to `rvplugin/ori_sync/makepackage.csh`'s hand-maintained vendoring list. `host-owned-visibility` §6a.1 shipped without `authority.py` in that list, and because `__init__.py` imports inside `try/except ImportError`, the RV plugin stayed connected but inert. Prefer extending `authority.py` over adding a module.
- **Protocol**: Backwards compatible — sessions without role config behave as today (`default_role = "driver"`). Token hashes (not plaintext) in `STATE_SNAPSHOT`.
- **Risk**: Low. With `default_role: driver` there is zero behaviour change until a session opts in, and that default is itself the rollback.
- **Future**: A streaming/web-based reviewer client is plausible — viewers and reviewers don't necessarily need local media playback (though viewers are expected to have local media in the primary use case).

### Open considerations (not blocking)

- **Disruptive vs non-disruptive mutations**: Some content changes (e.g. appending a clip to the end of a playlist) don't change the current selection and could be safe for non-drivers. The initial implementation uses the simpler per-field-group model; finer-grained "disruption level" gating could be a future refinement.
- **Application-type heuristics**: In mixed sessions, xStudio is often the natural content authority since RV typically shows a subset. **Partly settled**: `authority.HOST_PREFERENCE` already ranks xStudio above OpenRV for *visibility*, as a preference and never a requirement, so an OpenRV-only session still elects a host. Whether the same ranking should seed a *default role* is still open, and is a stronger claim — a ranking that only decides who breaks a tie is safe, whereas one that assigns permissions can leave a session with no driver.
- **Multiple drivers**: The model supports multiple drivers (it's not restricted to one). In a small session this is the default. In a managed session, multiple people could hold the driver token. `broadcast-ownership` resolves contention between them; without it, they contend as peers do today.
