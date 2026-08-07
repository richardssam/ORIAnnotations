## Context

`SyncManager._peers` (`{guid: {app, capabilities}}`) is fed by `PEER_ANNOUNCE` and read by `authority.elect_host_guid`. Entries are added in `_h_peer_announce` and removed only by `drop_peer()`, which nothing calls.

Relevant current state:

- **`drop_peer()` is already written and already correct** — it pops the entry and calls `elect_host()`. It was added deliberately "so that when a departure signal is added, host failover is one call rather than a second election implementation".
- **`PEER_ANNOUNCE` has no cadence by design.** Sent from `start_session()` with `reply_requested=True`, and answered once with `reply_requested=False`. The docstring is explicit that this is anti-storm.
- **`STATE_SNAPSHOT` already carries authority state** — `host_guid`, added by `host-owned-visibility`, omitted when unset and ignored as `None` on receipt. `send_state_snapshot` returns early when the master holds no timelines.
- **`tick()` already runs timeout checks on the poll thread**, using `time.time()` deltas: master failover at 2.0 s, state-request timeout at 5.0 s. It is the established place for this kind of check.
- **Host election is single-writer**: `elect_host()` owns the transition, other threads enqueue via `request_host_election()`, `_drain_host_elections()` re-checks at drain time (`fix-discovery-thread-safety`'s discipline).
- **Master failover is separate and works** — `_last_who_is_master_time` plus a 2.0 s threshold, driven by request/response rather than by peer liveness.
- `close()` calls `network.stop()` directly, with no flush step.

## Goals / Non-Goals

**Goals:**

- A departed peer leaves the peer table, promptly on a clean disconnect and eventually on any other kind.
- Host failover works when the host departs — the concrete bug this fixes.
- One transition (`drop_peer`) for both detection paths, so there is no second election implementation.
- Remove the one O(N) burst in the peer protocol (the join-time answer cascade), which heartbeats make redundant.
- Additive, backwards-compatible protocol.

**Non-Goals:**

- Changing master election or failover. It has its own working mechanism; conflating the two is how "master" and "host" get re-merged, which `host-owned-visibility` D2 spent effort separating.
- A presence/roster *product* feature. The roster exists for election correctness, not as a "who's online" UI surface.
- Master-aggregated peer tracking. Considered and rejected in D6 — it couples visibility authority to master liveness.
- Fixing `send_state_snapshot`'s empty-session early return. D6 depends on the heartbeat, not the snapshot, for discoverability.
- Reliable delivery guarantees for `PEER_DEPART`. It is an optimisation over aging, and aging is the correctness backstop.
- Detecting a *hung* peer that still heartbeats. Out of scope; a peer that talks is a peer that is here.

## Decisions

### D1: Two mechanisms, because neither covers the other's case

| Path | Detects | Latency | Covers |
|---|---|---|---|
| `PEER_DEPART` message | Clean disconnect, app quit | Immediate | The common case |
| Liveness aging | Crash, `kill -9`, network loss, broker partition | One timeout | Everything else |

Departure alone misses every unclean exit — and unclean is exactly when a frozen session is least explicable to the user. Aging alone works but makes every ordinary disconnect take a full timeout to propagate, which is a visible stall in the case that happens most.

- *Alternative — aging only:* rejected. Simpler, but it makes the common case slow for no benefit; the message is cheap and precise.
- *Alternative — departure only:* rejected. It is a best-effort message on an unreliable path, protecting a correctness property. A backstop is not optional.

### D2: Liveness needs a heartbeat, and `PEER_ANNOUNCE` becomes it

There is nothing to age against today: a peer announces on join and then may legitimately go silent forever. So aging requires a periodic signal, and the honest options were:

- *Piggyback `last_seen` on any message from a peer:* **rejected**, and this is the trap. It costs nothing, but a genuinely idle peer — a viewer watching a screening, touching nothing — emits nothing and would age out while present. Under `session-roles` that peer might be the only driver.
- *Heartbeat only from peers holding a role worth failing over:* rejected; conditional liveness is more state and more edge cases than it saves.
- *Periodic `PEER_ANNOUNCE`, answered by nobody (chosen)*: reuses the message that already carries exactly the peer-table payload, and needs no new schema.

**This does not reintroduce the storm the docstring warns about.** That storm is the answer cascade — an announcement provoking an answer from every peer. A periodic re-announce that nobody answers is O(N): at a 5 s cadence, a 30-peer session carries 6 messages/second total, against scrub traffic that runs at frame rate. D6 goes further and removes the answer mechanism outright, so the cascade cannot return by a later edit reinstating a flag.

Starting values: **heartbeat 5 s, timeout 15 s** (three missed announcements). The 3× ratio is conventional and the absolute values sit in the same range as `tick()`'s existing 2 s / 5 s thresholds.

### D3: Both paths call `drop_peer()`; nothing else mutates the table

`drop_peer()` already pops and re-elects. `_h_peer_depart` calls it (already on the poll thread, inside `tick()` — the same position `_h_peer_announce` occupies when it calls `elect_host()` directly). The aging sweep calls it from `tick()`, beside the existing timeout checks.

Single-writer is preserved: the poll thread is the only mutator, exactly as `fix-discovery-thread-safety` requires and as `elect_host` already assumes. No locks.

### D4: A late `PEER_ANNOUNCE` must not resurrect a departed peer — and does not

The ordering worth checking: peer A sends `PEER_ANNOUNCE`, then `PEER_DEPART`; if they were reordered, A would be re-added after being dropped and stay forever.

They cannot reorder. Each peer publishes through a single publisher thread onto a fanout exchange, so per-sender order is preserved, and A emits nothing after `PEER_DEPART`. Stated here rather than assumed, because the property is load-bearing and lives in the network layer, not this code.

The other direction is a *feature*: if a live peer is wrongly aged out — say its poll thread stalled past the timeout — its next heartbeat re-adds it and election re-runs. The mechanism self-heals rather than requiring a rejoin.

### D5: Departure is emitted from core, not from each plugin

`PEER_DEPART` is sent by `SyncManager.close()`. RV's `disconnect_from_session` and xStudio's `disconnect` both already call it, so both get the behaviour with no plugin edit.

This is the same reasoning `host-owned-visibility` used for its enforcement point, and it applies with particular force here: the two disconnect paths are separately written, and hand-replicated protocol behaviour between these two plugins has already drifted (discovery re-broadcast cadence, snapshot assembly placement). A departure emitted from two places would drift the same way, and the failure would be silent — one host announcing its exit and the other not.

**Flush hazard.** `close()` currently calls `network.stop()` immediately. The departure must be handed to the publisher and given the chance to drain first. `network-send-robustness` documents that the in-flight message on a failed publish is currently dropped; if that change lands first this gets more reliable, and if it does not, a lost `PEER_DEPART` degrades to aging — the designed fallback, not a failure. Do not add a blocking wait on the UI thread to make it certain: RV's disconnect already runs on the Qt main thread, and that same change is moving work *off* it.

### D6: The roster travels in `STATE_SNAPSHOT`, and the answer cascade is retired

A joiner today learns the peer set from an **answer cascade**: it announces with `reply_requested=True` and every peer answers once. That is the only O(N) burst in the peer protocol, and `PeerAnnounce`'s docstring is explicit about why it exists — it "lets a late joiner discover peers that have long since gone quiet".

D2 removes that justification. Once every peer re-announces on a cadence, a joiner learns quiet peers within one interval whether or not anyone answers it. So the cascade becomes redundant work, and this change should not leave it behind.

`STATE_SNAPSHOT` gains a `peers` field beside the `host_guid` it already carries. A joiner already sends `STATE_REQUEST` and already receives a snapshot; the roster rides along, and `reply_requested` is deleted — one field on the message, one branch in `_h_peer_announce`, one argument on `announce_peer`.

| | Today | With D6 |
|---|---|---|
| Join into N peers | 1 announce + **N answers** | 1 announce |
| New message types | — | none |

**The two halves of this change are mutually enabling, and that is the reason they belong together.** Heartbeats make the cascade removable; the cascade's removal is what stops this change from adding periodic traffic *on top of* an O(N) burst it left in place. Neither half is safe alone: retiring the cascade without heartbeats would leave a joiner permanently ignorant of quiet peers.

- *Alternative — full master aggregation (master owns the table, broadcasts a roster):* **rejected.** It does not reduce steady-state traffic: the master still needs a heartbeat from every peer to judge liveness, so the roster is one message *on top of* N, not instead of them. It couples visibility authority to master liveness, which `host-owned-visibility` D2 rejected on the grounds that a master re-election would then silently change who controls the view — and this change exists to *stop* an absent peer freezing visibility, so reintroducing that shape through the master would be self-defeating. It also converts an incremental, order-insensitive protocol into versioned state: "add this peer" is idempotent, whereas a whole roster arriving out of order silently resurrects a departed peer, which is the message class this codebase has been bitten by before (snapshot-vs-delta buffering, latest-wins coalescing).
- *Alternative — keep the cascade as well:* rejected; it is pure redundancy once heartbeats exist, and the join burst is the cost this decision exists to remove.

**The empty-session gap, and why it is acceptable.** `send_state_snapshot` returns early when the master holds no timelines, so a joiner into a session with none gets no snapshot and therefore no roster. It is not stranded: its own announcement still reaches everyone, and it learns them from the next heartbeat round — bounded by the heartbeat interval rather than unbounded. Fixing the early return is out of scope here; the roster must not be the only thing that makes a peer discoverable, which the heartbeat guarantees.

**Roster contents.** Ship the same `{guid: {app, capabilities}}` shape the table already holds, minus `last_seen` — receipt time is the receiver's own clock, and a sender's timestamp crossing the wire would need clock-skew handling for no benefit. The receiver stamps `last_seen` on adoption, exactly as it does on an announcement. `export_state` should carry the roster too, so the test harness can assert on peer-table convergence the way it already can on `is_host`/`host_guid`.

## Risks / Trade-offs

- **[A stalled peer is declared dead]** A machine under memory pressure can stall its poll thread past 15 s and be dropped while alive, triggering a needless host re-election. → Self-healing by D4's re-announce, and the 3-missed-heartbeat margin is deliberately generous. Note this repo has already been bitten by swap-induced latency imitating a timing race, so treat a departure storm during a slow run as a symptom of the machine, not of the protocol — sample free memory alongside.
- **[Heartbeat traffic in a large session]** N messages per interval, forever, including in sessions that never idle. → O(N), not O(N²); 6 msg/s at 30 peers is negligible next to frame-rate scrub traffic. Revisit only if a session size makes it measurable.
- **[Timeout too short for a slow structural apply]** A peer inside a multi-second `load_otio` may not service its poll loop. → 15 s exceeds observed rebuild times, and the heartbeat is emitted from `tick()`, so this is really the "stalled peer" risk above with the same mitigation.
- **[Departure makes host churn visible]** Today a departed host silently keeps the role; afterwards, election actually moves and peers change behaviour mid-session. That is the point, but it is a behaviour change in already-shipped code. → It only fires where the alternative is a frozen view. Log every departure-triggered election, as `elect_host` already logs elections.

## Migration Plan

1. `PeerDepart` message type + `last_seen` on peer-table entries + `_h_peer_depart` → `drop_peer()`. No emission yet; a peer that receives one acts on it.
2. Periodic `announce_peer()` and the aging sweep in `tick()`. Aging is now live, so departure works end-to-end even before anything emits the message.
3. `close()` emits `PEER_DEPART` before stopping the network, with the flush ordering from D5.
4. Roster in `STATE_SNAPSHOT` and `export_state`; then retire `reply_requested` (D6). **Order matters within this step** — carry the roster before deleting the cascade, so the join path is never left with neither.

Each step is independently useful and independently revertible. Steps 1–2 alone fix host failover; step 3 only makes the common case prompt; step 4 is a traffic reduction that changes no authority behaviour. Step 4 depends on step 2 having landed, per D6's mutual-enablement note.

## Open Questions

- Final heartbeat/timeout values. 5 s / 15 s is a starting point, not a measured one; the two-host suite plus a deliberately killed peer is the way to settle it.
- Should a departure that changes the host surface anything to the user, or only to the log? The log is enough for this change; `session-roles` 2b is where session-membership UI belongs, and this should not grow a second surface ahead of it.
- Does `PEER_DEPART` warrant a reason field (quit / kicked / error)? Nothing consumes one today. Leaving it out; adding a field later is additive.
