## Why

The sync protocol currently treats every peer as equal — any participant can navigate, modify content, and annotate at any time. This creates two distinct problems:

1. **Echo/bounce complexity**: When peer A scrubs and broadcasts, peer B applies the change locally, which fires a local "frame changed" event, which B's plugin detects and re-broadcasts back to A. This feedback loop requires ~15 fragile time-window suppression hacks spread across both plugins. Every new sync feature risks introducing new echo paths.

2. **Scale chaos**: In large review sessions (20–30 people in dailies or screenings), any participant can inadvertently change the selection, scrub to a different shot, add/remove media, or draw on screen. The protocol is effectively a megaphone in a library.

These are related but separable problems, and this proposal addresses them in two phases:

- **Phase 1: Broadcast ownership** — A lightweight "talking stick" mechanism that eliminates echo/bounce by ensuring only one peer at a time broadcasts per message category. No permissions, no roles — just contention resolution. This directly replaces the ~15 echo guard hacks.
- **Phase 2: Session roles** — A three-tier permission model (driver/reviewer/viewer) for large managed sessions. In these sessions, ownership is implicit (only the driver broadcasts), so the echo problem is structurally eliminated without even needing the Phase 1 mechanism.

### Current state

Today, the only distinguished role is **master** — but master is purely a *state-sync* concept (it holds the canonical timeline snapshot and responds to `STATE_REQUEST`). It says nothing about authority over content or navigation. Every peer has identical broadcast permissions.

There is one partial precedent: in `sequence_sync.py`, RV already gates OTIO-origin content changes to the master peer (`if not mgr.is_master: return`). This pattern is a natural starting point for broader broadcast gating.

### The echo/bounce problem

The codebase currently maintains **~15 separate echo suppression mechanisms**, most of which are time-window hacks (`suppress_until = now + 1.5s`) that are fragile and race-prone:

| Location | Guard | What it prevents |
|---|---|---|
| Core (`SyncManager`) | `_is_syncing` flag | Re-broadcast during snapshot apply |
| Core (`RabbitMQNetwork`) | `source_guid` filter | Receiving own messages |
| RV plugin | `_ignore_annotations_until` | Annotation re-trigger after remote apply |
| RV plugin | `_rv_updating` flag | Playback re-broadcast during remote apply |
| RV plugin | `is_master` gate | Non-master OTIO topology broadcast |
| xStudio plugin | `playback._playback_apply_suppress_until` | Playback echo during rapid scrub |
| xStudio plugin | `playback._selection_broadcast_suppress_until` | Selection echo after remote select |
| xStudio plugin | `structure._structural_mutation_suppress_until` | Structure echo after remote apply |
| xStudio plugin | `annotation._reload_suppress_until` | Bookmark re-trigger burst |
| xStudio plugin | `playback._local_scrub_active_until` | Clip-boundary echo during scrub |
| xStudio plugin | `playback._last_applied_frame` | Single-frame echo guard |
| xStudio plugin | `playback._applied_clip_echo_guid` | Clip-specific echo guard |
| xStudio plugin | `playback._applying_pinned_mode` | PSM attribute-change echo |
| xStudio plugin | `annotation._our_bookmark_uuids` | Remote annotation re-broadcast |
| ~~xStudio plugin~~ | ~~`_last_remote_stop_at`~~ | Deleted as dead code — never read |

**Note (Phase 0 landed):** the `xstudio-controller-encapsulation` change moved every
xStudio guard above off `ORISyncPlugin` and onto its owning controller, so Phase 1c
deletes them at `self.playback._*`, `self.structure._*`, and `self.annotation._*` — not
as plugin attributes. `_last_remote_stop_at` no longer exists (it was assigned in
`__init__` and never read). Note also that `annotation._our_bookmark_uuids` deliberately
survives `disconnect()`; if Phase 1c removes it, check the rejoin path that relies on it
to avoid re-broadcasting remote-origin bookmarks as duplicates.

The root cause is always the same: two peers simultaneously believe they should broadcast the same type of message. Phase 1 makes that structurally impossible.

### The scale problem

