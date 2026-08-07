## Context

**Split from `session-roles` on 2026-08-07** (Phase 1 of that proposal), and revised against the landed code. The sync protocol treated every peer as an equal broadcaster when this was first written. It no longer does: `host-owned-visibility` (archived 2026-08-06) made **visibility** single-writer under an elected host and, in doing so, built most of the enforcement machinery this design specified as new work. Echo loops are still suppressed by ~18 mechanisms, most of them wall-clock windows scattered across both host plugins.

What this change still introduces: broadcast **leases** over the position and structure categories.

Relevant current state — the top group is new since the first draft:

- **`authority.py` exists** and defines four categories (`visibility` / `position` / `annotation` / `structure`), the `SENT` / `SUPPRESSED` status vocabulary, `strip_visibility_fields`, the `ORI_VISIBILITY_AUTHORITY` kill switch, and `elect_host_guid`.
- **The enforcement point is built and proven.** `SyncManager._enforce_visibility` strips visibility fields in one place, called from `broadcast_playback_state`; a live soak recorded 284 strips and zero leaked `view_mode` sends. `test_no_plugin_gates_a_broadcast_on_being_host` holds the "plugins never check authority" invariant.
- **Deterministic election already follows `fix-discovery-thread-safety`'s discipline**: `elect_host()` owns the transition, `request_host_election()` enqueues from other threads onto a `queue.Queue` drained by the poll thread, `_drain_host_elections()` re-checks eligibility at drain time. Claim handling should reuse this pattern rather than invent a second one.
- **A peer table exists** (`SyncManager._peers`, `{guid: {app, capabilities}}`), populated by `PEER_ANNOUNCE`, with a settled announce-on-join / answer-once cadence.
- **`STATE_SNAPSHOT` already carries authority state** (`host_guid`), with the compatibility convention to copy: omit when unset, ignore `None` on receipt.
- **`owns_visibility()` is the shared predicate for local-intent branches** — deliberately *not* the broadcast gate. The lease has no equivalent need, because position has no intent inference; if one appears, it goes through core, not per plugin.
- `SyncManager` owns all `broadcast_*` methods, the master-election state machine, and `STATE_SNAPSHOT` assembly.
- The two host plugins have measurably drifted on hand-replicated protocol behaviour (discovery re-broadcast cadence, snapshot assembly placement), which rules out per-plugin enforcement.
- RV's host events are synchronous: a remote apply wrapped in `_rv_updating` reliably scopes its own echoes. xStudio's are not: applying a remote playhead change fires `attribute_changed` callbacks asynchronously, some arriving after the apply scope has exited. This asymmetry shapes the echo-filtering design below.
- `RabbitMQNetwork` already filters self-sent messages by `source_guid`, and `SyncManager._is_syncing` already scopes snapshot application. Both are retained.
- **A comparable guard deletion has already failed once.** `host-owned-visibility` §5.1 soaked, found its three candidate guards fired 0 times, and still closed **"do not delete"** — the host's visibility transition turned out to be caused by a *follower's structural message*, so the category was not single-writer by every route. D5 below is built around that result.

## Goals / Non-Goals

**Goals:**

- Structurally eliminate the asynchronous time-window echo guards (8 of ~18) by making "two peers broadcasting the same category" impossible in the steady state — for **position** and **structure**, the categories that remain multi-writer.
- All peers converge on the same ownership view from the same message set, with no central authority.
- One enforcement point (`SyncManager.broadcast_*`) shared by both hosts; `session-roles`' role gate slots into the same choke point.
- Fully backwards-compatible protocol: peers that predate ownership ignore the new messages and behave as today.

**Non-Goals:**

- Adversarial security. Enforcement is send-side; peers trust each other.
- Receive-side validation or broker-side filtering.
- Per-object or per-clip ownership granularity — two leased categories (position, structure) only.
- Re-cutting the category boundaries. They are `session-visibility-authority`'s to define; this change adds a mechanism *over* them. Visibility in particular keeps its static host owner and gains no lease.
- Closing the visibility bypass. `fix-visibility-authority-bypass` owns that, and the deletion step waits on it.
- Permissions, roles, or any per-participant concept. That is `session-roles`, which does not depend on this change.
- Fairness guarantees in contention. Correctness = convergence; who wins a photo-finish claim is best-effort.

