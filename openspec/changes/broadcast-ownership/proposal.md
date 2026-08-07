## Why

When peer A scrubs and broadcasts, peer B applies the change locally, which fires a local "frame changed" event, which B's plugin detects and re-broadcasts back to A. This feedback loop is suppressed today by **~18 fragile time-window hacks** (`suppress_until = now + 1.5s`) spread across both plugins. Every new sync feature risks introducing new echo paths, and the 2026-08-05 debugging session added three more guards in a single sitting — each fix uncovering another broadcast path that did not consult the guard.

The root cause is always the same: two peers simultaneously believe they should broadcast the same type of message.

This change introduces a lightweight **write lease** — a distributed "talking stick" — so that only one peer at a time broadcasts per category. No permissions, no roles: just contention resolution.

> **Split from `session-roles` on 2026-08-07.** This was Phase 1 of that proposal. The two phases were separated because they turned out to be independent: `session-roles` composes with *category authority*, which already exists for visibility, so it does not need this change to land first. Keeping them together made the unblocked half hostage to this one, which is blocked (see "What this replaces"). `session-roles` remains the companion change: the two share an enforcement point and a vocabulary, and D7 here tabulates the authority axes that exist without roles, deferring the role-composition rule to that change.

### Current state

`host-owned-visibility` (archived 2026-08-06) built most of this change's *machinery* while solving a different problem — making **visibility** single-writer under an elected **host**. What exists today in `python/otio_sync_core/authority.py` and `manager.py`, which this change builds on rather than re-invents:

| Landed | Where |
|---|---|
| A category table mapping each `broadcast_*` method to a category | `authority.BROADCAST_CATEGORIES` — `visibility` / `position` / `annotation` / `structure` |
| `SENT` / `SUPPRESSED` returned from every `broadcast_*` | `authority.SENT`, `authority.SUPPRESSED` (`broadcast_add_annotation` excepted — callers need the clip GUID, and annotation is never gated) |
| Field-group enforcement inside one core method | `SyncManager._enforce_visibility` → `authority.strip_visibility_fields`, called only from `broadcast_playback_state` |
| A runtime kill switch, read per call | `ORI_VISIBILITY_AUTHORITY=0` (`authority.enforcement_enabled`) |
| Deterministic election with the `fix-discovery-thread-safety` discipline | `SyncManager.elect_host()` owns the transition; `request_host_election()` enqueues from other threads; `_drain_host_elections()` re-checks eligibility at drain time |
| A peer table with app + capabilities | `SyncManager._peers`, populated by the `PEER_ANNOUNCE` message |
| Authority state carried to late joiners | `StateSnapshot.host_guid`, adopted via `adopt_host()` — omitted when unset, `None` ignored on receipt |
| Authority exposed to the test harness | `is_host` / `host_guid` on both inspector hooks, in the runner's `ignore_keys`; `drive_host: true` in `sync_tests.yaml` |

So the shape originally argued for — one enforcement point, a status return, a category table, a kill switch, deterministic election, snapshot-carried authority — is **built and proven on the wire**. What is *not* built is the dynamic part: leases, claims, contention, and any authority over **position** or **structure**.

`sequence_sync.py:404` still gates OTIO-origin content changes on `if not mgr.is_master: return`. `host-owned-visibility` deliberately left structure on `is_master` and named folding it in as this change's job (its design.md, Non-Goals).

### The echo/bounce problem

Inventory re-verified against the tree on 2026-08-07 — the earlier "~15" undercounted, because chasing the 2026-08-05 position bugs added guards faster than the proposal was updated:

| Location | Guard | What it prevents | Category |
|---|---|---|---|
| Core (`SyncManager`) | `_is_syncing` flag | Re-broadcast during snapshot apply | all |
| Core (`RabbitMQNetwork`) | `source_guid` filter | Receiving own messages | all |
| RV plugin | `_ignore_annotations_until` | Annotation re-trigger after remote apply | annotation |
| RV plugin | `_rv_updating` flag | Re-broadcast during remote apply | position + annotation |
| RV plugin | `is_master` gate (`sequence_sync.py:404`) | Non-master OTIO topology broadcast | structure |
| xStudio plugin | `playback._playback_apply_suppress_until` | Playback echo during rapid scrub | position |
| xStudio plugin | `playback._last_applied_frame` / `_last_polled_frame` | Single-frame echo guard | position |
| xStudio plugin | `playback._last_received_frame` | A view change clobbering a peer's seek with a fresh playhead's 0 | position |
| xStudio plugin | `playback._local_scrub_active_until` | Selection-driven clip-start seeks snapping our playhead | position |
| xStudio plugin | Throttled scrub flush re-checking the guard at flush time | Releasing a position captured before a peer began driving | position |
| xStudio plugin | `playback._loop_mode_apply_suppress_until` | `playback_mode` echo | position |
| xStudio plugin | `playback._applying_pinned_mode` | PSM attribute-change echo (apply-scope flag, not a window) | position |
| xStudio plugin | `playback._selection_broadcast_suppress_until` | Selection echo after remote select | visibility |
| xStudio plugin | `playback._applied_clip_echo_guid` / `_applied_clip_echo_until` | Delayed `show_atom` outliving the window above | visibility |
| xStudio plugin | `playback._local_view_action_until` | In-flight remote view message overriding a local one | visibility |
| xStudio plugin | `structure._structural_mutation_suppress_until` | Structure echo after remote apply | structure |
| xStudio plugin | `annotation._reload_suppress_until` | Bookmark re-trigger burst | structure→annotation |
| xStudio plugin | `annotation._our_bookmark_uuids` | Remote annotation re-broadcast | annotation |
| ~~xStudio plugin~~ | ~~`_last_remote_stop_at`~~ | Deleted as dead code — never read | — |

The category column is the load-bearing addition. A guard is only retirable by the mechanism that makes its category single-writer — and most of these turn out to be **position** guards, not the "navigation" guards the original proposal assumed. That reshapes this change's target; see "What this replaces".

**Prerequisite complete.** `xstudio-controller-encapsulation` (archived 2026-08-07) moved every xStudio guard above off `ORISyncPlugin` and onto its owning controller, so the deletion step is a controller-local diff at `self.playback._*`, `self.structure._*`, and `self.annotation._*` — not plugin attributes. Note that `annotation._our_bookmark_uuids` deliberately survives `disconnect()`; if it is ever removed, check the rejoin path that relies on it to avoid re-broadcasting remote-origin bookmarks as duplicates.

**Two *uses* were already retired**, without deleting the guards themselves. `host-owned-visibility` §4.4 replaced two reads of `_playback_apply_suppress_until` — the `playing_override` on a Pinned Source Mode transition, and the `_new_source_clip` frame reset — with `manager.owns_visibility()`. The deadline is still armed and still read by the position guards. This is the pattern the deletion step should expect: category authority retires *inferences*, and only sometimes retires the state they were inferring from.

## Concept

Before a peer broadcasts a position or structure message, it must hold a short-lived **ownership lease** for that category. While a peer holds a lease, it broadcasts freely. Other peers apply incoming messages but suppress their own outbound broadcasts for that category — no echo guards needed.

You claim the stick, speak, and release it when you're done. Others listen silently while you hold it.

### Ownership categories

**The categories are not this change's to define.** `host-owned-visibility` established them in `authority.BROADCAST_CATEGORIES`, splitting what the original proposal called "navigation" into two, on the grounds that it "forces *may scrub* and *may change what is on screen* to be the same permission, which is exactly the split the product needs". That objection stands and is adopted. This change takes the four categories as given and decides only **which of them gain a lease**:

| Category | Message types / field groups | Authority today | Lease? |
|---|---|---|---|
| **visibility** | `view_mode` / `clip_guid` field group of `PLAYBACK_SETTINGS_1.0` | **Host only** — already single-writer, statically | ❌ Not needed. A static single writer cannot contend with itself |
| **position** | `current_time` / `playing` / `playback_mode` field group | Any peer | ✅ **Yes — this is where the echo actually lives** |
| **position** | `DISPLAY_SET` — channel, exposure, pan/zoom | Any peer | ✅ Yes, on a **separate lease channel** (design.md D8) |
| **structure** | `ADD_TIMELINE`, `REMOVE_TIMELINE`, `REPLACE_TIMELINE`, `RENAME_TIMELINE`, `SET_PROPERTY`, `INSERT_CHILD` (structural), `REMOVE_CHILD`, `MOVE_CHILD` | Gated by `is_master` | ✅ Yes — subsumes the `is_master` gate |
| **annotation** | `ANNOTATION`, `INSERT_CHILD` (annotation track) | Any peer, never gated | ❌ No ownership (additive, multi-writer) |

The two ownership channels are therefore **position and structure**, not "navigation and structure". This is a substitution, not an addition: visibility was the half of old-navigation a lease would have covered, and it is already covered by a simpler mechanism — a static elected owner. Position is the half a lease is genuinely for, because the product requires it to stay multi-writer.

> **Static host authority and dynamic leases are not competing designs.** They apply to different categories for different reasons. Visibility has one owner because the *product* wants one person choosing what everyone looks at. Position has a rotating owner because the product wants everyone able to scrub — so contention is real and must be resolved rather than assigned.

`DISPLAY_SET` sits under position and is **ungated by role** (`host-owned-visibility` §7.1, settled: reviewers legitimately toggle channels and exposure locally). It nonetheless **takes a lease** — settled 2026-08-07 — because it is broadcast, multi-writer, and driven by continuous gestures, the same shape as the scrub traffic that does echo. Those two facts are not in tension: role decides *who may ever* emit it, the lease decides *who is emitting it right now*.

It gets a **channel of its own** rather than sharing position's, so that adjusting exposure never blocks another peer's scrub. That makes three lease channels — `position`, `display`, `structure` — without re-cutting the categories themselves. See design.md D8.

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
- If FREE → claim (broadcast a `CLAIM` message) and start broadcasting.
- If HELD by self → broadcast freely, refresh lease timer.
- If HELD by another peer → queue a claim request, suppress outbound until ownership transfers.
- Each broadcast refreshes the lease timer. Lease expires after T ms of silence (configurable, e.g. 500ms–2s).
- On expiry: if a pending claim exists, transfer to that peer. Otherwise, category becomes FREE.
- If the owner disconnects, the lease expires naturally via timeout.

### Contention resolution

When two peers try to claim the same category simultaneously:

- **Owner holds until idle**: If peer A owns position and is actively scrubbing, peer B's claim is queued. A keeps ownership until the lease expires (A stops broadcasting for T ms). This prevents mid-operation interruption.
- **Deterministic tiebreak**: Two peers can claim a FREE category in the same latency window, and each will see the other's `CLAIM` *after* its own. Without a rule they can disagree about the owner indefinitely, not briefly — there is no central authority to break the tie. Each `CLAIM` therefore carries a claim timestamp: earliest claim wins, lowest peer GUID breaks exact ties. Every peer applies the same rule to the same message set and converges on the same owner.
- **Latency window**: On local-network RabbitMQ, latency is sub-millisecond. During the brief claim-propagation window, two peers might overlap — worst case, a brief echo identical to today's behaviour, resolved by the tiebreak within one round-trip. Far better than permanent echo risk.
- **No starvation**: Leases are short-lived. An idle owner releases within T ms, so a waiting peer is never blocked for long.

## What this replaces

**The predecessor of this table made a claim that a live soak falsified, and the correction is the most important thing on this page.**

`host-owned-visibility` §5.1 set out to delete three visibility guards on the reasoning that *"a host's transitions are user-caused by definition."* It soaked the change in a live two-app session on 2026-08-06. The three guards fired **0 times** — which, read alone, says "inert, delete them". The task closed **"do not delete"** anyway, because the same session showed the host isolating exactly the two clips the follower had isolated, in the same order: the follower's `ADD_TIMELINE` fired the host's own selection machinery, and the host then broadcast that as visibility, legitimately, because it *is* the host. The premise was false — the host's transition was caused by a follower's structural message.