| Session size | Current model | Problem |
|---|---|---|
| 2–3 peers | Everyone can do everything | Works fine |
| 5–10 peers | Occasional conflicts | Manageable with coordination |
| 20–30 peers | Any of 30 people can change selection, scrub, draw, or add media | Chaotic and unusable |

---

## Phase 1: Broadcast Ownership (Write Leases)

### Concept

Before a peer broadcasts non-annotation messages, it must hold a short-lived **ownership lease** for that message category. While a peer holds a lease, it broadcasts freely. Other peers apply incoming messages but suppress their own outbound broadcasts for that category — no echo guards needed.

This is essentially a distributed "talking stick": you claim the stick, speak, and release it when you're done. Others listen silently while you hold it.

### Ownership categories

Not every message type needs ownership — only the ones that bounce. Annotations are additive (strokes), not positional, so multi-writer is fine:

| Category | Message types covered | Bounces today? | Ownership value |
|---|---|---|---|
| **Navigation** | `PLAYBACK_SET`, `DISPLAY_SET`, selection | Yes — the worst offender | High |
| **Structure** | `ADD_TIMELINE`, `REMOVE_TIMELINE`, `REPLACE_TIMELINE`, `RENAME_TIMELINE`, `SET_PROPERTY`, `INSERT_CHILD` (structural), `REMOVE_CHILD` | Yes — event cascades | High |
| **Annotation** | `ANNOTATION`, `INSERT_CHILD` (annotation track) | Mild — additive | ❌ No ownership (multi-writer) |

Starting with just **two ownership channels** — navigation and structure — covers the vast majority of echo guards.

### Lease lifecycle

```
  ┌─────────┐     ┌──────────────┐     ┌────────────────┐     ┌─────────┐
  │  FREE   │────▶│  CLAIMED     │────▶│  HELD (by A)   │────▶│  FREE   │
  │         │     │  (broadcast  │     │                │     │         │
  │         │     │   CLAIM msg) │     │  A broadcasts  │     │         │
  └─────────┘     └──────────────┘     │  freely. Each  │     └─────────┘
                                       │  broadcast     │
                                       │  refreshes the │
                                       │  lease timer.  │
                                       └────────────────┘
                                         │
                                         │ Lease expires after
                                         │ T ms of silence
                                         │ (e.g. 500ms–2s)
                                         ▼
                                       ┌────────────────┐
                                       │ If pending     │
                                       │ CLAIM exists → │
                                       │ transfer       │
                                       └────────────────┘
```

**Mechanics**:
- A peer that wants to broadcast first checks if the category is FREE or already HELD by itself.
- If FREE → auto-claim (broadcast a `CLAIM` message) and start broadcasting.
- If HELD by self → broadcast freely, refresh lease timer.
- If HELD by another peer → queue a claim request, suppress outbound until ownership transfers.
- Each broadcast refreshes the lease timer. Lease expires after T ms of silence (configurable, e.g. 500ms–2s).
- On expiry: if a pending claim exists, transfer to that peer. Otherwise, category becomes FREE.
- If the owner disconnects, the lease expires naturally via timeout.

### Contention resolution

When two peers try to claim the same category simultaneously:

- **Owner holds until idle**: If peer A owns navigation and is actively scrubbing, peer B's claim is queued. A keeps ownership until the lease expires (A stops broadcasting for T ms). This prevents mid-operation interruption.
- **Deterministic tiebreak**: Two peers can claim a FREE category in the same latency window, and each will see the other's `CLAIM` *after* its own. Without a rule they can disagree about the owner indefinitely, not briefly — there is no central authority to break the tie. Each `CLAIM` therefore carries a claim timestamp: earliest claim wins, lowest peer GUID breaks exact ties. Every peer applies the same rule to the same message set and converges on the same owner.
- **Latency window**: On local-network RabbitMQ, latency is sub-millisecond. During the brief claim-propagation window, two peers might overlap — worst case, a brief echo identical to today's behaviour, resolved by the tiebreak within one round-trip. Far better than permanent echo risk.
- **No starvation**: Leases are short-lived. An idle owner releases within T ms, so a waiting peer is never blocked for long.

### What this replaces

With ownership, **9 of 15 echo guards** become unnecessary. The pattern in the survivors: ownership retires the *asynchronous time-window* guards, while the cheap, synchronous *apply-scope* guards remain — they serve a different purpose (filtering echo events before they reach the broadcast path at all; see "Echo filtering" under Enforcement strategy):