## Decisions

### D1: Ownership lives entirely in `SyncManager`; plugins never check it

**Largely landed.** `SyncManager` gains a small `OwnershipLease` per *leased* category (`owner_guid`, `claim_ts`, `deadline`, `pending_claimant`). The status return and the "plugins never check" invariant already exist and are test-guarded; this decision now reduces to adding a second check beside the first.

- *Alternative — per-plugin guard clauses (as in the first draft):* rejected; the hosts have already drifted on hand-replicated behaviour. `host-owned-visibility` §1.4 verified the invariant holds today and added `test_no_plugin_gates_a_broadcast_on_being_host` to keep it.
- *Alternative — receive-side validation:* rejected; requires ownership metadata on every message and N enforcement points instead of one.

**The category mapping is not defined here.** It lives in `authority.BROADCAST_CATEGORIES`, established by `host-owned-visibility`, and this change consumes it:

| Category | Landed authority | This change adds |
|---|---|---|
| `visibility` (`view_mode`/`clip_guid` field group) | Host only, static | — nothing. A static single writer cannot contend with itself |
| `position` (`current_time`/`playing`/`playback_mode`) | Any peer | **Lease** |
| `position` (`broadcast_display_state` — channel, exposure, pan/zoom) | Any peer | **Lease, on its own channel** (D8) |
| `structure` (timeline add/remove/replace/rename, `SET_PROPERTY`, structural `INSERT_CHILD`/`REMOVE_CHILD`/`MOVE_CHILD`) | Any peer, gated by `is_master` | **Lease**, subsuming the `is_master` gate (D7) |
| `annotation` | Any peer, never gated | — nothing (multi-writer by design) |

The original draft's single **navigation** category is retired. `host-owned-visibility` rejected it explicitly — it "forces *may scrub* and *may change what is on screen* to be the same permission, which is exactly the split the product needs" — and the counter-argument holds: under one navigation category, `session-roles`' reviewer tier could not both let a supervisor reach the frame they are annotating and stop them changing everyone's shot.

Two mechanical consequences of enforcement being **per field group**, not per message:

- `authority.py` needs `strip_position_fields` alongside `strip_visibility_fields`, and `_enforce_position` alongside `_enforce_visibility`, so one playback message can lose one group and keep the other.
- `SUPPRESSED` already means *"sent with fields stripped"*, not *"not sent"*. A mixed message is the normal case. This change must not redefine the status, and tests must assert on the sent envelope rather than on whether a send occurred — the discipline `test_broadcast_authority.py` already follows.

### D2: Convergence via deterministic ordering, not synchronized clocks

`CLAIM_OWNERSHIP` carries `(category, peer_guid, claim_ts)`. Conflict rule, applied identically by every peer: an earlier `claim_ts` wins; exact ties break to the lower `peer_guid`. A claim never preempts a live (unexpired) lease held by a *different* peer — it is recorded as `pending_claimant` instead.

The insight that makes this safe without clock sync: convergence only requires that all peers apply the same rule to the same message *content*. Clock skew between machines can bias *who* wins a simultaneous claim (a fairness concern, explicitly a non-goal) but cannot make two peers disagree about the winner, because both evaluate the same two `(claim_ts, guid)` pairs.

- *Alternative — Lamport/sequence numbers:* more machinery for the same convergence property; wall clock + GUID is sufficient because fairness is out of scope.
- *Alternative — master arbitrates claims:* re-introduces a round-trip through one peer and couples ownership to master liveness; rejected.

**Precedent, now with an implementation to copy.** `authority.elect_host_guid` already resolves a distributed decision this way: a pure function of the peer table, ranked, with GUID ascending as the deterministic tie-break, so two peers evaluating the same inputs always agree. Claim resolution is the same shape with `(claim_ts, guid)` in place of `(rank, guid)`. Keeping the conflict rule a **pure function of the message set**, testable without a network, is the property that made host election straightforward to verify, and this change should preserve it rather than fold the rule into the lease state machine.

