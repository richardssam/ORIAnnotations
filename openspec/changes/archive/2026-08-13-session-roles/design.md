## Context

**Split from the original two-phase proposal on 2026-08-07**, and **split again on 2026-08-10.** Phase 1 (write leases) is `broadcast-ownership`, which has since archived with leases on by default. Role *administration* — changing another peer's role, changing the session default, and the message that carries either — is `session-role-administration`. What remains here is the role model and its enforcement.

The sync protocol has no per-participant permission concept. Every peer may emit anything, bounded only by category authority: visibility is host-only, and position, display, and structure are gated by a write lease any peer is equally eligible to take. In a 20–30-person screening that is unusable.

Relevant current state, re-verified against the tree on 2026-08-10:

- **`authority.py` exists** and defines four categories (`visibility` / `position` / `annotation` / `structure`), the `SENT` / `SUPPRESSED` status vocabulary, `strip_visibility_fields` / `strip_position_fields`, two kill switches (`ORI_VISIBILITY_AUTHORITY`, `ORI_BROADCAST_OWNERSHIP`), `elect_host_guid`, and the lease channels and durations.
- **The enforcement point is built, proven, and already composite.** `broadcast_playback_state` calls `_enforce_visibility` then `_enforce_position`, each stripping one field group; a live soak recorded 284 visibility strips and zero leaked `view_mode` sends. The role check goes ahead of both.
- **Leases are live, not prospective.** `ORI_BROADCAST_OWNERSHIP` defaults to on, `claim_category()` is wired into every input-driven path in both plugins, and a lease is *confirmed* only when a broadcast in that category actually goes out (`_refresh_lease_confirmed`). Both facts constrain this design — see D8.
- **Plugins never test authority**, and this is test-guarded (`test_no_plugin_gates_a_broadcast_on_being_host`). Where a plugin genuinely needs to know, it asks one shared core predicate (`owns_visibility()`), not per-application logic.
- **`elect_host_guid` is a pure function of the peer table**, ranked by `HOST_PREFERENCE` with a GUID tie-break, so every peer reaches the same host from the same inputs.
- **A peer table exists** (`SyncManager._peers`, `{guid: {app, capabilities, last_seen}}`), populated by **two** paths: periodic `PEER_ANNOUNCE` (also the liveness heartbeat), and the roster carried in `STATE_SNAPSHOT` (`_peer_roster()` / `adopt_peers()`). The answer-to-announce cascade was removed — it was the only step whose message count grew with session size — so the roster is the sole source for a peer that has gone quiet.
- **Peer liveness and departure are settled** (`peer-departure`): 5 s heartbeat, 15 s timeout, `PEER_DEPART`, both converging on `drop_peer()`.
- **`STATE_SNAPSHOT` already carries three kinds of authority state** — `host_guid`, the peer roster, and the ownership section — all sharing one compatibility convention: omit when unset, ignore an absence on receipt, adopt via a named operation rather than direct assignment.
- **A session-state panel exists in both hosts** (`session-state-ui`) over a shared Qt-free projection, with a Debug Mode. Its `peer_role()` is an explicit placeholder for this change; its spec requires the projection stay read-only.
- **The sync viewer already declares `capabilities=[]`** specifically so a passive observer can never be elected host. Role-based exclusion generalises an idea already in the code.
- **Authority is over the displayed outcome, not one message's fields** (`fix-visibility-authority-bypass`). Stripping is necessary and not sufficient; role inherits that invariant unchanged.
- The two host plugins have measurably drifted on hand-replicated protocol behaviour, which rules out per-plugin enforcement.

## Goals / Non-Goals

**Goals:**

- Driver/reviewer/viewer roles workable for 20–30-person sessions, with driver reconnection that needs no secret.
- Role enforced in core, at both places authority is exercised — the `broadcast_*` guard and lease claiming — never in a plugin.
- Zero behaviour change until a session opts in — `default_role: driver` reproduces today exactly, and is also the rollback.
- Every peer reaches the same host from the same inputs, with role as an additional filter that does not disturb that property.
- A session that ends up with no driver says so, and has one action that fixes it.

