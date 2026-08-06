## Context

The sync protocol treats every peer as an equal broadcaster. Echo loops between peers are currently suppressed by ~15 mechanisms, most of them wall-clock windows (`suppress_until = now + 1.5s`) scattered across both host plugins. The proposal introduces broadcast ownership (Phase 1, write leases per message category) and session roles (Phase 2, driver/reviewer/viewer permissions).

Relevant current state:

- `SyncManager` (in `otio_sync_core`) already owns all `broadcast_*` methods, the master-election state machine, and `STATE_SNAPSHOT` assembly — it is the natural single enforcement point.
- The two host plugins have measurably drifted on hand-replicated protocol behaviour (discovery re-broadcast cadence, snapshot assembly placement), which rules out per-plugin enforcement.
- RV's host events are synchronous: a remote apply wrapped in `_rv_updating` reliably scopes its own echoes. xStudio's are not: applying a remote playhead change fires `attribute_changed` callbacks asynchronously, some arriving after the apply scope has exited. This asymmetry shapes the echo-filtering design below.
- `RabbitMQNetwork` already filters self-sent messages by `source_guid`, and `SyncManager._is_syncing` already scopes snapshot application. Both are retained.

## Goals / Non-Goals

**Goals:**

- Structurally eliminate the asynchronous time-window echo guards (~9 of 15) by making "two peers broadcasting the same category" impossible in the steady state.
- All peers converge on the same ownership view from the same message set, with no central authority.
- One enforcement point (`SyncManager.broadcast_*`) shared by both hosts; the Phase 2 role gate slots into the same choke point.
- Fully backwards-compatible protocol: peers that predate ownership ignore the new messages and behave as today.
- Phase 2: driver/reviewer/viewer roles workable for 20–30-person sessions, with token-based driver reconnection.

**Non-Goals:**

- Adversarial security. Enforcement is send-side; peers trust each other. Tokens gate accidents, not attackers.
- Receive-side validation or broker-side filtering.
- Locking the local UI of viewers/reviewers (local divergence + snap-back is accepted; see proposal).
- Per-object or per-clip ownership granularity — two coarse categories (navigation, structure) only.
- Stable cross-session peer identity (token elevation covers driver reconnection instead).
- Fairness guarantees in contention. Correctness = convergence; who wins a photo-finish claim is best-effort.

## Decisions

### D1: Ownership lives entirely in `SyncManager`; plugins never check it

`SyncManager` holds a small `OwnershipLease` per category (`owner_guid`, `claim_ts`, `deadline`, `pending_claimant`). Every `broadcast_*` method checks the relevant category before sending and returns a status (`SENT` / `SUPPRESSED`) the plugin can observe for logging or UI.

