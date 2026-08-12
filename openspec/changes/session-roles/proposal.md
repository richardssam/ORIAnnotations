## Why

In large review sessions — 20–30 people in dailies or screenings — any participant can inadvertently change the selection, scrub to a different shot, add or remove media, or draw on screen. The protocol is effectively a megaphone in a library.

| Session size | Current model | Problem |
|---|---|---|
| 2–3 peers | Everyone can do everything | Works fine |
| 5–10 peers | Occasional conflicts | Manageable with coordination |
| 20–30 peers | Any of 30 people can change selection, scrub, draw, or add media | Chaotic and unusable |

This change introduces a three-tier permission model — **driver / reviewer / viewer** — enforced at the broadcast choke point that already exists.

> **Split from the original two-phase proposal on 2026-08-07.** Phase 1 (broadcast ownership / write leases) is now the separate change `broadcast-ownership`, which **archived on 2026-08-10 with leases enabled by default**. The split was made on the grounds that this change did not depend on it, and that remains true of the role model — but the composition it described as future is now the deployed state, so this change is specified against a live two-check pipeline rather than against a single visibility check.
>
> **Split again on 2026-08-10.** Role *administration* — editing another peer's role, editing the session's default role, and the protocol message that carries such a change — moves to a follow-on change, `session-role-administration`. What remains here is the model and its enforcement: the matrix, the composition rule, role assignment on join, election filtering, and a read-only role display. That keeps this change's defining property intact — inert until a session opts in — and keeps a new targeted protocol message out of the change that introduces the axis.
>
> **Depends on `peer-identity`.** Role memory is keyed on identity (D3), and a role-administration UI is unusable against GUIDs. That change carries the identity field set and the provider seam; this one consumes `user`.

### Current state

`host-owned-visibility` (archived 2026-08-06) established the enforcement point and the vocabulary this change plugs into, and four changes since have built on it. What already exists in `python/otio_sync_core/`:

| Landed | Where |
|---|---|
| A category table mapping each `broadcast_*` method to a category | `authority.BROADCAST_CATEGORIES` — `visibility` / `position` / `annotation` / `structure` |
| `SENT` / `SUPPRESSED` returned from every `broadcast_*` | `authority.SENT`, `authority.SUPPRESSED` (`broadcast_add_annotation` excepted — callers need the clip GUID, and annotation is never gated) |
| Field-group enforcement inside one core method, **for two categories** | `SyncManager._enforce_visibility` → `strip_visibility_fields`; `_enforce_position` → `strip_position_fields`, called in that order from `broadcast_playback_state` |
| Write leases on position, display, and structure — **enabled by default** | `broadcast-ownership`: `authority.LEASE_CHANNELS`, `SyncManager._owns_channel()` / `claim_category()`, `ORI_BROADCAST_OWNERSHIP` |
| The invariant that plugins never test authority | `test_no_plugin_gates_a_broadcast_on_being_host` |
| A runtime kill switch per mechanism, read per call | `ORI_VISIBILITY_AUTHORITY=0`, `ORI_BROADCAST_OWNERSHIP=0` |
| Host election as a pure function of the peer table | `authority.elect_host_guid`, with `HOST_PREFERENCE` ranking and GUID tie-break |
| A peer table with app + capabilities, and **two** paths that populate it | `SyncManager._peers` — periodic `PEER_ANNOUNCE`, plus the roster in `STATE_SNAPSHOT` (`_peer_roster()` / `adopt_peers()`) |
| Peer liveness and departure | `peer-departure`: 5 s heartbeat, 15 s timeout, `PEER_DEPART`, `drop_peer()` |
| Authority state carried to late joiners | `StateSnapshot.host_guid` and the ownership section — omitted when unset, absence ignored on receipt |
| A shared Qt-free session-state projection and a panel over it in both hosts | `session-state-ui`: `session_state.session_state_snapshot`, `ui_model.py`, both panels, Debug Mode |
| A placeholder waiting for this change | `session_state.peer_role()` — returns `"Host"`/`"Client"`, documented as *"placeholder until the `session-roles` change lands"* |