**Non-Goals:**

- Adversarial security. Enforcement is send-side; peers trust each other. Roles gate accidents, not attackers.
- Receive-side validation or broker-side filtering.
- Locking the local UI of viewers/reviewers. Local divergence + snap-back is accepted, consistent with `host-owned-visibility` §7.2.
- Re-cutting the category boundaries. They belong to `session-visibility-authority`; roles compose with them.
- Contention resolution between peers that *do* have permission. That is `broadcast-ownership`, which has landed; this change composes with it rather than adjusting it.
- **Role administration** — changing another peer's role, changing `default_role` mid-session, and the targeted message either needs. `session-role-administration`.
- **Defining identity.** The field set, its provider seam, and the join-time override are `peer-identity`; this change consumes `user` and adds no identity concepts of its own.

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

Evaluation order is **role first, then category authority**. Three reasons, and the third only became true when leases landed:

1. Role is static; a lease check touches expiry state. Cheaper first.
2. A ceiling that is evaluated after a gate is not a ceiling.
3. **A lease is confirmed as a side effect of a broadcast going out.** `_enforce_position` calls `_refresh_lease_confirmed` on the path where it owns the channel, and a confirmed lease is the one state `_apply_claim` will not preempt. Checking category first would therefore let a role-blocked peer promote its lease to un-preemptable while its message goes out stripped — authority state hardened by a broadcast the role model just denied.

Four concepts coexist, and conflating any two re-introduces a bug this codebase has already had:

| Concept | Answers | Scope | Determined by |
|---|---|---|---|
| **master** | Who holds the canonical snapshot? | Session | Liveness / discovery timing |
| **host** | Who chooses what everyone looks at? | Category (visibility) | Capability, `elect_host_guid` |
| **lease owner** | Who is broadcasting this category *right now*? | Category (position, structure) | `broadcast-ownership` |
| **role** | What is this *participant* permitted to emit at all? | Per peer | Policy + identity memory (this change) |

`host-owned-visibility` D2 rejected "host is always the master" precisely because a master re-election, which turns on liveness, would then silently change who controls the view. The same reasoning forbids collapsing role onto host: role is a property of a *person's seat*, host is a property of *one category*, and a session can legitimately have several drivers and one host.

**Relationship to `broadcast-ownership`, revised 2026-08-10.** The original text here said no decision may assume a lease exists, because leases were unbuilt and possibly blocked. They are now built, enabled by default, and soaked. The independence claim has served its purpose — this change was never hostage to that one — and holding to it now would be worse than pointless: it would specify a role gate against a code path that no longer exists, and it would miss the composition in D8, which is only a defect *because* leases are live. Decisions here may and do assume leases; what they must not assume is that a lease says anything about permission, which is the conflation D2's table exists to prevent.

### D3: Role assignment is identity-memory, then default

**Re-decided 2026-08-10.** The original decision was "GUID-memory first, then token, then default", with tokens carried as salted hashes in the snapshot. `peer-identity` removes the problem tokens were solving, so they go.

Assignment on join: (1) the joining peer's `user` is present in `peer_roles` → restore that role; (2) otherwise `default_role`.

The whole justification for tokens was reconnection. A driver who drops and rejoins their own managed session must not land on `viewer` and be locked out of the session they are running — and GUID memory cannot help, because the reconnecting peer's GUID is new, which is the case that matters. A shared secret answered that by making the role reclaimable by anyone who knew it. Identity answers it by recognising the person, which is what the session actually meant all along, and it does so without a secret to distribute, hash, store in `STATE_SNAPSHOT`, enter through a dialog, or rotate when a screening ends.