### D3: Lease expiry is computed on each peer's local clock from message receipt

Each owner broadcast (any message in the category, plus the initial claim) refreshes the lease. Every peer sets `deadline = local_monotonic_now + T` on *receipt*. No deadline value crosses the wire, so cross-machine clock sync is never needed. Peers may disagree about the expiry instant by up to one network latency — harmless, because expiry only matters when someone tries to claim, and that claim then propagates and converges under D2.

On expiry: if a `pending_claimant` exists, ownership transfers to it (each peer applies this locally and deterministically — pending claims are ordered by the same D2 rule); otherwise the category becomes FREE. Owner disconnect is handled by the same expiry path, no special case.

Default `T = 1.0 s` for position, configurable per channel; the agreed envelope is **500 ms – 2 s** (settled 2026-08-07). The constraint that sets the floor: T must exceed the largest natural gap between broadcasts during continuous interaction (scrub events arrive at frame rate; structure operations can gap by several hundred ms during a `load_otio` rebuild). Structure wants a larger T than position, display a smaller one — working defaults 0.5 s display / 1.0 s position / 2.0 s structure, spanning the envelope exactly.

### D4: Claims are triggered by user input, never by the broadcast guard

The guard in `broadcast_*` suppresses and returns `SUPPRESSED`; it does **not** auto-claim. Claiming is an explicit `manager.claim_category()` call made only from input-driven plugin paths. This is the load-bearing rule from the echo-filtering section: if apply-echoes could reach an auto-claiming guard, every remote apply would queue a claim and steal the lease at the next expiry.

How each host keeps echoes away from the claim call:

- **RV (synchronous events):** the existing `_rv_updating` apply-scope guard, converted to a depth-counted context manager (`with self._updating():`), wraps every remote apply. Events fired inside the scope never reach broadcast or claim paths. Complete and race-free because RV delivers events synchronously.
- **xStudio (asynchronous events):** apply-scope flags cannot cover callbacks that arrive after the scope exits. The claim gate therefore keeps **one** residual per-category horizon: applying a remote message stamps `last_remote_apply[category] = now`, and `claim_category()` is a no-op within a short horizon (~0.3 s) of that stamp. This is honestly a time window — but it is one mechanism, in one place, with a benign failure mode (a spurious *claim request*, which D2/D3 resolve; never a broadcast echo), replacing five scattered windows whose failure mode was a live feedback loop.

A suppressed broadcast never queues its payload. If a peer is granted ownership later (pending claim promoted), the plugin broadcasts its *current* state if the user is still interacting, or nothing. Replaying deferred state is itself an echo source.

### D5: Guard removal is blocked until the category is single-writer *by every route*

**Built around `host-owned-visibility`'s soak, which returned a negative result.** The original decision — three commits behind a kill switch, with deletion last — was procedurally right and still stands. What it got wrong was the *exit criterion*: it treated "the guards no longer fire" as sufficient evidence that they are unnecessary.

`host-owned-visibility` ran exactly that experiment. Live two-app session, enforcement on, three candidate visibility guards instrumented. They fired **0 times**. The task closed **"do not delete"**, because the same session showed the host isolating precisely the two clips the follower had isolated, in the same order: the follower's `ADD_TIMELINE` fired the host's own selection machinery, and the host broadcast the result as visibility — legitimately, being the host. The guards did not fire because the behaviour they guard was reaching the host by a route with no guard on it.

The rule this change adopts:

> **A guard cannot be shown unnecessary by a session in which the behaviour it guards is broken by another route.** Silence is evidence only once the category is single-writer by *every* route, not merely on the wire.

Two operational consequences:

1. **The exit criterion is a positive demonstration, not an absence.** Before deleting a position guard, show that the *contended* path is exercised — two peers scrubbing at once, with the lease transferring — and that the guard is still silent under it. A quiet session proves nothing.
2. **The deletion step is gated on `fix-visibility-authority-bypass`.** That change owns the inherited deletion question and may re-cut what counts as a visibility-driven transition. Deleting position guards while a known structure→visibility bypass is open repeats the mistake with a different category.