The general rule this change inherits:

> **A guard cannot be shown unnecessary by a session in which the behaviour it guards is broken by another route.** Zero firings is evidence of nothing until the category is provably single-writer *by every route*, not just on the wire.

`fix-visibility-authority-bypass` owns the open question, and `docs/visibility_authority_guards.md` carries the full inventory plus a warning against re-adopting the falsified premise from the table alone. **The deletion step is blocked on that change closing.**

With that established, the honest replacement table. Visibility rows are settled elsewhere and listed for completeness:

| Current guard | Replaced by | Notes |
|---|---|---|
| `_playback_apply_suppress_until` | ✅ Position ownership | Owner broadcasts, non-owners suppress. Its two *visibility* reads are already retired (§4.4 of `host-owned-visibility`); the deadline itself waits on this lease |
| `_last_applied_frame` / `_last_polled_frame` | ✅ Position ownership | |
| `_last_received_frame` | ✅ Position ownership | |
| `_local_scrub_active_until` | ✅ Position ownership | |
| Throttled scrub flush re-check | ✅ Position ownership | The flush cannot release a stale position if only one peer is broadcasting position at all |
| `_loop_mode_apply_suppress_until` | ✅ Position ownership | |
| `_applying_pinned_mode` | ⚠️ Still needed | Apply-scope flag around a write we make ourselves — not a time window and not an inference. Same class as `_rv_updating` |
| `_structural_mutation_suppress_until` | ✅ Structure ownership | |
| `is_master` gate (`sequence_sync.py:404`) | ✅ Structure ownership | Subsumed — `host-owned-visibility` explicitly deferred this here |
| `_selection_broadcast_suppress_until` | 🔶 Visibility — not ours | Already inert on the wire (a follower's selection asserts no visibility). Still guards local bookkeeping; blocked behind `fix-visibility-authority-bypass` |
| `_applied_clip_echo_guid` / `_applied_clip_echo_until` | 🔶 Visibility — not ours | Same |
| `_local_view_action_until` | 🔶 Visibility — not ours | Same |
| `_rv_updating` | ⚠️ Still needed | Apply-scope reentrancy guard — filters echo events *before* the ownership check (see Echo filtering) |
| `_reload_suppress_until` | ⚠️ Still needed | The bookmark re-trigger burst manifests on the *annotation* broadcast path, which deliberately has no ownership channel — structure ownership cannot gate it. Needs an apply-scope guard around remote structural applies instead of a wall-clock window |
| `_is_syncing` | ⚠️ Still needed | Fundamental: snapshot apply guard |
| `source_guid` filter | ⚠️ Still needed | Fundamental: self-message discard |
| `_ignore_annotations_until` | ❌ Annotations stay multi-writer | |
| `_our_bookmark_uuids` | ❌ Annotations stay multi-writer | |

**8 guards** are candidates for the position/structure leases — down from the original "9 of 15", and against a larger denominator. That is not a worse outcome; the original number counted visibility guards this change no longer owns, and counted `_last_remote_stop_at`, which never existed as anything but dead code. The pattern in the survivors is unchanged: ownership retires the *asynchronous time-window* guards, while the cheap synchronous *apply-scope* guards remain, because they serve a different purpose — filtering echo events before they reach the broadcast path at all.

## Protocol additions

- **`CLAIM_OWNERSHIP`**: `{ category: "position"|"structure", peer_guid: "...", claim_ts: <monotonic-ish wall clock> }` — `claim_ts` plus `peer_guid` drive the deterministic tiebreak for concurrent claims
- **`RELEASE_OWNERSHIP`**: `{ category: "position"|"structure", peer_guid: "..." }` (explicit release; timeout is the implicit fallback)
- **`STATE_SNAPSHOT`** gains a `broadcast_ownership` section — current owner GUID and remaining lease time per category — so a late joiner converges on the session's ownership view instead of assuming both categories are FREE and immediately claiming. It joins `host_guid`, already carried there, and **follows the same backwards-compatibility convention `adopt_host()` established**: omit the section when unset, and ignore a `None` on receipt, so a peer predating the field cannot clear a locally-held lease.
- **No new peer-announcement message.** `PEER_ANNOUNCE` and the `SyncManager._peers` table already exist (announce on join, answer once, no storm).
- Ownership state tracked locally by each peer — no central authority needed. Every peer applies the same claim/release messages, resolves conflicts with the same tiebreak rule, and converges on the same view of who owns what.

## Enforcement strategy

Send-side only — no receive-side validation; peers trust each other. **This choke point already exists and is proven**: `host-owned-visibility` put the visibility check inside `SyncManager.broadcast_*`, and the live soak recorded OpenRV stripping visibility 284 times and sending `view_mode` zero times. This change adds a second check beside it, in the same place.

The argument for centralising has been re-confirmed rather than merely asserted. `host-owned-visibility` §1.4 verified that *no plugin gates a broadcast on the role*, and guards it with `test_no_plugin_gates_a_broadcast_on_being_host`. Where the two plugins genuinely need to know — their *local intent* branches — they go through one shared core predicate, `SyncManager.owns_visibility()`, rather than each application deciding. The lease must follow that shape exactly: **a lease is not something a plugin tests.**

```python
# In SyncManager — beside the existing _enforce_visibility call:
def broadcast_playback_state(self, state_dict, ...):
    inner, status = self._enforce_visibility(inner)       # landed: strips view_mode/clip_guid
    inner, status = self._enforce_position(inner, status) # this change: strips position fields
    # ... existing send path ...

def _enforce_position(self, state, status):
    if self._owns_category(authority.POSITION):
        self._refresh_lease(authority.POSITION)
        return state, status
    # No auto-claim here — claiming is input-driven only; see Echo filtering.
    return authority.strip_position_fields(state), authority.SUPPRESSED
```

Two details inherited from the landed code rather than designed here: the status vocabulary is `authority.SENT` / `authority.SUPPRESSED`, and `SUPPRESSED` already means *"sent, but with fields stripped"* rather than *"not sent"* — a mixed playback message that loses one field group but keeps another is the normal case, not an edge case. `broadcast_add_annotation` remains the one method that returns a clip GUID instead of a status, and annotation is never gated, so it is unaffected.

### Echo filtering must happen *before* the ownership check

On a non-owning peer, applying a remote message still fires local host events (RV's `frame-changed`, xStudio's async `attribute_changed`). If those echo events are allowed to reach the broadcast guard, every remote apply queues a claim request from the echoing peer — and when the owner's lease next expires, ownership transfers to that peer, which then broadcasts a stale frame back at the original owner. That is the same feedback loop, just running at lease-expiry speed instead of per-message. Two rules follow:

1. **Apply-scope reentrancy guards are fundamental, not replaced.** `_is_syncing` and RV's `_rv_updating` (and an equivalent around remote structural applies, replacing `_reload_suppress_until`'s wall-clock window) stop echo events from entering the broadcast path at all. Ownership retires the asynchronous *time-window* guards; the synchronous *apply-scope* guards stay.
2. **A suppressed broadcast does not queue its payload.** When ownership is later granted, the peer broadcasts its *current* state if the user is still interacting, or nothing at all. Replaying deferred stale state on lease transfer is itself an echo source.