- *Alternative — keep tokens alongside identity:* rejected for this change. What is left for them is "a person the session has never seen needs to drive", which `session-role-administration` serves better: an administrator grants it, visibly, to a named person, instead of a secret circulating in a room of thirty. If a case survives that, tokens can be added back — nothing here forecloses them.
- *Alternative — GUID-keyed memory:* rejected; it fails the only case it exists for.
- *Alternative — role claim + peer approval:* rejected; more complex with no clear benefit.

**The key is `user`, not `user@host`.** One person on a workstation and a laptop holds the same role on both, which is the behaviour a supervisor expects. `peer-identity` carries `host` as well, so a later change can narrow the key without a protocol change.

**`peer_roles` is also the right store for any later "has this person been approved?" memory**, and a follow-on should extend it rather than adding a parallel one. An admitted user is a user in the map; a session's memory of who may participate is the same question, at the same key, with the same lifetime (it dies with the session, per the risk below). Two identity-keyed maps that must agree is a divergence waiting to happen, and the reason to say so here is that admission's natural instinct is a separate allow-list.

**What this trades away, stated plainly.** Identity is self-declared and overridable (`peer-identity` D4), so typing another person's username inherits their remembered role. Under this change's declared non-goal of adversarial security that is acceptable, and it is not weaker than a plaintext token crossing an unauthenticated broker — but it is now *the* mechanism rather than a fallback, so it is a decision and not an inherited property. The escalation path is an authenticated identity provider through `peer-identity`'s seam, which is exactly the shape that seam exists for. Nothing about role assignment changes when it is swapped.

### D4: Both elections become role-aware, at different strengths

- *Master election* **prefers** drivers, expressed as a **ranking applied at the existing discovery timeout**, not as a new wall-clock deferral. An earlier draft had a non-driver "defer self-election briefly" if discovery responses showed a driver present; that is a new timing window, in a codebase that has spent `fix-playback-position-echo-loop`, `broadcast-ownership`, and `retire-position-structure-echo-guards` removing them, and `otio-sync-core` requires self-election to be one operation with named callers (discovery timeout, state-request timeout, failover) rather than a sequence assembled per call site. A driver-first rank evaluated when that timeout fires reaches the same outcome with nothing to tune. Master remains orthogonal to role — a reviewer promoted to master is master for state-sync only.
- *Host election* **restricts** to drivers. This is the stronger form and it is not optional: a non-driver host holds visibility authority while its role forbids emitting visibility, so the session's shot freezes and nothing reports why. `elect_host_guid` gains a `role == driver` filter beside its existing capability check.

Being a pure function of the peer table, the filter is one predicate and the determinism property is untouched — every peer still reaches the same host from the same inputs, which is the property that makes simultaneous election safe without a claim protocol.

**The filter reads a field that arrives by two paths, and a missing role must not read as "not a driver".** Peers enter the table from `PEER_ANNOUNCE` and from the snapshot roster, and a peer adopted from a roster that predates this change carries no role. Treating absence as ineligible would let one old peer, or one adoption ordering, produce a table with no drivers — which is not a delayed election but a spurious trip of D7's driverless gate. Absence resolves to the session's `default_role`, so a session that never opted in still elects exactly as it does today. This is the same "unknown is not the restrictive value" rule that the `xs_flat_playlist` `media_exists` default got wrong once already.

`session-visibility-authority` requires that visibility authority not freeze with nothing reporting the cause — written after an elected host had left and kept the role. This filter makes an equivalent state reachable by a second route, and D7 is what discharges that obligation.

Host election must also keep `fix-discovery-thread-safety`'s discipline, which `host-owned-visibility` already implements and which adding a role input must not erode: `elect_host()` owns the transition and no call site assembles it; other threads enqueue via `request_host_election()` rather than mutating; `_drain_host_elections()` re-checks eligibility **at drain time**, so a driver that announced during queue latency is taken into account. A role arriving between enqueue and drain is exactly the hazard that re-check exists for.