- *Alternative — per-plugin guard clauses (as in the proposal's first draft):* rejected; the hosts have already drifted on hand-replicated behaviour, and Phase 2 would double the duplicated surface.
- *Alternative — receive-side validation:* rejected; requires role/ownership metadata on every message and N enforcement points instead of one.

Category mapping is a static table in core: `broadcast_playback_state`, `broadcast_display_state`, selection → **navigation**; timeline add/remove/replace/rename, `SET_PROPERTY`, structural `INSERT_CHILD`/`REMOVE_CHILD`/`MOVE_CHILD` → **structure**; annotation paths → **no category** (multi-writer, never gated).

### D2: Convergence via deterministic ordering, not synchronized clocks

`CLAIM_OWNERSHIP` carries `(category, peer_guid, claim_ts)`. Conflict rule, applied identically by every peer: an earlier `claim_ts` wins; exact ties break to the lower `peer_guid`. A claim never preempts a live (unexpired) lease held by a *different* peer — it is recorded as `pending_claimant` instead.

The insight that makes this safe without clock sync: convergence only requires that all peers apply the same rule to the same message *content*. Clock skew between machines can bias *who* wins a simultaneous claim (a fairness concern, explicitly a non-goal) but cannot make two peers disagree about the winner, because both evaluate the same two `(claim_ts, guid)` pairs.

- *Alternative — Lamport/sequence numbers:* more machinery for the same convergence property; wall clock + GUID is sufficient because fairness is out of scope.
- *Alternative — master arbitrates claims:* re-introduces a round-trip through one peer and couples ownership to master liveness; rejected.

### D3: Lease expiry is computed on each peer's local clock from message receipt

Each owner broadcast (any message in the category, plus the initial claim) refreshes the lease. Every peer sets `deadline = local_monotonic_now + T` on *receipt*. No deadline value crosses the wire, so cross-machine clock sync is never needed. Peers may disagree about the expiry instant by up to one network latency — harmless, because expiry only matters when someone tries to claim, and that claim then propagates and converges under D2.

On expiry: if a `pending_claimant` exists, ownership transfers to it (each peer applies this locally and deterministically — pending claims are ordered by the same D2 rule); otherwise the category becomes FREE. Owner disconnect is handled by the same expiry path, no special case.

Default `T = 1.0 s` per category, configurable. The constraint that sets the floor: T must exceed the largest natural gap between broadcasts during continuous interaction (scrub events arrive at frame rate; structure operations can gap by several hundred ms during a `load_otio` rebuild). Structure may want a larger T than navigation.

### D4: Claims are triggered by user input, never by the broadcast guard

The guard in `broadcast_*` suppresses and returns `SUPPRESSED`; it does **not** auto-claim. Claiming is an explicit `manager.claim_category()` call made only from input-driven plugin paths. This is the load-bearing rule from the proposal's echo-filtering section: if apply-echoes could reach an auto-claiming guard, every remote apply would queue a claim and steal the lease at the next expiry.

How each host keeps echoes away from the claim call:

- **RV (synchronous events):** the existing `_rv_updating` apply-scope guard, converted to a depth-counted context manager (`with self._updating():`), wraps every remote apply. Events fired inside the scope never reach broadcast or claim paths. Complete and race-free because RV delivers events synchronously.
- **xStudio (asynchronous events):** apply-scope flags cannot cover callbacks that arrive after the scope exits. The claim gate therefore keeps **one** residual per-category horizon: applying a remote message stamps `last_remote_apply[category] = now`, and `claim_category()` is a no-op within a short horizon (~0.3 s) of that stamp. This is honestly a time window — but it is one mechanism, in one place, with a benign failure mode (a spurious *claim request*, which D2/D3 resolve; never a broadcast echo), replacing five scattered windows whose failure mode was a live feedback loop.

A suppressed broadcast never queues its payload. If a peer is granted ownership later (pending claim promoted), the plugin broadcasts its *current* state if the user is still interacting, or nothing. Replaying deferred state is itself an echo source.

### D5: Guard removal is a separate, revertible step behind a kill switch

Phase 1 lands in three commits: (1) core ownership mechanism + protocol messages, dark — enforcement returns `SENT` unconditionally when disabled; (2) enforcement enabled by default (`ORI_BROADCAST_OWNERSHIP=0` env kill switch reverts to today's behaviour); (3) removal of the nine retired guards. The kill switch makes commit 2 safe to soak in real sessions; commit 3 is a pure deletion that reverts cleanly if the soak finds a gap in the replacement table.

Prerequisite: the xStudio guards being deleted live as plugin-resident state that conceptually belongs to `PlaybackSyncController`; the planned encapsulation cleanup (controller-owned state + per-controller `reset()`) should land first so commit 3 is a small, controller-local diff. That cleanup is tracked outside this change.

### D6: Late joiners inherit ownership via `STATE_SNAPSHOT`

The snapshot gains a `broadcast_ownership` section: per category, `owner_guid` and `remaining_ms` (deadline expressed as a countdown, not an absolute time, for the same clock-independence reason as D3). Without this, a late joiner assumes both categories FREE and its first scrub fights the active driver. Phase 2 reuses the identical channel for `session_roles` (policy, `peer_roles` map, token hashes).

### D7: Roles are a static permission matrix evaluated in the same choke point

Phase 2 adds a `role → allowed message set` table in core (exactly the proposal's matrix, encoded once). The `broadcast_*` guard becomes: role check, then ownership check — both in `SyncManager`, both invisible to plugins except via the returned status. Role state: `self_role`, `peer_roles: {guid → role}`, `role_policy` (default role, token hashes), all carried in `STATE_SNAPSHOT`.

Role assignment on join: (1) GUID present in `peer_roles` → restore previous role; (2) token presented and hash matches → elevated role; (3) otherwise `default_role`. Tokens travel as salted hashes in the snapshot; the plaintext token is only ever sent by the *joining* peer inside its join message (acceptable under the trust model — the broker is already unauthenticated).

Master election prefers drivers (proposal's ordering). Implementation: the election response carries the peer's role; a peer defers self-election briefly if it is not a driver and the discovery responses show a driver present.

### D8: Interaction between ownership and the existing master concept

Master remains purely a state-sync role (snapshot authority). The existing `is_master` gate on OTIO-origin structure broadcasts in `sequence_sync.py` is subsumed by structure ownership: the master's structural rebuild paths must now `claim_category("structure")` like anyone else. This unifies two overlapping authority concepts into one, and multi-driver structure edits become safe (serialized by the lease) instead of silently master-only.

## Risks / Trade-offs

- **[Lease T mistuned]** Too short: ownership churns during natural pauses (scrub hesitation, `load_otio` gaps), letting echoes or rival claims in. Too long: handoff feels sluggish. → Per-category configurable T, defaults 1.0 s navigation / 2.0 s structure; the soak period behind the D5 kill switch is where these get tuned. Instrument lease transfers in the log.
- **[xStudio residual claim horizon is still a time window]** (D4) A very late async echo (> 0.3 s after apply) could trigger a spurious claim. → Failure mode is a pending claim resolved by D2/D3, not a broadcast; worst case is an unnecessary ownership transfer after the real owner goes idle, which self-corrects the moment either user next interacts.
- **[Mixed-version sessions]** An old peer ignores `CLAIM_OWNERSHIP` and broadcasts freely; new peers suppress against it but it never suppresses against them. → Echo behaviour with an old peer is no worse than today (its guards still run); document that full echo elimination requires all peers upgraded. The protocol remains wire-compatible.
- **[Guard removal deletes something the lease doesn't actually cover]** The proposal's replacement table is analysis, not proof. → D5's three-commit structure: guards are deleted only after enforcement has soaked with both mechanisms active; commit 3 reverts independently.
- **[Structure lease vs. long rebuilds]** Applying a remote `REPLACE_TIMELINE` can take seconds (xStudio `load_otio`), during which the applying peer's own poll scans may detect "changes". → These scans must sit behind the apply-scope guard (the `_reload_suppress_until` replacement from the proposal), not behind ownership — already reflected in the replacement table.
- **[Snapshot-carried role policy dies with the session]** If every peer leaves, tokens and role config are lost. → Accepted (proposal considered and rejected broker/external storage); screening organisers re-issue tokens per session.

## Migration Plan

1. **Phase 0 (prerequisite, separate change):** controller encapsulation cleanup in the xStudio plugin — move plugin-resident echo state into controllers, add per-controller `reset()`.
2. **Phase 1a:** ownership state machine + `CLAIM_OWNERSHIP`/`RELEASE_OWNERSHIP` + snapshot section in `otio_sync_core`, enforcement disabled. Unit tests for D2 convergence (concurrent claims, expiry transfer, late join).
3. **Phase 1b:** enforcement on by default with `ORI_BROADCAST_OWNERSHIP=0` kill switch; plugins wire `claim_category()` into input paths; RV's `_rv_updating` becomes a context manager; xStudio adds the claim horizon. Soak in the two-host test suite and live sessions.
4. **Phase 1c:** delete the nine retired guards. Pure removal commit.
5. **Phase 2a:** role matrix + policy in core and snapshot; role check in the guard; `PEER_ROLE` on join. Default policy `driver` — zero behaviour change until a session opts in.
6. **Phase 2b:** host UI — role indication, token entry on join, "re-sync to driver" action.

Rollback: each phase is additive and independently revertible; 1b and 2a have runtime kill switches (env var / `default_role: driver`).

## Open Questions

- Final T values per category (settle during 1b soak; see risk above).
- Should destructive annotation operations (`clear-all-paint`) require structure ownership, given annotations are otherwise multi-writer? Leaning yes — a clear is not additive.
- Phase 1 UX when a broadcast is suppressed: silent, or a subtle "peer X is driving" indicator? Silent is the 1b default; indicator may be folded into Phase 2 UI work.
- `PEER_ROLE` as a new message vs. a field on the existing join/`WHO_IS_MASTER` exchange — decide when touching the join path in 2a.
- Whether the sync test suite's inspector needs ownership state exposed in `ORI_FULLSTATE_FILE` for assertions (likely yes, cheap to add in 1a).