The distinguished concepts today are **master** (state-sync: holds the canonical snapshot), **host** (visibility authority), and **lease owner** (whoever is currently driving position, display, or structure). None of them says anything about what a *participant* is permitted to emit — every peer has identical broadcast permissions, bounded only by category authority, and every peer is equally eligible to take the next lease.

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
| Destructive annotation ops (`clear-all-paint`) | annotation | ✅ | ❌ | ❌ |
| `STATE_SNAPSHOT` | — | ✅* | ❌ | ❌ |

\* `STATE_SNAPSHOT` is a master concern, orthogonal to session role — but in practice the master should be a driver.

Four rows are worth calling out:

- **Reviewers may scrub.** An earlier draft denied them `PLAYBACK_SET` entirely, which contradicted the role's own description ("can annotate but cannot navigate or modify content") only because "navigate" conflated position with visibility. A reviewer marking up a shot needs to get to the frame they are marking up. Visibility still moves only when the host moves it.
- **`DISPLAY_SET` is open to everyone, including viewers.** Settled by `host-owned-visibility` §7.1: display state is per-peer and ungated *by role*, because reviewers legitimately toggle channels and exposure locally and nothing in the wrong-clip divergence traced to it. A viewer toggling their own alpha channel is not a session event. Note this is a statement about **roles only** — `broadcast-ownership` D8 does put display state under a lease (on its own channel), so "any role may emit it" and "one peer emits it at a time" both hold. Role decides who may *ever*; the lease decides who is emitting *right now*.
- **Visibility is host-only even for a driver.** Role and category authority compose; a driver who is not the host still does not choose the shot.
- **A destructive annotation op is driver-only, though annotation is otherwise open.** Handed here explicitly by `broadcast-ownership`, which considered leasing `clear-all-paint` and declined: *"a clear is more naturally a driver-only action than a lease-holder-only one"*, leaving a known accepted gap that any peer can clear everyone's paint. Role is the axis that closes it, and this is the one row where the role gate is finer-grained than the category. It is also the only row that removes a permission a reviewer has today, so it is the row to check first if a session reports annotations disappearing.

## How roles relate to host, master, and ownership

Four concepts coexist once `broadcast-ownership` also lands, and conflating any two of them re-introduces a bug this codebase has already had:

| Concept | Answers | Scope | Determined by |
|---|---|---|---|
| **master** | Who holds the canonical snapshot? | Session | Liveness / discovery timing |
| **host** | Who chooses what everyone looks at? | Category (visibility) | Capability, `authority.elect_host_guid` |
| **lease owner** | Who is broadcasting this category *right now*? | Category (position, structure) | Claim + tiebreak (`broadcast-ownership`) |
| **role** | What is this *participant* permitted to emit at all? | Per peer | Policy + identity memory (this change) |

**The composition rule** — stated here once, since this change introduces the axis that makes composition necessary:

> Role is a **ceiling**, category authority is a **gate**, and a broadcast must pass both. A driver has permission to emit visibility; only the host actually does.

Order is **role first, then category authority** — the cheaper order, since role is static while a lease check touches expiry state, and now also the *correct* one: a lease is confirmed as a side effect of a broadcast actually going out (`_refresh_lease_confirmed`), so checking category first would let a role-blocked peer confirm a lease it is not permitted to use.

```
Full broadcast guard (all of step 2 is landed as of 2026-08-10):

  Can this peer broadcast this field group?

  1. Check ROLE: is this peer's role permitted this field group?   [this change]
     └── NO → strip / suppress (role gate)
     └── YES ↓

  2. Check CATEGORY AUTHORITY:
     ├── visibility  → am I the host?              (_enforce_visibility)
     │                 └── NO → strip view_mode/clip_guid
     └── position /  → do I own the lease?         (_enforce_position, _owns_channel)
         structure     └── YES → broadcast freely, confirm lease
                       └── NO  → strip fields, return SUPPRESSED
```