### D5: Role travels on `PEER_ANNOUNCE` **and** the snapshot peer roster, not a new message

Role must reach `SyncManager._peers`, because D4's host filter reads the peer table. `PEER_ANNOUNCE` already carries `app` and `capabilities` into that table, on join and periodically thereafter as this peer's liveness heartbeat.

- *Alternative — a new `PEER_ROLE` message (the original proposal):* rejected. It would duplicate a message that now exists, with its own cadence to get right and its own storm risk. That message did not exist when the first draft was written; it does now.

**Corrected 2026-08-10: announce is not the only path in.** This decision originally described a "settled announce-on-join / answer-once cadence". There is no answer any more — answering an announcement was removed as the only step in the protocol whose message count grew with session size, and a joiner now learns quiet peers from the roster in `STATE_SNAPSHOT` (`_peer_roster()` / `adopt_peers()`). So the roster is not a redundant copy of what announcements provide; for a peer that has gone quiet it is the only source until its next heartbeat.

Role therefore travels both ways, and a task that adds it to one is incomplete. `peer-identity` establishes this two-path plumbing for its own fields and lands first, so role is added to an existing multi-field pipeline rather than building it — which is also why the roster's absent-role default (D4) is decided once and applies to both.

A role change during a session re-announces, so it propagates through the same path as join. This change makes only one such change — D7's self-elevation; the rest is `session-role-administration`, whose targeted message is applied by its *target*, which then re-announces, keeping `PEER_ANNOUNCE` the single write path into every peer's table.

### D6: The default role is `driver`, and that default is the rollback

`default_role: driver` means every peer is permitted everything, which is exactly today's behaviour — so the change is inert until a session opts into a stricter policy. This is deliberately the same "additive, reversible by configuration" shape as `ORI_VISIBILITY_AUTHORITY=0`, but it needs no env var because the policy itself carries the off switch.

Two consequences worth stating so they are not undone later:

- A session whose snapshot omits role policy must be treated as `default_role: driver`, not as "no one may broadcast". Following `host_guid`'s convention — omit when unset, ignore `None` on receipt — a peer predating this change cannot clear a session's role policy, and a session predating it cannot lock out a new peer.
- Because the default is permissive, **the failure mode of a bug here is a session that behaves like today**, not a session that freezes. That is the right direction for a mechanism whose worst outcome would otherwise be "nobody can drive and nothing says why".
- **"Absence resolves to `default_role`" is stated over a three-value role set, and does not survive a fourth value below `viewer` unexamined.** A later admission-control change would want an unadmitted/`pending` state beneath `viewer`; if that role is reachable by *absence* — an old peer, an adoption ordering, a roster written by earlier code — then a session that opts into admission silently un-admits every peer that predates the field, which is the lock-out this rule exists to prevent. The rule to preserve is not "absence means `default_role`" literally but **absence never resolves to a more restrictive state than the session's own default**, and a fourth role must decide explicitly which side of that it sits on rather than inheriting D4's predicate.

### D7: A driverless session is recoverable by an explicit user action

**Settled 2026-08-07; recovery mechanism revised 2026-08-10 with D3.** D4's host restriction makes a new state reachable that cannot occur today: `default_role: viewer` and nobody present whom the session remembers as a driver, so there are no drivers, therefore no peer eligible for host, therefore visibility is frozen with no way back. Identity-keyed memory (D3) covers reconnection, which is the common case, but it cannot help a room where nobody has ever held the role.

The session therefore offers a **"Become controller"** menu action, and this is the deadlock's designed exit.

**It elevates the peer to `driver`, and stops there.** It does *not* claim host directly. Host follows as a consequence: `elect_host_guid` is a pure function of the peer table (D4), so the moment a driver exists the next election resolves onto it. One user action, no second mechanism, and the determinism property is untouched.