| Current guard | Replaced by | Notes |
|---|---|---|
| `_playback_apply_suppress_until` | ✅ Navigation ownership | Owner broadcasts, non-owners suppress |
| `_selection_broadcast_suppress_until` | ✅ Navigation ownership | |
| `_local_scrub_active_until` | ✅ Navigation ownership | |
| `_last_applied_frame` | ✅ Navigation ownership | |
| `_applied_clip_echo_guid` | ✅ Navigation ownership | |
| `_last_remote_stop_at` | ✅ Navigation ownership | |
| `_rv_updating` | ⚠️ Still needed | Apply-scope reentrancy guard — filters echo events *before* the ownership check (see Echo filtering) |
| `_applying_pinned_mode` | ✅ Navigation ownership | |
| `_structural_mutation_suppress_until` | ✅ Structure ownership | |
| `_reload_suppress_until` | ⚠️ Still needed | The bookmark re-trigger burst manifests on the *annotation* broadcast path, which deliberately has no ownership channel — structure ownership cannot gate it. Needs an apply-scope guard around remote structural applies instead of a wall-clock window |
| `is_master` gate (OTIO stacks) | ✅ Structure ownership | Subsumed |
| `_is_syncing` | ⚠️ Still needed | Fundamental: snapshot apply guard |
| `source_guid` filter | ⚠️ Still needed | Fundamental: self-message discard |
| `_ignore_annotations_until` | ❌ Annotations stay multi-writer | |
| `_our_bookmark_uuids` | ❌ Annotations stay multi-writer | |

### Protocol additions (Phase 1)

- **`CLAIM_OWNERSHIP`**: `{ category: "navigation"|"structure", peer_guid: "...", claim_ts: <monotonic-ish wall clock> }` — `claim_ts` plus `peer_guid` drive the deterministic tiebreak for concurrent claims
- **`RELEASE_OWNERSHIP`**: `{ category: "navigation"|"structure", peer_guid: "..." }` (explicit release; timeout is the implicit fallback)
- **`STATE_SNAPSHOT`** gains a `broadcast_ownership` section — current owner GUID and lease deadline per category — so a late joiner converges on the session's ownership view instead of assuming both categories are FREE and immediately auto-claiming.
- Ownership state tracked locally by each peer — no central authority needed. Every peer applies the same claim/release messages, resolves conflicts with the same tiebreak rule, and converges on the same view of who owns what.

### Enforcement strategy

Send-side only — no receive-side validation; peers trust each other. The ownership check lives in **`SyncManager.broadcast_*` itself**, not in per-plugin guard clauses. The two host plugins have already drifted on hand-replicated protocol behaviour (discovery re-broadcast cadence differs between RV and xStudio); a guard duplicated per plugin will drift the same way. Centralising it in the core means both hosts get identical behaviour for free, and the Phase 2 role gate slots into the same choke point.

```python
# In SyncManager (Phase 1):
def broadcast_playback_state(self, state_dict, ...):
    if not self._owns_category("navigation"):
        self._claim_category("navigation")  # reached only for genuine user input — see below
        return BROADCAST_SUPPRESSED        # plugin can observe/log the suppression
    self._refresh_lease("navigation")
    # ... existing send path ...
```

#### Echo filtering must happen *before* the ownership check

On a non-owning peer, applying a remote message still fires local host events (RV's `frame-changed`, xStudio's async `attribute_changed`). If those echo events are allowed to reach the broadcast guard, every remote apply queues a claim request from the echoing peer — and when the owner's lease next expires, ownership transfers to that peer, which then broadcasts a stale frame back at the original owner. That is the same feedback loop, just running at lease-expiry speed instead of per-message. Two rules follow:

1. **Apply-scope reentrancy guards are fundamental, not replaced.** `_is_syncing` and RV's `_rv_updating` (and an equivalent around remote structural applies, replacing `_reload_suppress_until`'s wall-clock window) stop echo events from entering the broadcast path at all. Ownership retires the asynchronous *time-window* guards; the synchronous *apply-scope* guards stay.
2. **A suppressed broadcast does not queue its payload.** When ownership is later granted, the peer broadcasts its *current* state if the user is still interacting, or nothing at all. Replaying deferred stale state on lease transfer is itself an echo source.