## Capabilities

### New Capabilities
- `broadcast-ownership`: The write-lease mechanism — ownership over the **position** and **structure** categories (visibility is already single-writer under `session-visibility-authority` and needs no lease), claim/release protocol messages with deterministic tiebreak, lease lifecycle, contention resolution, ownership state in `STATE_SNAPSHOT`, and the lease check added beside the existing visibility check in `SyncManager.broadcast_*`.

### Modified Capabilities
- `session-visibility-authority`: the categories it established gain a lease for position and structure, leaving its visibility rule as a static policy underneath the same mechanism; its `SENT`/`SUPPRESSED` contract is extended to position field-group stripping.
- `otio-sync-core`: ownership tracking per category, claim/release message handling with deterministic tiebreak, the lease check inside `broadcast_*`, ownership state in `STATE_SNAPSHOT`.
- `openrv-sync-plugin`: remove asynchronous time-window echo suppression (enforcement lives in core); retain apply-scope reentrancy guards; `_rv_updating` becomes a depth-counted context manager.
- `xstudio-plugin-module-structure`: remove asynchronous time-window echo suppression (enforcement lives in core); retain apply-scope reentrancy guards, including a new one around remote structural applies to replace `_reload_suppress_until`.
- `protocol-message-docs`: `CLAIM_OWNERSHIP` and `RELEASE_OWNERSHIP` messages, `broadcast_ownership` section in `STATE_SNAPSHOT`.