**It is offered only in the deadlock state** — no eligible driver in the peer table. This is the constraint that keeps the role model from being decorative: an always-available self-elevation button would make `default_role: viewer` advisory and D3's memory a suggestion. The waiver is safe precisely and only where it applies:

> A session with no driver has no authority worth protecting. There is nobody to ask for permission, and nothing in flight to disrupt — the alternative to self-elevation is a session that stays frozen until everyone reconnects.

Outside that state, a role comes from the session's memory of who you are (D3) or from an administrator (`session-role-administration`).

**The state must be reported, not only exitable.** `session-visibility-authority` requires that a frozen view not be frozen silently, and D4 makes a second route into that state — so the driverless condition is surfaced in the session state panel, which already exists and already polls this state. The action's availability doubles as the indicator, but it is the indicator that is required: a greyed-out menu item nobody opens is not a report. This is the same obligation `host-owned-visibility` D4 was written against, arriving by a new route.

**Concurrent clicks are safe by construction.** Two peers clicking in the same window both become drivers; host election then picks one deterministically by preference rank and GUID. Convergence, not contention — the same property that lets `broadcast-ownership` D2 resolve simultaneous claims without an arbiter. No locking, no "who clicked first".

*Alternative — auto-promote a viewer when the last driver leaves:* rejected. It would silently hand control to whoever happens to be in the peer table, which is how a screening session acquires an accidental driver. An explicit action means a human chose it.

*Alternative — a general "take control" action, always available:* rejected, as above; it collapses into "no roles".

**Naming.** "Controller" is a UI label, not a fifth concept — the spec term stays `driver`, and the action's effect is exactly "set my role to driver". Anything else would add a term to the four-axis table in D2 that nothing else uses. Whether the button reads "Become controller", "Take control", or something else is a UI decision; the mapping to `driver` is not.

This also answers the adjacent question about a general "request driver" affordance: there isn't one. `host-owned-visibility` §7.3's restraint (election only, no UI) still holds for the *normal* case. This is an escape hatch for a state that has no other exit, which is a different thing from a handoff mechanism — and it lands at the role layer, leaving the `claim_host()` seam §7.3 preserved in `elect_host` still unused and still available.

### D8: Role gates claiming a lease, not only broadcasting

**New 2026-08-10, and the one decision here that `broadcast-ownership` landing forced.**

A peer that may not broadcast a category SHALL NOT claim that category's lease. The check lives in `claim_category()`, in core, keyed on the same role table as the broadcast guard.

Without it, the two changes compose into a defect that neither has on its own:

- Plugins call `claim_category()` unconditionally from every input-driven path — `playback_sync.py`, `sequence_sync.py`, `display_sync.py`, xStudio's controller. They must, because plugins never test authority (D1); a plugin that consulted role before claiming would be the exact invariant violation `test_no_plugin_gates_a_broadcast_on_being_host` exists to prevent.
- Viewers and reviewers keep interacting locally. That is not an oversight, it is this change's local interaction model: option 2, local divergence with snap-back, chosen deliberately over locking the UI.
- So a viewer scrubbing its own playhead claims the position lease. `_apply_claim` will not preempt a *confirmed* lease, so this is harmless while a driver is actively broadcasting — but the moment the driver's lease goes idle or unconfirmed, the viewer takes it.
- The viewer can then never confirm it, because confirmation happens only when a broadcast in that category actually goes out (`_refresh_lease_confirmed`) and the role gate strips its messages. The lease sits held-but-unconfirmed for its full duration, and `resolve_claim` prefers the *earlier* `claim_ts`, so the driver's fresh claim loses to the viewer's older one until it expires.

Net effect: up to a lease duration of dead position sync each time a viewer touches its playhead — 1.0 s for position, 2.0 s for structure — in a session designed to have twenty-five of them. A viewer that can claim but not broadcast is strictly worse than a viewer with no role at all.