---

## Phase 2: Session Roles (Permissions)

### Concept

Phase 1 solves *contention* (who IS broadcasting right now). Phase 2 solves *authorization* (who CAN broadcast). Together they cover both the echo problem and the large-session control problem.

### Three-tier role model

Introduce `driver`, `reviewer`, and `viewer` roles that control which message types a peer is *allowed* to emit:

- **Driver**: Full control — navigation, content mutations, annotations, playback. Equivalent to today's behaviour.
- **Reviewer**: Can annotate (draw, text) but cannot navigate or modify content. Intended for leads/supervisors who need to mark up shots but shouldn't drive the session.
- **Viewer**: Passive observer — receives all state but emits nothing. The existing `sync_viewer` is a natural viewer. Intended for the majority of a large screening audience.

#### Per-message permission matrix

| Message type | Driver | Reviewer | Viewer |
|---|---|---|---|
| `ADD_TIMELINE` | ✅ | ❌ | ❌ |
| `REMOVE_TIMELINE` | ✅ | ❌ | ❌ |
| `REPLACE_TIMELINE` | ✅ | ❌ | ❌ |
| `RENAME_TIMELINE` | ✅ | ❌ | ❌ |
| `PLAYBACK_SET` | ✅ | ❌ | ❌ |
| `DISPLAY_SET` | ✅ | ❌ | ❌ |
| `SET_PROPERTY` | ✅ | ❌ | ❌ |
| `ANNOTATION` (strokes) | ✅ | ✅ | ❌ |
| `INSERT_CHILD` (annotation track) | ✅ | ✅ | ❌ |
| `INSERT_CHILD` (structural) | ✅ | ❌ | ❌ |
| `REMOVE_CHILD` | ✅ | ❌ | ❌ |
| `STATE_SNAPSHOT` | ✅* | ❌ | ❌ |

\* `STATE_SNAPSHOT` is a master concern, orthogonal to session role — but in practice the master should be a driver.

### How roles interact with ownership

The two mechanisms are orthogonal but complementary:

| Configuration | Who resolves echo? | Who resolves permissions? |
|---|---|---|
| Multi-driver (default, small sessions) | **Ownership** (Phase 1) | Everyone can do everything |
| Managed session (1 driver + viewers) | **Roles** make ownership implicit | **Roles** (Phase 2) |
| Managed session (driver + reviewers + viewers) | **Roles** (navigation/structure), **ownership** not needed | **Roles** (Phase 2) |

In a managed session with a single driver, the driver implicitly owns all categories — no claim messages needed. Phase 1 ownership is most valuable in multi-driver sessions where everyone has permission but contention must be resolved.

```
Full broadcast guard (Phase 1 + Phase 2):

  Can this peer broadcast this message?

  1. Check ROLE: is this peer a driver/reviewer for this message type?
     └── NO → suppress (role gate)
     └── YES ↓

  2. Check OWNERSHIP: does this peer own this category?
     └── YES → broadcast freely
     └── NO → is the category FREE?
         └── YES → auto-claim and broadcast
         └── NO → queue claim, suppress until ownership transfers
```

### Enforcement strategy: send-side only

Roles are enforced by **suppressing outbound broadcasts** — the role check lives in the same `SyncManager.broadcast_*` choke point as the Phase 1 ownership check, so both hosts share one implementation. There is no protocol-level message rejection or broker-side filtering.

**Why send-side, not receive-side?** We considered three enforcement points:

1. **Send-side (chosen)**: The sender checks its own role before emitting. Simple guard clauses in the core. No protocol changes needed for enforcement itself.
2. **Receive-side**: Every receiver validates the sender's role before applying a message. Requires the protocol to carry role information per-message and every peer to enforce it. Much more complex.
3. **Broker-side**: RabbitMQ would reject unauthorized messages. The fanout exchange topology doesn't naturally support per-topic authorization, and this would tie the role model to broker infrastructure.

Send-side enforcement is sufficient because we're not defending against adversaries — we're preventing well-intentioned reviewers from accidentally stepping on each other. A "soft" enforcement at the sender is the right match for this trust model.