**The guard is not the only place role has to be consulted.** Leases are acquired by `claim_category()`, which plugins call unconditionally from every input-driven path — as they must, since plugins never test authority. A viewer or reviewer that is blocked from *broadcasting* position still interacts locally (that is this change's local interaction model, deliberately), so without a second check it claims the position lease from a driver and then can never use it. Role therefore gates **claiming as well as broadcasting**, in core, on the same table. See design.md D8; this is the one composition that `broadcast-ownership` landing has made load-bearing rather than hypothetical.

### Roles reduce contention but never remove it

Note that "reviewers may scrub" makes position contention *survive* into managed sessions — under the earlier matrix, roles alone would have reduced every managed session to a single position writer:

| Configuration | Who resolves position echo? | Who resolves permissions? |
|---|---|---|
| Multi-driver (default, small sessions) | **Ownership** (`broadcast-ownership`) | Everyone can do everything |
| Managed session (1 driver + viewers) | **Roles** make position ownership implicit — one *writer*, provided viewers are also barred from *claiming* (D8) | **Roles** (this change) |
| Managed session (driver + reviewers + viewers) | **Ownership** — reviewers scrub too | **Roles** (this change) |
| Any session | Visibility: **host** (already landed, static) | — |

The qualifier on the second row is not pedantry. Roles reduce that session to one writer only if a viewer's local scrubbing stays out of the lease mechanism entirely; a viewer that can claim but not broadcast is *worse* than one with no role at all, because it can hold a lease it will never confirm.

## Enforcement strategy: send-side only

Roles are enforced by **suppressing outbound broadcasts** — the role check lives in the same `SyncManager.broadcast_*` choke point that already strips visibility fields, so both host applications share one implementation. There is no protocol-level message rejection or broker-side filtering.

**Why send-side, not receive-side?** Three enforcement points were considered:

1. **Send-side (chosen)**: The sender checks its own role before emitting. Simple guard clauses in the core. No protocol changes needed for enforcement itself.
2. **Receive-side**: Every receiver validates the sender's role before applying a message. Requires the protocol to carry role information per-message and every peer to enforce it. Much more complex.
3. **Broker-side**: RabbitMQ would reject unauthorized messages. The fanout exchange topology doesn't naturally support per-topic authorization, and this would tie the role model to broker infrastructure.

Send-side enforcement is sufficient because we're not defending against adversaries — we're preventing well-intentioned reviewers from accidentally stepping on each other. A "soft" enforcement at the sender is the right match for this trust model.

**Send-side is where enforcement lives; it is not the whole of what must hold.** `fix-visibility-authority-bypass` settled that authority is over the **displayed outcome**, not over one message's fields: stripping is necessary and not sufficient, because a peer's action can reach the session through a category it *is* permitted to emit and change what others see as a side effect. That invariant now sits in `session-visibility-authority`, and role inherits it unchanged — a viewer's permitted actions must not move the host's display by any route. (The original 2026-08-06 route for this — a follower's clip-timeline registration firing the host's selection machinery — was **withdrawn on 2026-08-09** after the host's behaviour reproduced with the follower idle. The invariant survived the retraction; the route did not. See that change's `evidence.md`.)

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

- **`default_role`**: The role assigned to peers the session does not already know. Defaults to `driver` for backwards compatibility (small sessions work as before). Set to `viewer` for screening mode.
- **`peer_roles`**: A `user → role` map — the session's memory of who holds what. Keyed on the identity `peer-identity` carries, **not** on peer GUID: a driver who drops and rejoins gets a new GUID but the same `user`, which is the reconnection case that previously needed a token to solve.
- **Editing either of these at runtime is out of scope here** and moves to `session-role-administration`, together with the targeted message a role change requires. This change assigns roles on join and displays them; it does not change them mid-session, with one exception — D7's self-elevation out of a driverless session, which is the deadlock's only exit and therefore cannot wait for a follow-on change.

**Tokens are no longer the primary mechanism.** The earlier draft carried `driver_token` / `reviewer_token` shared secrets, and their entire justification was reconnection: a driver whose GUID changed had no other way back. Identity-keyed `peer_roles` covers that case directly and statelessly, which leaves tokens serving only "someone the session has never seen needs to drive" — better served by an administrator granting it (`session-role-administration`) than by a secret passed around a screening room. This change therefore ships **without tokens**, and without token hashes in `STATE_SNAPSHOT`. See design.md D3.

The cost is stated rather than hidden: identity is self-declared and overridable (`peer-identity` D4), so typing another person's username inherits their remembered role. That is acceptable under this change's declared non-goal of adversarial security, and it is not weaker than a plaintext token on an unauthenticated broker — but it is now the mechanism, so it is a decision rather than an inherited property. If a session ever needs role assignment it can trust, the answer is an authenticated identity provider through `peer-identity`'s seam, not verification bolted onto a role.

### Recovering a driverless session

Restricting host election to drivers creates a state that cannot occur today: no drivers present, therefore no peer eligible for host, therefore visibility frozen with no way back. This happens if `default_role` is `viewer` and nobody the session remembers as a driver ever joins.

**This collides with a requirement that landed since the first draft, and the collision has to be answered rather than noticed later.** `session-visibility-authority` now requires that a departing host not leave the session's view frozen — *"with no peer permitted to change it and nothing reporting the cause"* — written as a failure to close, after a host that had left was still counted as elected. D4's driver filter makes an equivalent state reachable by a different route, so this change owes that requirement two things: an exit, and a report.

The exit is a **"Become controller"** action. It sets the peer's role to `driver` and stops there — host follows automatically, because `elect_host_guid` is a pure function of the peer table and resolves onto the new driver at the next election. One action, no second mechanism.

The report is that the driverless state is **visible in the session state panel**, not merely inferable from a menu item being enabled. The panel exists now (`session-state-ui`), it already polls this state, and a frozen session that explains nothing is precisely the failure `host-owned-visibility` D4 was written against. Availability of the action doubles as the indicator, but the indicator is the requirement.

It is offered **only when no eligible driver exists**. That gate is what keeps the role model from being decorative: an always-available "take control" button would make `default_role: viewer` advisory. The waiver is safe exactly where it applies — a session with no driver has no authority worth protecting, nobody to ask, and nothing in flight to disrupt. Outside that state, a role comes from the session's memory of who you are, or from an administrator (`session-role-administration`).

Two peers clicking at once is safe: both become drivers, and host election picks one deterministically. Convergence rather than contention, the same property that makes simultaneous host election safe without a claim protocol.

"Controller" is a UI label; the role it grants is `driver`. See design.md D7.

### Driver reconnection

A driver who accidentally disconnects and re-joins their own managed session must not be assigned the default role (`viewer`) and locked out of the session they are running.

Four approaches were considered:

1. **GUID-keyed role memory** — the session remembers `guid → role`. Fails at exactly the case that matters: a reconnecting peer generally has a new GUID.
2. **Stable peer identity** — GUIDs persist across sessions, stored in config. Rejected when first written for introducing identity-management concerns; that objection is now moot in one direction and answered in the other, because `peer-identity` introduces identity deliberately and with a provider seam.
3. **Token-based elevation** — a shared secret reclaims the role regardless of GUID. Chosen in the original draft, **superseded 2026-08-10**: it solves reconnection at the cost of a secret to distribute, hash, store in the snapshot, and enter through a dialog, all to answer a question identity answers directly.
4. **Identity-keyed role memory (chosen)** — `peer_roles` maps `user → role`. A reconnecting driver is recognised because they are the same person, not because they present the same GUID or the same secret.

Two consequences follow, and both are deliberate. One user on two machines holds the same role on both — correct for a supervisor with a workstation and a laptop, and the reason the key is `user` rather than `user@host`. And a session's memory dies with the session, as the rest of the role policy does; a screening organiser sets the policy for the session they are running.

### Where session state lives

Role policy is carried in the `STATE_SNAPSHOT` payload, alongside existing timeline and playback state — the same channel that already carries `host_guid`, the peer roster, and the ownership section. When a new peer joins and receives the snapshot, they also receive the role configuration.

Three precedents now share that channel, not one, and all three use the same compatibility convention: omit when unset, ignore an absence on receipt. `session_roles` is a fourth instance of a settled pattern rather than a new idea.

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

The preference must be expressed as a **ranking within the existing self-election path**, not as a new wall-clock deferral. `otio-sync-core` requires that self-election be one operation owning every transition it entails, with named callers (discovery timeout, state-request timeout, failover); and this codebase has spent three changes removing timing windows rather than adding them. A driver-first rank applied at the existing discovery timeout gets the same outcome with no new window to tune. See design.md D4.

*Host election* **restricts** to drivers. A non-driver host would hold visibility authority while its role forbade it from emitting visibility — the session's shot would freeze with no peer able to change it, and nothing would report why. `elect_host_guid` therefore filters candidates on `role == driver` in addition to the existing capability check, with `HOST_PREFERENCE` ranking applied among the survivors. Its existing shape accommodates this: it is a pure function of the peer table, so the filter is one predicate and the determinism property is unaffected.

The precedent for keeping a passive peer out of the host role is already in the code: the sync viewer declares `capabilities=[]` specifically so that an observer can never be elected. Role-based exclusion is the same idea generalised.

## Peer role awareness

Peers advertise their role via a **field on the existing `PEER_ANNOUNCE` message**, not a new `PEER_ROLE` message. `PEER_ANNOUNCE` already carries `app` and `capabilities` into `SyncManager._peers`, and is sent on join and periodically thereafter as this peer's liveness heartbeat. Role belongs in that entry beside capability — and *must* be there, because host election reads the peer table and now needs role to filter on it.

**Announce is not the only path into the peer table, and role must travel both.** A joiner learns peers it has not yet heard announce from the roster in `STATE_SNAPSHOT` (`_peer_roster()` / `adopt_peers()`) — the answer-to-announce cascade was deliberately removed, so the roster is not a redundant second copy but the *only* source for a quiet peer until its next heartbeat. Role on the announce alone would leave adopted peers role-less for up to the heartbeat interval, and a role-filtered `elect_host_guid` reading that table sees **no drivers** — which is not merely a late election but a spurious trip of D7's driverless gate, the one gate that keeps the role model from being decorative. `peer-identity` establishes this two-path plumbing for its own fields; role follows it.

This resolves the question of whether `PEER_ROLE` should be a message or a field: the message it would have duplicated now exists, so the field wins by default. It also enables UI features like "25 viewers, 2 reviewers, 1 driver connected" from state the peer table already maintains.

A peer announcing its own role means role is **self-declared**, exactly as `app` and `capabilities` already are. Under send-side enforcement that is coherent — a peer enforces its own ceiling — and it is why changing *someone else's* role needs a targeted message rather than a wish, which is `session-role-administration`'s job.

## Capabilities

### New Capabilities
- `session-role-model`: The three-tier role hierarchy — driver, reviewer, viewer roles, the per-field-group permission matrix, the composition rule with category authority (role is a ceiling, category authority a gate) covering **both** the broadcast guard and lease claiming, role assignment logic (default policy, identity-keyed memory), and recovery from a driverless session via an explicit self-elevation action gated on that state.
- `session-role-config`: Session-level configuration — default joiner role, the identity-keyed `peer_roles` map, session state storage in `STATE_SNAPSHOT`, and the driver reconnection flow.

### Modified Capabilities
- `session-visibility-authority`: host election is restricted to drivers, so a passive peer cannot be elected into an authority its role forbids it from exercising; the driverless state that restriction makes reachable is reported and recoverable, satisfying the existing requirement that the view not freeze with nothing reporting the cause.
- `broadcast-ownership`: a peer SHALL NOT claim a lease for a category its role forbids it from broadcasting — a claim is now gated by role as well as triggered only by local user input.
- `otio-sync-core`: role policy in `STATE_SNAPSHOT`, role-aware master **and host** election, per-peer role tracking carried on **both** `PEER_ANNOUNCE` and the snapshot peer roster, role enforcement at the existing `broadcast_*` choke point.
- `session-state-ui`: the projection's `role` field carries the session role (driver/reviewer/viewer) rather than the `Host`/`Client` placeholder, with master and host remaining separate flags; the panel reports a driverless session.
- `ori-session-management`: session lifecycle (join, leave, reconnect) must account for role assignment and restoration.
- `openrv-sync-plugin`: role indication and the driverless-session "Become controller" action.
- `xstudio-plugin-module-structure`: role indication and the driverless-session "Become controller" action.
- `protocol-message-docs`: `role` field on `PEER_ANNOUNCE` (not a new `PEER_ROLE` message) and in the `STATE_SNAPSHOT` peer roster, role policy section in the `STATE_SNAPSHOT` payload.

## Impact

- **Core**: `SyncManager` gains role policy storage, peer role tracking, role-aware master and host election, and a role check on both the broadcast guard and `claim_category()`. `STATE_SNAPSHOT` payload extended with a `session_roles` section, following the convention its three existing authority sections share — omitted when unset, absence ignored on receipt, so a peer predating the field cannot clear a session's role policy.
- **Plugins**: Role-based guards live in core, not plugins — including the claim gate, so no plugin has to learn to stop calling `claim_category()`. UI affordances only: role indication, connected peer roles, optional "re-sync to driver" action, and a "Become controller" action enabled only when the peer table shows no eligible driver. No changes to receive paths.
- **Prerequisite**: `peer-identity`. Role memory keys on `user`, and the peer-field propagation this change needs (announce **and** snapshot roster) is established there.
- **sync_viewer**: Already a natural viewer, and already declares `capabilities=[]`. May gain UI to show role breakdown.
- **Packaging**: any new core module must be added to `rvplugin/ori_sync/makepackage.csh`'s hand-maintained vendoring list. `host-owned-visibility` §6a.1 shipped without `authority.py` in that list, and because `__init__.py` imports inside `try/except ImportError`, the RV plugin stayed connected but inert. The list is currently maintained — `authority.py`, `session_state.py`, `ui_model.py`, and both QML files are all in it — so the risk is regression, not backlog. Prefer extending `authority.py` over adding a module.
- **Protocol**: Backwards compatible — sessions without role config behave as today (`default_role = "driver"`). No tokens and no token hashes; see "Session role policy".
- **Risk**: Low for the model. With `default_role: driver` there is zero behaviour change until a session opts in, and that default is itself the rollback. The one non-inert edit is the claim gate, which changes nothing while every peer is a driver.
- **Future**: A streaming/web-based reviewer client is plausible — viewers and reviewers don't necessarily need local media playback (though viewers are expected to have local media in the primary use case).

### Deferred to `session-role-administration`

Named here so the follow-on change can be written from this list rather than rediscovered:

- A targeted role-change message (`SET_PEER_ROLE`: peer, role, issuer), applied by the target, which then re-announces — so `PEER_ANNOUNCE` remains the single write path into every peer's table.
- Who may issue one. The leaning is **host only**: one seat, already elected, already deterministic, and already restricted to drivers under D4, where "any driver" would give a session N administrators and demotion races with no resolution rule. D7's self-elevation stays the one exception.
- Editing `default_role` mid-session, which is session policy rather than a peer's property: it broadcasts, and it affects future joiners only — it never retroactively demotes anyone.
- The panel controls for both, and with them the modification to `session-state-ui`'s "the panel never mutates sync state" requirement. Per-row role editing cannot be a menu item, so that requirement has to move; the way to keep its intent is that the *projection* stays strictly read-only and the panel dispatches through an explicit command surface rather than writing into the snapshot dict.
- Optional token elevation, if a use case survives identity-keyed memory and administrator grants.

### Open considerations (not blocking)

- **Disruptive vs non-disruptive mutations**: Some content changes (e.g. appending a clip to the end of a playlist) don't change the current selection and could be safe for non-drivers. The initial implementation uses the simpler per-field-group model; finer-grained "disruption level" gating could be a future refinement.
- **Application-type heuristics**: In mixed sessions, xStudio is often the natural content authority since RV typically shows a subset. **Partly settled**: `authority.HOST_PREFERENCE` already ranks xStudio above OpenRV for *visibility*, as a preference and never a requirement, so an OpenRV-only session still elects a host. Whether the same ranking should seed a *default role* is still open, and is a stronger claim — a ranking that only decides who breaks a tie is safe, whereas one that assigns permissions can leave a session with no driver.
- **Multiple drivers**: The model supports multiple drivers (it's not restricted to one). In a small session this is the default. In a managed session, several people can be remembered as drivers. `broadcast-ownership` resolves contention between them, and does so today rather than hypothetically.
- **Host as a lease, not a static election, when candidates are equally ranked**: Host today is decided once by `elect_host_guid` (app-preference, then GUID tiebreak) and never moves again while the elected peer stays connected — even if it goes idle while an equally-ranked peer is actively trying to show something. `broadcast-ownership` gives position and structure exactly this kind of idle-expiry handoff; visibility deliberately has none, on the grounds that a static single writer needs no contention resolution. That reasoning holds cleanly when one candidate is a clear preference (xStudio over OpenRV), but is less obviously right when two candidates tie on rank (e.g. two OpenRV peers) and the elected one is sitting idle. Whether that case warrants giving host a lease — and how it would compose with host election already being restricted to drivers, above — is open. **Distinct from "Become controller"**: that is a manual action gated on *no eligible driver existing at all*; this would be automatic and would apply even with a driver present and connected, merely idle. Raised 2026-08-08 during a live broadcast-ownership soak, observing that RV↔RV sessions have no such handoff today.