The fix is small and belongs beside the switch it mirrors: `claim_category()` already no-ops when `ownership_enforcement_enabled()` is false, on the reasoning that a disabled mechanism must not leave a claim state machine running against a policy no longer in force. A role that forbids the category is the same statement about the same call.

- *Alternative — let the claim through and suppress at broadcast:* rejected; that is the defect above.
- *Alternative — have plugins skip the claim:* rejected; violates D1 and would drift between the two applications, which is the failure mode that produced this design in the first place.
- *Alternative — treat a role-blocked claim as a release:* rejected as over-reach. Not claiming is sufficient; releasing would let a viewer's local activity take a lease *away* from a driver, which is the same bug with the sign flipped.

Display is deliberately unaffected: every role may emit display state (`host-owned-visibility` §7.1), so every role may claim its channel.

### D9: The panel displays role; it does not edit it

`session-state-ui` spec'd its projection read-only — "the panel never mutates sync state", and a panel needing state the manager lacks derives it in the projection rather than growing `SyncManager`. This change keeps that contract intact: it replaces the `peer_role()` placeholder with the real role and adds nothing else to the surface.

That is possible only because role *administration* moved out. Per-row editing cannot be a menu item, so `session-role-administration` will have to modify that requirement — and the way to preserve its intent is stated there in advance: the projection stays strictly read-only, and the panel dispatches through an explicit command surface rather than writing into the snapshot dict.

Two smaller consequences of reusing that surface:

- **The word "role" is already taken in it.** `session-state-ui` calls master and host "roles", and its projection's `role` field currently holds `"Host"`/`"Client"`. This change re-points that field to the session role; master and host remain the separate `is_master` / `is_host` flags they already are, so the four axes of D2 stay four.
- **`peer_role()` is where the derivation belongs**, not in either panel — the same reason `peer-identity` derives its display name there. Two hosts formatting the same half-known state independently is how they drifted before.

## Risks / Trade-offs

- **[A session ends up with no driver]** If `default_role` is `viewer` and nobody the session remembers as a driver joins, nothing can change the shot and, under D4, nothing can even be elected host. → Identity-keyed memory (D3) covers reconnection; **D7's "Become controller" action is the guaranteed exit** when it does not apply. The state must also be *visible* — a frozen session that offers no explanation is the failure `host-owned-visibility` D4 was written against, and `session-visibility-authority` now requires the report — so the panel surfaces it and the action's availability doubles as the indicator.
- **[A viewer holds a lease it can never use]** (D8) The interaction between an unconditional `claim_category()` and a role that strips the resulting broadcast, which stalls position sync for a lease duration per viewer interaction. → The claim gate. This is the composition to re-check first if a managed session reports laggy or intermittent scrubbing, and the reason a soak of this change needs a *viewer actively interacting*, not a viewer sitting still.
- **[Role assignment is only as trustworthy as a typed username]** (D3) Identity is self-declared and overridable, so a user can inherit another person's remembered role. → Within the declared non-goal (not adversarial), and no weaker than the plaintext token it replaces. Escalation is an authenticated provider through `peer-identity`'s seam, which changes nothing about role assignment. Do not add verification to the role layer instead; that is the wrong layer and would not verify anything.
- **[A missing role reads as "not a driver"]** (D4) A peer adopted from a snapshot roster written by older code carries no role, and a filter that treats absence as ineligible can produce a table with no drivers — tripping D7's gate in a session that has one. → Absence resolves to `default_role`. Unknown is never the restrictive value.
- **[The escape hatch is mistaken for a handoff mechanism]** (D7) A "Become controller" button is one small step from "anyone can take over at any time", which would make roles decorative. → It is gated on the deadlock state (no eligible driver) and on nothing else; outside it, a role comes from D3's memory or from an administrator. Any later relaxation of that gate should be treated as re-opening D3, not as a UI tweak — and note that `session-role-administration` will put a role control on the same panel, which makes the distinction easier to blur and worth guarding explicitly.
- **[Role and host are conflated by a reader]** They are different axes and the matrix has host-only rows, which invites "driver == host". → D2's table above is the canonical four-axis statement. `broadcast-ownership` D7 tabulates only the three axes that exist without roles and defers the composition rule here, so the two documents overlap by subset rather than by duplication and cannot disagree.
- **[Snapshot-carried role policy dies with the session]** If every peer leaves, `default_role` and the `peer_roles` memory are lost. → Accepted (broker/external storage considered and rejected); a screening organiser sets the policy for the session they are running. Less costly than it was under tokens, since nothing has to be re-distributed — only re-set.
- **[Field-group enforcement is subtler than per-message]** A role gate that stripped `view_mode` but left `clip_guid` would still be asserting what the session looks at. → Strip whole field groups in one core function, never at call sites — the rule `host-owned-visibility` §1.3 established and tested, including the `clip_guid`-without-`view_mode` case.
- **[A landed mechanism is silently absent in RV]** New core modules must be added to `makepackage.csh`'s hand-maintained vendoring list; `__init__.py` imports inside `try/except ImportError`, so an omission leaves the plugin connected but inert. This exact fault shipped with `authority.py` (`host-owned-visibility` §6a.1). → Prefer extending `authority.py` to adding a module; update the list in the same commit; check the startup banner proves which copy RV loaded.
- **[Roles are believed to be enforced against a peer that does not cooperate]** Enforcement is send-side, so a demoted peer running modified code keeps broadcasting, and a panel that says "Viewer" implies otherwise. → Within the trust model, but it must be written into the spec rather than left implied — it becomes considerably more visible once `session-role-administration` lets someone press a button and watch nothing happen.