## Impact

- **Core**: `SyncManager` gains ownership tracking (position + structure, current owner GUID, lease expiry, deterministic claim tiebreak) and a lease check inside `broadcast_*`, beside the landed `_enforce_visibility`. Two new protocol messages (`CLAIM_OWNERSHIP`, `RELEASE_OWNERSHIP`); `STATE_SNAPSHOT` gains a `broadcast_ownership` section beside the existing `host_guid`. `authority.py` gains `strip_position_fields` as the counterpart to `strip_visibility_fields`.
- **Plugins**: No per-plugin guard clauses — plugins observe the suppressed/sent result from `broadcast_*` and keep their apply-scope reentrancy guards (which filter echo events before the broadcast path). **8 asynchronous time-window suppression mechanisms become candidates for removal** once ownership is in place — but see the deletion blocker above. This is a net reduction in code complexity.
- **Packaging**: any new core module must be added to `rvplugin/ori_sync/makepackage.csh`'s hand-maintained vendoring list. `host-owned-visibility` §6a.1 shipped without `authority.py` in that list, and because `__init__.py` imports inside `try/except ImportError`, the RV plugin did not fail loudly — it stayed connected and inert. Prefer extending `authority.py` over adding a module; if a module is added, update the list in the same commit.
- **Test suite**: no existing case has two peers driving the same category at once, which is why the position guards have no positive evidence either way. A deliberately contended case is a prerequisite for the deletion step, not an optional extra.
- **Protocol**: Fully backwards compatible — peers that don't understand ownership messages ignore them and behave as today.
- **Risk**: Low for the mechanism, which is additive and changes no receive path. **Not low for the deletion step** — that is where the comparable change went wrong, and it is gated on `fix-visibility-authority-bypass`.

### Settled 2026-08-07

- **`DISPLAY_SET` takes a lease** — on its own channel, not position's. Three channels: `position`, `display`, `structure`. See design.md D8.
- **Lease duration envelope: 500 ms – 2 s.** Working defaults 0.5 s display / 1.0 s position / 2.0 s structure; exact values tuned during the 1b soak.
- **Destructive annotation operations stay unleased** for now. `clear-all-paint` from any peer wipes everyone's paint, and that remains true — an accepted gap, not an oversight. It is a scope increase on a change already carrying a blocked deletion step, and a clear is arguably more naturally a *driver-only* action than a lease-holder-only one, which would make it `session-roles`' to own.
- **Suppression is silent.** No "peer X is driving" indicator in this change. It is wanted, but deferred to after `session-roles`, whose 2b step already builds the peer/role UI it belongs beside — building it here would create a second surface to reconcile later. The lease API should leave room for it, the way `elect_host` left room for a future `claim_host()`.

### Open considerations (not blocking)

- **Multiple concurrent drivers**: the lease resolves contention between any number of peers with permission to broadcast. `session-roles` layers permissions on top and reduces how often contention arises, but never removes it — reviewers scrub too.