### Local interaction model

When a viewer or reviewer is blocked from broadcasting (e.g., they scrub their playhead), their **local app still reacts normally** — they can explore locally, but their actions don't propagate. When the driver next sends a playback or navigation message, all peers re-sync to the driver's state.

We considered three options:

1. **Suppress locally too** — Viewer's app is fully locked to the driver. Like a screen share. Requires deep hooks into RV/xStudio to prevent local interaction — impractical.
2. **Let local state diverge, re-sync on next driver message (chosen)** — Less invasive. Viewer can explore locally but snaps back when the driver advances. Natural for a screening context.
3. **Suppress + UI feedback** — Block the action locally and show a toast. Best UX but most per-application work.

Option 2 is the pragmatic starting point. A manual "re-sync to driver" menu option is a reasonable addition for when a viewer wants to snap back without waiting.

### Session role policy

A session-level configuration controls how new joiners are assigned roles:

- **`default_role`**: The role assigned to peers who join without a token. Defaults to `driver` for backwards compatibility (small sessions work as before). Set to `viewer` for screening mode.
- **`driver_token`**: Optional shared secret. Any peer presenting this token on join is elevated to `driver`. Can be shared out-of-band (e.g., "use this code if you need to drive").
- **`reviewer_token`**: Optional shared secret for reviewer elevation.
- **Runtime changes**: A driver can promote/demote peers live during the session (stretch goal).

### Driver reconnection

The token model solves a critical edge case: a driver who accidentally disconnects and re-joins their own managed session would otherwise be assigned the default role (`viewer`).

We considered three approaches:

1. **Stable peer identity** — GUIDs persist across sessions (stored in config). Peers are recognised on reconnect. Clean but introduces identity management concerns (shared machines, per-project identities).
2. **Token-based elevation (chosen)** — The driver token lets anyone reclaim the driver role regardless of GUID. Also enables co-drivers and handoff. Simple and stateless.
3. **Role claim + peer approval** — Rejoining peer requests promotion; existing peers approve. More complex, no clear benefit.

Additionally, the session maintains a `peer_roles` map (`guid → role`) in memory. If a peer reconnects with the **same GUID**, their previous role is restored without needing a token. The token is the fallback for when the GUID changes.

### Where session state lives

Role policy is carried in the `STATE_SNAPSHOT` payload, alongside existing timeline and playback state. When a new peer joins and receives the snapshot, they also receive the role configuration.

We considered three options:

1. **In the STATE_SNAPSHOT (chosen)** — Natural extension of the existing sync model. When the master sends state to a new joiner, it includes role policy. If the master dies, the new master already has a copy.
2. **Broker-side** — Stored as pinned message or queue metadata. Survives all peers leaving, but adds infrastructure dependency and reduces portability.
3. **External (config file, API)** — Clean separation, but another moving part.

### Role-aware master election

Master election should prefer drivers, since the master holds the session config and needs full broadcast capability:

1. **Prefer a driver** — natural fit; they need to send `STATE_SNAPSHOT` and control the session.
2. **If no drivers, promote a reviewer** — becomes master for state-sync but not automatically a driver (role and master status remain orthogonal).
3. **If only viewers, promote one to master** — session effectively freezes from a content/navigation perspective. Everyone can see what's there but no one can drive until a driver rejoins.

### Peer role awareness

Peers broadcast their role on join (via a new `PEER_ROLE` message or a field on existing join messages) so the session knows who's connected and in what capacity. This enables UI features like "25 viewers, 2 reviewers, 1 driver connected".

---

## Capabilities

### New Capabilities
- `broadcast-ownership`: The write-lease mechanism (Phase 1) — ownership categories (navigation, structure), claim/release protocol messages with deterministic tiebreak, lease lifecycle, contention resolution, ownership state in `STATE_SNAPSHOT`, and the centralised ownership-based broadcast guard that replaces ~9 echo suppression mechanisms.
- `session-role-model`: The three-tier role hierarchy (Phase 2) — driver, reviewer, viewer roles, the per-message permission matrix, and role assignment logic (default policy, token elevation, GUID-based memory, runtime promotion/demotion).
- `session-role-config`: Session-level configuration (Phase 2) — default joiner role, driver/reviewer tokens, session state storage in `STATE_SNAPSHOT`, and the driver reconnection flow.