## Migration Plan

0. ~~**Enforcement point, status contract, category table, kill-switch pattern, deterministic election, peer table, snapshot-carried authority.**~~ **Done** — `host-owned-visibility`, archived 2026-08-06.
0b. ~~**Write leases on position, display, and structure.**~~ **Done** — `broadcast-ownership`, archived 2026-08-10, enabled by default. The category step of the guard is complete; this change layers a ceiling over it and gates the claim path (D8).
0c. **Prerequisite: `peer-identity`.** Carries the `user` D3 keys on, and establishes the announce + roster propagation D5 needs.

1. **2a — core:** role matrix + policy in core and snapshot; role check in the guard ahead of the category check; role gate on `claim_category()` (D8); `role` on `PEER_ANNOUNCE` **and** the snapshot peer roster; `elect_host_guid` restricted to drivers, with absence resolving to `default_role`. Default policy `driver` — zero behaviour change until a session opts in. Expose role, elected host, and the driverless condition in the test inspector alongside the existing `is_host`/`host_guid`, in the runner's `ignore_keys`.
2. **2b — hosts:** `session_state.peer_role()` returns the real role; both panels show it and surface the driverless state; D7's **"Become controller"** action (enabled only when the peer table shows no eligible driver); optional "re-sync to driver". Substantially smaller than when this was written, because `session-state-ui` shipped the panel, the projection, and the placeholder this replaces.
3. **Follow-on:** `session-role-administration` — editing roles and `default_role`, and the targeted message and panel controls they need.

Rollback: `default_role: driver` restores today's behaviour without a rebuild (D6). The claim gate is inert under that default, since every peer may broadcast every category.

A soak of 2a must include a **viewer that is actively interacting**, not merely present: the D8 composition is invisible in a session where non-drivers sit still, which is how it would otherwise reach a screening.

## Open Questions

