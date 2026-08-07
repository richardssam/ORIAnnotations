## Context

**Split from the original two-phase proposal on 2026-08-07.** Phase 1 (write leases) is now `broadcast-ownership`; this document covers the role model only. The split is possible because the role check composes with *category authority*, which already exists — so this change has no dependency on leases and can land against today's code.

The sync protocol has no per-participant permission concept. Every peer may emit anything, bounded only by category authority (visibility, host-only) and the `is_master` gate on structure. In a 20–30-person screening that is unusable.

Relevant current state:

- **`authority.py` exists** and defines four categories (`visibility` / `position` / `annotation` / `structure`), the `SENT` / `SUPPRESSED` status vocabulary, `strip_visibility_fields`, the `ORI_VISIBILITY_AUTHORITY` kill switch, and `elect_host_guid`.
- **The enforcement point is built and proven.** `SyncManager._enforce_visibility` strips visibility fields in one place, called from `broadcast_playback_state`; a live soak recorded 284 strips and zero leaked `view_mode` sends. The role check goes in the same place.
- **Plugins never test authority**, and this is test-guarded (`test_no_plugin_gates_a_broadcast_on_being_host`). Where a plugin genuinely needs to know, it asks one shared core predicate (`owns_visibility()`), not per-application logic.
- **`elect_host_guid` is a pure function of the peer table**, ranked by `HOST_PREFERENCE` with a GUID tie-break, so every peer reaches the same host from the same inputs.
- **A peer table exists** (`SyncManager._peers`, `{guid: {app, capabilities}}`), populated by `PEER_ANNOUNCE`, with a settled announce-on-join / answer-once cadence.
- **`STATE_SNAPSHOT` already carries authority state** (`host_guid`), with the compatibility convention to copy: omit when unset, ignore `None` on receipt, adopt via a named operation rather than direct assignment.
- **The sync viewer already declares `capabilities=[]`** specifically so a passive observer can never be elected host. Role-based exclusion generalises an idea already in the code.
- The two host plugins have measurably drifted on hand-replicated protocol behaviour, which rules out per-plugin enforcement.

## Goals / Non-Goals

**Goals:**

- Driver/reviewer/viewer roles workable for 20–30-person sessions, with token-based driver reconnection.
- One enforcement point (`SyncManager.broadcast_*`) shared by both host applications, layered on the landed category check.
- Zero behaviour change until a session opts in — `default_role: driver` reproduces today exactly, and is also the rollback.
- Every peer reaches the same host from the same inputs, with role as an additional filter that does not disturb that property.

**Non-Goals:**

- Adversarial security. Enforcement is send-side; peers trust each other. Tokens gate accidents, not attackers.
- Receive-side validation or broker-side filtering.
- Locking the local UI of viewers/reviewers. Local divergence + snap-back is accepted, consistent with `host-owned-visibility` §7.2.
- Re-cutting the category boundaries. They belong to `session-visibility-authority`; roles compose with them.
- Contention resolution between peers that *do* have permission. That is `broadcast-ownership`, and this change neither provides nor requires it.
- Stable cross-session peer identity (token elevation covers driver reconnection instead).

## Decisions

### D1: Roles are a static permission matrix evaluated in the existing choke point