Otherwise unchanged. This change lands in three commits: (1) core lease mechanism + protocol messages, dark; (2) enforcement enabled by default with an `ORI_BROADCAST_OWNERSHIP=0` env kill switch; (3) removal of the retired guards. Follow `ORI_VISIBILITY_AUTHORITY`'s implementation: **read per call, not cached at import**, so it can be flipped in a running interpreter, and make the switch revert the behaviour *completely* rather than half of it — `owns_visibility()` honours the same switch precisely so a disabled enforcement does not leave inference branches running against a policy that is no longer in force.

**Prerequisite: satisfied.** `xstudio-controller-encapsulation` (archived 2026-08-07) moved the guards onto their owning controllers, so commit 3 is a controller-local diff at `self.playback._*` / `self.structure._*` / `self.annotation._*`.

### D6: Late joiners inherit ownership via `STATE_SNAPSHOT`

The snapshot gains a `broadcast_ownership` section: per category, `owner_guid` and `remaining_ms` (deadline expressed as a countdown, not an absolute time, for the same clock-independence reason as D3). Without this, a late joiner assumes both categories FREE and its first scrub fights the active driver.

The channel already carries authority state — `StateSnapshot.host_guid`, added by `host-owned-visibility` — and its **backwards-compatibility convention is the part to copy**, not just the location: the field is *omitted from the payload when unset*, and a `None` *is ignored on receipt*, so a peer predating the field cannot clear a locally-elected host by sending a snapshot without it. A lease section that took `None` as "FREE" would let one old peer release every lease in the session. Adoption goes through a named operation (`adopt_host()`'s counterpart), not direct assignment, for the same single-writer reason as D1.

`session-roles` reuses the identical channel for its `session_roles` section (policy, `peer_roles` map, token hashes), independently of this change.

### D7: Interaction between ownership, master, and host

**There are three concepts, not two,** and the first draft's "unifies two overlapping authority concepts into one" no longer describes the end state. Keeping them distinct is deliberate:

| Concept | Answers | Scope | Elected by |
|---|---|---|---|
| **master** | Who holds the canonical snapshot? | Session | Liveness / discovery timing |
| **host** | Who chooses what everyone looks at? | Category (visibility) | Capability, `elect_host_guid` |
| **lease owner** | Who is broadcasting this category *right now*? | Category (position, structure) | Claim + tiebreak (this change) |

`session-roles` adds a fourth, orthogonal axis (**role** — what a *participant* may emit at all). The composition rule lives there; it is stated once, in that change, to avoid two documents drifting on it.

Master remains purely a state-sync role. Host remains a static per-category owner — `host-owned-visibility` D2 rejected "host is always the master" precisely because a master re-election, which turns on liveness, would then silently change who controls the view. That separation is preserved here; a lease is a third axis and must not be collapsed onto either.

The `is_master` gate on OTIO-origin structure broadcasts (`sequence_sync.py:404`) **is** subsumed, by structure ownership: the master's structural rebuild paths must `claim_category("structure")` like anyone else. Multi-writer structure edits become safe (serialized by the lease) instead of silently master-only. `host-owned-visibility` left structure on `is_master` and named this change as the place to fold it in, so this is an agreed hand-off rather than a unilateral re-cut.

One caution from the soak. The bypass in `fix-visibility-authority-bypass` runs *structure → visibility*: a follower's `ADD_TIMELINE` moved the host's view. Taking structure under a lease touches the emitting side of exactly that path, so this change must not be assumed to fix the bypass, nor to be independent of it — a structure lease changes *who may emit* the message, not what the receiving host does with it.

### D8: Display state takes a lease, on a channel of its own

**Settled 2026-08-07: display state is leased.** The earlier lean was "exempt", on the grounds that nothing had been observed to echo through it. That is an argument from absence of evidence, and this change has already been burned once by exactly that reasoning (D5) — display state is broadcast, multi-writer, and driven by continuous UI gestures (exposure drags, pan/zoom), which is the same shape as the scrub traffic that does echo. Leasing it is the consistent choice.

**But it must not share the position lease.** That is the part "yes to a lease" does not settle on its own, and the shared option is the wrong one:

> If display shared position's lease, a peer adjusting exposure would have to hold the lease that a *different* peer needs to scrub. Changing your own exposure would block someone else's playhead, and vice versa — two unrelated operations serialised against each other for no reason.

So there are **three lease channels**: `position`, `display`, `structure`.

This is a lease-channel split, not a category re-cut — display state stays in the `position` category for *authority* purposes, which keeps `session-visibility-authority`'s category table untouched (a stated non-goal is re-cutting it) and keeps display ungated by role, as `host-owned-visibility` §7.1 settled. The two facts compose without conflict:

| Question | Answer for display state | Set by |
|---|---|---|
| Which *role* may emit it? | Any — driver, reviewer, **and viewer** | `session-roles` (§7.1 precedent) |
| Which *peer* may emit it right now? | The display lease holder | This change, D8 |

Consequence for D1's "one enforcement point" shape: `_enforce_position` splits its lease lookup by field group rather than taking a single category-wide lease. The category → lease-channel mapping is a static table in core, beside `BROADCAST_CATEGORIES`, so the indirection stays in one place and no call site chooses a channel.

Default `T` for display: **0.5 s**, the low end of the agreed range — a gesture that has stopped should release quickly, and unlike a scrub there is no clip-boundary gap to ride out.

## Risks / Trade-offs

- **[Lease T mistuned]** Too short: ownership churns during natural pauses (scrub hesitation, `load_otio` gaps), letting echoes or rival claims in. Too long: handoff feels sluggish. → Per-channel configurable T within the agreed 500 ms – 2 s envelope; working defaults 0.5 s display / 1.0 s position / 2.0 s structure. The soak period behind the D5 kill switch is where these get tuned. Instrument lease transfers in the log.
- **[Three lease channels instead of two]** (D8) More state, and a peer can now hold display but not position, so "who is driving" is no longer a single answer. → The channels are independent by design and never need to be held together; the cost is confined to the lease table and the deferred "peer X is driving" UI, which will have to name a channel rather than a peer.
- **[xStudio residual claim horizon is still a time window]** (D4) A very late async echo (> 0.3 s after apply) could trigger a spurious claim. → Failure mode is a pending claim resolved by D2/D3, not a broadcast; worst case is an unnecessary ownership transfer after the real owner goes idle, which self-corrects the moment either user next interacts.
- **[Mixed-version sessions]** An old peer ignores `CLAIM_OWNERSHIP` and broadcasts freely; new peers suppress against it but it never suppresses against them. → Echo behaviour with an old peer is no worse than today (its guards still run); document that full echo elimination requires all peers upgraded. The protocol remains wire-compatible.
- **[Guard removal deletes something the lease doesn't actually cover]** The replacement table is analysis, not proof — and the analogous deletion has already been attempted once and withdrawn. → D5's three-commit structure, plus its exit criterion: a *positive* demonstration under contention, not an absence of firings, and the deletion gated on `fix-visibility-authority-bypass`. Commit 3 reverts independently.
- **[A landed mechanism is silently absent in RV]** New core modules must be added to `makepackage.csh`'s hand-maintained vendoring list; `__init__.py` imports inside `try/except ImportError`, so an omission does not fail loudly — the plugin stays connected and inert. This exact fault shipped with `authority.py` (`host-owned-visibility` §6a.1) and was caught only because the harness printed host/follower per app. → Prefer extending `authority.py` to adding a module; update the list in the same commit; check the startup banner proves which copy RV loaded.
- **[Structure lease vs. long rebuilds]** Applying a remote `REPLACE_TIMELINE` can take seconds (xStudio `load_otio`), during which the applying peer's own poll scans may detect "changes". → These scans must sit behind the apply-scope guard (the `_reload_suppress_until` replacement), not behind ownership — already reflected in the replacement table.

## Migration Plan

0. ~~**Prerequisite:** controller encapsulation cleanup in the xStudio plugin.~~ **Done** — `xstudio-controller-encapsulation`, archived 2026-08-07. Guards now live on their owning controllers with per-controller `reset()`.
0b. ~~**Enforcement point, status contract, category table, kill-switch pattern, deterministic election, snapshot-carried authority.**~~ **Done** — `host-owned-visibility`, archived 2026-08-06. Step 1 builds on these rather than creating them.
1. **1a:** lease state machine + `CLAIM_OWNERSHIP`/`RELEASE_OWNERSHIP` + snapshot section + `strip_position_fields`/`_enforce_position` in `otio_sync_core`, enforcement disabled. Unit tests for D2 convergence (concurrent claims, expiry transfer, late join). Expose lease state in the test inspector alongside the existing `is_host`/`host_guid`.
2. **1b:** enforcement on by default with `ORI_BROADCAST_OWNERSHIP=0` kill switch — read per call, reverting behaviour completely (D5); plugins wire `claim_category()` into input paths; RV's `_rv_updating` becomes a context manager; xStudio adds the claim horizon. Soak in the two-host test suite and live sessions, **including a deliberately contended case** — the suite has never had two peers driving the same category at once, which is why the position guards have no positive evidence either way.
3. **1c:** delete the retired guards. Pure removal commit. **Blocked on `fix-visibility-authority-bypass`** and on 1b's contended soak (D5).

Rollback: each step is additive and independently revertible; 1b has a runtime kill switch.

## Open Questions

- Exact `T` per channel within the agreed 500 ms – 2 s envelope (tune during the 1b soak; starting points in D3/D8).
- Does `timeline_guid` need a category? It is on neither the visibility nor the position field list and is deliberately unstripped, so a follower can already move a peer's `active_timeline_guid` bookkeeping (not its view). `docs/visibility_authority_guards.md` flags this as worth revisiting if the categories are re-cut — this change does not re-cut them, but it does add the first lease over the fields alongside it.

**Settled 2026-08-07:**

- ~~Should `broadcast_display_state` be exempt from the position lease?~~ → **No — it is leased**, on its own channel rather than sharing position's. See D8: the "exempt" lean was an argument from absence of evidence, which D5 exists to distrust; and sharing position's lease would serialise exposure against another peer's scrub for no reason. Three channels: `position`, `display`, `structure`.
- ~~Lease duration range~~ → **500 ms – 2 s confirmed** as the envelope. Working defaults inside it: 0.5 s display, 1.0 s position, 2.0 s structure. Exact values still to be tuned in the 1b soak, which is the remaining open question above.
- ~~Should destructive annotation operations (`clear-all-paint`) require structure ownership?~~ → **No, not for now.** The earlier lean was yes ("a clear is not additive"), and that reasoning is still sound — but it is a scope increase on a change already carrying a blocked deletion step, and annotations stay multi-writer everywhere else. **Known accepted gap:** any peer can clear everyone's paint, unleased. Revisit if it bites, or when `session-roles` gives destructive operations a role to hang off — a clear is more naturally a driver-only action than a lease-holder-only one.
- ~~UX when a broadcast is suppressed~~ → **Silent for now.** A "peer X is driving" indicator is wanted eventually but deferred, most likely to *after* `session-roles`, whose 2b step already builds the peer/role UI this would live beside. Building it here would mean a second UI surface to reconcile later. `host-owned-visibility` §7.3 settled the parallel question for host the same way — election only, no UI — while shaping `elect_host` so a later `claim_host()` slots in without changing call sites; the lease API should leave the same room.
- ~~Whether the test inspector needs ownership state exposed for assertions~~ → **yes.** `host-owned-visibility` §2.4 established the pattern: `is_host`/`host_guid` on both hooks, in the runner's `ignore_keys` because they differ between peers by construction. It paid for itself immediately by making a stale RV plugin visible. Lease state follows the same route in 1a.
- ~~Whether `display_state` belongs to navigation~~ → it is **position** for authority purposes, per-peer and ungated by role (`host-owned-visibility` §7.1), with its own lease channel (D8).