- ~~Is runtime promotion/demotion in 2a or deferred?~~ → **Deferred, to `session-role-administration`** (split 2026-08-10). It was cheap in mechanism — D5's re-announce path — and expensive in authority: promoting *someone else* needs a targeted message, an issuer rule, and a mutating panel control, none of which this change otherwise has. D7's self-elevation stays here because a deadlock with no exit cannot wait for a follow-on change.
- ~~What exactly makes a driver "eligible" for D7's gate?~~ → **Settled by `peer-departure` (implemented 2026-08-07): present in the peer table now means present.** That change added `PEER_DEPART` for clean exits and a 5 s heartbeat / 15 s liveness timeout for everything else, both converging on `drop_peer()`. A driver that has gone stops being in the table within one timeout, so D7's gate needs no staleness rule of its own — exactly the outcome this question was holding out for. The residual is latency, not correctness: the escape hatch can stay greyed out for up to the liveness timeout after an unclean exit.
**Settled since the first draft:**

- ~~Does the destructive-annotation row (`clear-all-paint` → driver only) need a category of its own, or is it a special case inside `annotation`?~~ → **Neither: an explicit `destructive` flag on the call**, settled 2026-08-12 against the code. A category cannot express it, and this is not a matter of taste — `BROADCAST_CATEGORIES` is keyed on **method name**, and RV's clear path emits through `broadcast_replace_annotation_commands`, the same method ordinary stroke edits use (`annotation_sync.py::on_clear_paint`, bound at `plugin.py:91`). A new category key would therefore gate every reviewer annotation edit or none. The two ways out are splitting the clear onto its own broadcast method (a protocol-shaped change to serve a role row) or having the caller state what the call *is*; the second is smaller and is consistent with D1, which forbids a plugin testing its own **authority** but not a plugin declaring its own **intent** — the same distinction that lets a plugin call `claim_category()` on user input. So `broadcast_replace_annotation_commands` gains a keyword-only `destructive=False`, both plugins' clear paths pass `destructive=True`, and the role gate reads it. It stays inside the `annotation` category throughout, so nothing about lease behaviour changes.
- ~~Should `STATE_SNAPSHOT` emission itself be role-gated, or left as a pure master concern?~~ → **Left ungated**, settled 2026-08-12. The master serves state regardless of role, as the matrix footnote already described. Gating it would let a session whose master is a viewer fail to answer a joiner's state request at all, which is a freeze — the exact failure direction D6 exists to keep this change away from, and one reached through the state-sync layer where no role mechanism would report it. D4's driver *preference* in master election is the right strength here: it makes a viewer-master unlikely without making it fatal.
- ~~Should a driverless session surface a visible state, or is silence acceptable?~~ → **Visible *and* actionable** (D7). A "Become controller" action, offered only in that state, both signals the condition and resolves it — a frozen session that explains nothing is the failure `host-owned-visibility` D4 was written against.
- ~~Does role warrant a user-visible affordance beyond token entry?~~ → **In this change, only the D7 escape hatch plus a read-only role display** (D9). `host-owned-visibility` §7.3's "election only, no UI" restraint still governs the normal case; D7 is an exit from a state with no other exit, not a handoff mechanism, and it leaves the `claim_host()` seam unused. Editing lives in `session-role-administration`.
- ~~`PEER_ROLE` as a new message vs. a field on an existing exchange~~ → **field on `PEER_ANNOUNCE`, and on the snapshot peer roster** (D5). That message now exists, and host election needs role in the peer table by both routes.
- ~~How does a reconnecting driver get its role back?~~ → **Identity-keyed memory, not a token** (D3, re-decided 2026-08-10). Tokens existed only for this case; `peer-identity` answers it directly, so they are cut.
- ~~Whether a reviewer may navigate~~ → **position yes, visibility no.** The original "cannot navigate" conflated the two; `host-owned-visibility` split them, and a reviewer must be able to reach the frame they are annotating.
- ~~Whether `display_state` is role-gated~~ → **no**, it is per-peer and ungated for every role including viewer (`host-owned-visibility` §7.1). Orthogonally, `broadcast-ownership` D8 does lease it on its own channel — the two compose, and neither implies the other.
- ~~Whether the test inspector needs authority state exposed~~ → **yes**, pattern established by `host-owned-visibility` §2.4.