### Modified Capabilities
- `otio-sync-core`: Phase 1 — ownership tracking per category, claim/release message handling with deterministic tiebreak, ownership enforcement inside `broadcast_*`, ownership state in `STATE_SNAPSHOT`. Phase 2 — role policy in `STATE_SNAPSHOT`, role-aware master election, per-peer role tracking, role enforcement at the same `broadcast_*` choke point.
- `ori-session-management`: Phase 2 — session lifecycle (join, leave, reconnect) must account for role assignment and restoration.
- `openrv-sync-plugin`: Phase 1 — remove asynchronous time-window echo suppression (ownership enforcement lives in core); retain apply-scope reentrancy guards. Phase 2 — UI affordances for role indication and token entry.
- `xstudio-plugin-module-structure`: Phase 1 — remove asynchronous time-window echo suppression (ownership enforcement lives in core); retain apply-scope reentrancy guards, including a new one around remote structural applies to replace `_reload_suppress_until`. Phase 2 — UI affordances for role indication and token entry.
- `protocol-message-docs`: Phase 1 — `CLAIM_OWNERSHIP` and `RELEASE_OWNERSHIP` messages, `broadcast_ownership` section in `STATE_SNAPSHOT`. Phase 2 — `PEER_ROLE` message, role policy section in `STATE_SNAPSHOT` payload, token hash fields.

## Impact

### Phase 1 impact

- **Core**: `SyncManager` gains ownership tracking (two categories, current owner GUID, lease expiry, deterministic claim tiebreak) and enforces the ownership check inside its `broadcast_*` methods. Two new protocol messages (`CLAIM_OWNERSHIP`, `RELEASE_OWNERSHIP`); `STATE_SNAPSHOT` gains a `broadcast_ownership` section for late joiners.
- **Plugins**: No per-plugin guard clauses — plugins observe the suppressed/sent result from `broadcast_*` and keep their apply-scope reentrancy guards (which filter echo events before the broadcast path). **~9 asynchronous time-window suppression mechanisms can be removed** once ownership is in place. This is a net reduction in code complexity.
- **Protocol**: Fully backwards compatible — peers that don't understand ownership messages ignore them and behave as today.
- **Risk**: Low. Ownership is additive — it doesn't change any existing receive paths or message formats.

### Phase 2 impact

- **Core**: `SyncManager` gains role policy storage, peer role tracking, token verification, role-aware master election. `STATE_SNAPSHOT` payload extended with `session_roles` section.
- **Plugins**: Role-based broadcast guards layered on top of ownership guards. UI affordances: role indication, connected peer roles, "join as" or token entry, optional "re-sync to driver" action. No changes to receive paths.
- **sync_viewer**: Already a natural viewer. May gain UI to show role breakdown.
- **Protocol**: Backwards compatible — sessions without role config behave as today (`default_role = "driver"`). Token hashes (not plaintext) in `STATE_SNAPSHOT`.
- **Future**: A streaming/web-based reviewer client is plausible — viewers and reviewers don't necessarily need local media playback (though viewers are expected to have local media in the primary use case).

### Open considerations (not blocking)

- **Disruptive vs non-disruptive mutations**: Some content changes (e.g., appending a clip to the end of a playlist) don't change the current selection and could be safe for non-drivers. The initial implementation uses the simpler per-message-type model; finer-grained "disruption level" gating could be a future refinement.
- **Application-type heuristics**: In mixed sessions (e.g., xStudio + RV), xStudio is often the natural content authority since RV typically shows a subset. An automatic heuristic ("xStudio defaults to driver, RV defaults to reviewer") could complement explicit role assignment, but is not part of the initial proposal.
- **Multiple drivers**: The model supports multiple drivers (it's not restricted to one). In a small session this is the default. In a managed session, multiple people could hold the driver token. Phase 1 ownership resolves contention between multiple drivers.
- **Ownership lease duration**: The optimal lease timeout (T ms) needs tuning. Too short and ownership churns during natural pauses in scrubbing. Too long and handoff feels sluggish. 500ms–2s is the likely range, possibly configurable per-category.