A `role → allowed field groups` table in core (the proposal's matrix, encoded once). Note **field groups, not message types** — the boundary runs inside `PLAYBACK_SETTINGS_1.0`, so a role table keyed on message type cannot express "a reviewer may scrub but not change the shot", which is the whole point of the reviewer tier.

The `broadcast_*` guard becomes: role check, then category authority — both in `SyncManager`, both invisible to plugins except via the returned status.

- *Alternative — per-plugin role checks:* rejected. The applications have already drifted on hand-replicated behaviour, and `host-owned-visibility` §1.4 established (and test-guards) the invariant that plugins never gate a broadcast on authority. Any role-dependent *local* behaviour must go through a shared core predicate, the way `owns_visibility()` does.
- *Alternative — receive-side validation:* rejected; requires role metadata on every message and N enforcement points instead of one.

Mechanically this reuses `strip_visibility_fields`' shape: strip the disallowed field groups in **one** core function, never at call sites. `SUPPRESSED` already means *"sent with fields stripped"* rather than *"not sent"*, and a mixed message is the normal case — this change must not redefine the status. Tests assert on the sent envelope, not on whether a send occurred, following `test_broadcast_authority.py`.

### D2: Role is a ceiling; category authority is a gate

The composition rule, stated here because this change introduces the axis that makes composition necessary:

> A broadcast must pass **both** checks. Role says what this participant may ever emit; category authority says whether this peer is the one emitting it right now. A driver has permission to emit visibility; only the host actually does.

Evaluation order is **role first, then category authority** — also the cheaper order, since role is static while a lease check (once `broadcast-ownership` lands) may touch expiry state.

Four concepts coexist, and conflating any two re-introduces a bug this codebase has already had:

| Concept | Answers | Scope | Determined by |
|---|---|---|---|
| **master** | Who holds the canonical snapshot? | Session | Liveness / discovery timing |
| **host** | Who chooses what everyone looks at? | Category (visibility) | Capability, `elect_host_guid` |
| **lease owner** | Who is broadcasting this category *right now*? | Category (position, structure) | `broadcast-ownership` |
| **role** | What is this *participant* permitted to emit at all? | Per peer | Policy + token (this change) |

`host-owned-visibility` D2 rejected "host is always the master" precisely because a master re-election, which turns on liveness, would then silently change who controls the view. The same reasoning forbids collapsing role onto host: role is a property of a *person's seat*, host is a property of *one category*, and a session can legitimately have several drivers and one host.

**Independence from `broadcast-ownership`.** Without leases, the category-authority step is simply `visibility → host?` and nothing for position/structure — which is today's behaviour. The role gate is fully functional against that. This is why the two changes were split, and it should stay true: no decision here may assume a lease exists.

### D3: Role assignment is GUID-memory first, then token, then default

Assignment on join: (1) GUID present in `peer_roles` → restore previous role; (2) token presented and hash matches → elevated role; (3) otherwise `default_role`.

Tokens travel as salted hashes in the snapshot; the plaintext token is only ever sent by the *joining* peer inside its join message. Acceptable under the trust model — the broker is already unauthenticated, and tokens gate accidents rather than attackers.

- *Alternative — stable cross-session peer identity:* rejected; introduces identity management concerns (shared machines, per-project identities) for a problem tokens solve statelessly.
- *Alternative — role claim + peer approval:* rejected; more complex with no clear benefit.

The GUID-memory step exists for the reconnection edge case: a driver who drops and rejoins their own managed session would otherwise land on `default_role` (`viewer`) and be locked out of the session they are running. Token elevation is the fallback when the GUID changes.

### D4: Both elections become role-aware, at different strengths

- *Master election* **prefers** drivers. The election response carries the peer's role; a peer defers self-election briefly if it is not a driver and the discovery responses show a driver present. Master remains orthogonal to role — a reviewer promoted to master is master for state-sync only.
- *Host election* **restricts** to drivers. This is the stronger form and it is not optional: a non-driver host holds visibility authority while its role forbids emitting visibility, so the session's shot freezes and nothing reports why. `elect_host_guid` gains a `role == driver` filter beside its existing capability check.

Being a pure function of the peer table, the filter is one predicate and the determinism property is untouched — every peer still reaches the same host from the same inputs, which is the property that makes simultaneous election safe without a claim protocol.

Host election must also keep `fix-discovery-thread-safety`'s discipline, which `host-owned-visibility` already implements and which adding a role input must not erode: `elect_host()` owns the transition and no call site assembles it; other threads enqueue via `request_host_election()` rather than mutating; `_drain_host_elections()` re-checks eligibility **at drain time**, so a driver that announced during queue latency is taken into account. A role arriving between enqueue and drain is exactly the hazard that re-check exists for.

### D5: Role travels as a field on `PEER_ANNOUNCE`, not a new message

Role must reach `SyncManager._peers`, because D4's host filter reads the peer table. `PEER_ANNOUNCE` already carries `app` and `capabilities` into that table with a settled announce-on-join / answer-once cadence.

- *Alternative — a new `PEER_ROLE` message (the original proposal):* rejected. It would duplicate a message that now exists, with its own cadence to get right and its own storm risk. That message did not exist when the first draft was written; it does now.

A role change during a session re-announces, so promotion/demotion propagates through the same path as join.

### D6: The default role is `driver`, and that default is the rollback

`default_role: driver` means every peer is permitted everything, which is exactly today's behaviour — so the change is inert until a session opts into a stricter policy. This is deliberately the same "additive, reversible by configuration" shape as `ORI_VISIBILITY_AUTHORITY=0`, but it needs no env var because the policy itself carries the off switch.

Two consequences worth stating so they are not undone later:

- A session whose snapshot omits role policy must be treated as `default_role: driver`, not as "no one may broadcast". Following `host_guid`'s convention — omit when unset, ignore `None` on receipt — a peer predating this change cannot clear a session's role policy, and a session predating it cannot lock out a new peer.
- Because the default is permissive, **the failure mode of a bug here is a session that behaves like today**, not a session that freezes. That is the right direction for a mechanism whose worst outcome would otherwise be "nobody can drive and nothing says why".

### D7: A driverless session is recoverable by an explicit user action

**Settled 2026-08-07.** D4's host restriction makes a new state reachable that cannot occur today: `default_role: viewer`, no token holder present (or the driver dropped and its GUID changed), so there are no drivers, therefore no peer eligible for host, therefore visibility is frozen with no way back. Tokens and GUID memory (D3) cover the common cases, but neither helps if nobody in the room has the token.

The session therefore offers a **"Become controller"** menu action, and this is the deadlock's designed exit.

**It elevates the peer to `driver`, and stops there.** It does *not* claim host directly. Host follows as a consequence: `elect_host_guid` is a pure function of the peer table (D4), so the moment a driver exists the next election resolves onto it. One user action, no second mechanism, and the determinism property is untouched.

**It is offered only in the deadlock state** — no eligible driver in the peer table. This is the constraint that keeps the role model from being decorative: an always-available self-elevation button would make `default_role: viewer` advisory, and every token in D3 pointless. The waiver is safe precisely and only where it applies:

> A session with no driver has no authority worth protecting. There is nobody to ask for permission, and nothing in flight to disrupt — the alternative to self-elevation is a session that stays frozen until everyone reconnects.

Outside that state, elevation goes through the token, unchanged.

**Concurrent clicks are safe by construction.** Two peers clicking in the same window both become drivers; host election then picks one deterministically by preference rank and GUID. Convergence, not contention — the same property that lets `broadcast-ownership` D2 resolve simultaneous claims without an arbiter. No locking, no "who clicked first".

*Alternative — auto-promote a viewer when the last driver leaves:* rejected. It would silently hand control to whoever happens to be in the peer table, which is how a screening session acquires an accidental driver. An explicit action means a human chose it.

*Alternative — a general "take control" action, always available:* rejected, as above; it collapses into "no roles".

**Naming.** "Controller" is a UI label, not a fifth concept — the spec term stays `driver`, and the action's effect is exactly "set my role to driver". Anything else would add a term to the four-axis table in D2 that nothing else uses. Whether the button reads "Become controller", "Take control", or something else is a UI decision; the mapping to `driver` is not.

This also answers the adjacent question about a general "request driver" affordance: there isn't one. `host-owned-visibility` §7.3's restraint (election only, no UI) still holds for the *normal* case. This is an escape hatch for a state that has no other exit, which is a different thing from a handoff mechanism — and it lands at the role layer, leaving the `claim_host()` seam §7.3 preserved in `elect_host` still unused and still available.

## Risks / Trade-offs

- **[A session ends up with no driver]** If `default_role` is `viewer` and the token holder never joins (or the driver drops and its GUID changes), nothing can change the shot and, under D4, nothing can even be elected host. → Tokens are the intended recovery and GUID memory covers the common reconnect; **D7's "Become controller" action is the guaranteed exit** when neither applies. The state must also be *visible* — a frozen session that offers no explanation is the failure `host-owned-visibility` D4 was written against — so the action's availability doubles as the indicator.
- **[The escape hatch is mistaken for a handoff mechanism]** (D7) A "Become controller" button is one small step from "anyone can take over at any time", which would make roles decorative. → It is gated on the deadlock state (no eligible driver) and on nothing else; outside it, elevation goes through the token. Any later relaxation of that gate should be treated as re-opening D3, not as a UI tweak.
- **[Role and host are conflated by a reader]** They are different axes and the matrix has host-only rows, which invites "driver == host". → D2's table above is the canonical four-axis statement. `broadcast-ownership` D7 tabulates only the three axes that exist without roles and defers the composition rule here, so the two documents overlap by subset rather than by duplication and cannot disagree.
- **[Snapshot-carried role policy dies with the session]** If every peer leaves, tokens and role config are lost. → Accepted (broker/external storage considered and rejected); screening organisers re-issue tokens per session.
- **[Field-group enforcement is subtler than per-message]** A role gate that stripped `view_mode` but left `clip_guid` would still be asserting what the session looks at. → Strip whole field groups in one core function, never at call sites — the rule `host-owned-visibility` §1.3 established and tested, including the `clip_guid`-without-`view_mode` case.
- **[A landed mechanism is silently absent in RV]** New core modules must be added to `makepackage.csh`'s hand-maintained vendoring list; `__init__.py` imports inside `try/except ImportError`, so an omission leaves the plugin connected but inert. This exact fault shipped with `authority.py` (`host-owned-visibility` §6a.1). → Prefer extending `authority.py` to adding a module; update the list in the same commit; check the startup banner proves which copy RV loaded.
- **[Tokens in an unauthenticated broker]** Plaintext tokens cross the wire on join. → Explicitly within the trust model (non-goal: adversarial security). Hashes, not plaintext, in the snapshot.

## Migration Plan

0. ~~**Enforcement point, status contract, category table, kill-switch pattern, deterministic election, peer table, snapshot-carried authority.**~~ **Done** — `host-owned-visibility`, archived 2026-08-06. This change builds on these rather than creating them.
1. **2a:** role matrix + policy in core and snapshot; role check in the guard ahead of the category check; `role` field on `PEER_ANNOUNCE`; `elect_host_guid` restricted to drivers. Default policy `driver` — zero behaviour change until a session opts in. Expose role and elected host in the test inspector alongside the existing `is_host`/`host_guid`, in the runner's `ignore_keys`.
2. **2b:** host UI — role indication, connected-peer roles, token entry on join, "re-sync to driver" action, and D7's **"Become controller"** action (enabled only when the peer table shows no eligible driver). The driverless state must be visible as well as recoverable.

Not sequenced against `broadcast-ownership`. Either may land first; if leases land first, the guard gains a second category branch and no decision here changes.

Rollback: `default_role: driver` restores today's behaviour without a rebuild (D6).

## Open Questions

- Runtime promotion/demotion is listed as a stretch goal — is it in 2a or deferred? It reuses D5's re-announce path, so it is cheap, but it adds an authority question (who may promote?) that the token model otherwise avoids. D7 answers the *self*-promotion case only, and only in the deadlock state; promoting someone else is still open.
- ~~What exactly makes a driver "eligible" for D7's gate?~~ → **Settled by `peer-departure` (implemented 2026-08-07): present in the peer table now means present.** That change added `PEER_DEPART` for clean exits and a 5 s heartbeat / 15 s liveness timeout for everything else, both converging on `drop_peer()`. A driver that has gone stops being in the table within one timeout, so D7's gate needs no staleness rule of its own — exactly the outcome this question was holding out for. The residual is latency, not correctness: the escape hatch can stay greyed out for up to the liveness timeout after an unclean exit.
- Should `STATE_SNAPSHOT` emission itself be role-gated, or left as a pure master concern? The matrix marks it master-only with a footnote; in practice the master should be a driver, but nothing yet enforces that.

**Settled since the first draft:**

- ~~Should a driverless session surface a visible state, or is silence acceptable?~~ → **Visible *and* actionable** (D7). A "Become controller" action, offered only in that state, both signals the condition and resolves it — a frozen session that explains nothing is the failure `host-owned-visibility` D4 was written against.
- ~~Does role warrant a user-visible affordance beyond token entry?~~ → **Only the D7 escape hatch.** `host-owned-visibility` §7.3's "election only, no UI" restraint still governs the normal case; D7 is an exit from a state with no other exit, not a handoff mechanism, and it leaves the `claim_host()` seam unused.
- ~~`PEER_ROLE` as a new message vs. a field on an existing exchange~~ → **field on `PEER_ANNOUNCE`** (D5). That message now exists with a settled cadence, and host election needs role in the peer table anyway.
- ~~Whether a reviewer may navigate~~ → **position yes, visibility no.** The original "cannot navigate" conflated the two; `host-owned-visibility` split them, and a reviewer must be able to reach the frame they are annotating.
- ~~Whether `display_state` is role-gated~~ → **no**, it is per-peer and ungated for every role including viewer (`host-owned-visibility` §7.1). Orthogonally, `broadcast-ownership` D8 does lease it on its own channel — the two compose, and neither implies the other.
- ~~Whether the test inspector needs authority state exposed~~ → **yes**, pattern established by `host-owned-visibility` §2.4.
