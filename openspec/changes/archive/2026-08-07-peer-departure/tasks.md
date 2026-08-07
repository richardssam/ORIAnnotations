## 1. Receive side — act on departure before anything emits it

- [x] 1.1 Add `PeerDepart` to `python/otio_sync_core/protocol_messages.py` (`SCHEMA = "LiveSession.1"`, `EVENT = "PEER_DEPART"`, field `peer_guid`). No reason field — nothing consumes one, and adding it later is additive (design.md Open Questions).
- [x] 1.2 Register `("LiveSession.1", "PEER_DEPART"): self._h_peer_depart` in the dispatch table beside the existing `PEER_ANNOUNCE` entry.
- [x] 1.3 Implement `_h_peer_depart` as a call to the existing `drop_peer()`, resolving the GUID the way `_h_peer_announce` does (`msg.peer_guid or source`). Do **not** re-implement removal or election — `drop_peer` already pops and calls `elect_host()`, and it is on the poll thread here, so a direct call is right (same position `_h_peer_announce` occupies).
- [x] 1.4 Verify no call site assigns `_peers` directly. Single-writer is the poll thread; `fix-discovery-thread-safety`'s discipline applies unchanged and no locks are added.

## 2. Liveness — the backstop that covers crashes

- [x] 2.1 Add `last_seen` to peer-table entries, stamped in `_h_peer_announce`. Note the entry-equality check there (`if known != entry`) exists to keep the log quiet — a timestamp inside the compared dict would make every announcement look like a change, so keep `last_seen` out of that comparison.
- [x] 2.2 Emit a periodic `announce_peer()` on a 5 s cadence, requesting no answers. Not requesting answers is load-bearing — the storm the `PeerAnnounce` docstring warns about is the answer cascade (design.md D2). §3a.4 then removes the answering mechanism outright, so this cannot regress via a later edit reinstating the flag.
- [x] 2.3 Add the aging sweep to `tick()`, beside the existing master-failover and state-request checks, using the same `time.time()` delta style. Drop peers whose `last_seen` exceeds 15 s (three missed heartbeats) via `drop_peer()`.
- [x] 2.4 Leave `PeerAnnounce`'s docstring alone until §3a.5, which owns the rewrite. Editing it here and again there would mean two passes with different intents — this step's behaviour is only half the final story.
- [x] 2.5 Confirm a live-but-idle peer is never aged out. This is the trap the design rejected `last_seen`-on-any-traffic for: a viewer watching a screening emits nothing, and under `session-roles` might be the only driver.

## 3. Send side — make the common case prompt

- [x] 3.1 Emit `PEER_DEPART` from `SyncManager.close()` before `network.stop()`. **Core, not plugins** — RV's `disconnect_from_session` and xStudio's `disconnect` both already call `close()`, and duplicating the emission across two separately-written paths is where these hosts have drifted before (design.md D5).
- [x] 3.2 Give the publisher a chance to flush before stopping. Do **not** add a blocking wait on the UI thread: RV's disconnect runs on the Qt main thread and `network-send-robustness` is moving work off it. A lost departure degrades to §2's aging, which is the designed fallback.
- [x] 3.3 Confirm neither plugin needs an edit. If either does, the emission is in the wrong place.

## 3a. Roster in session state, then retire the cascade

**Order within this section matters**: carry the roster before deleting the cascade, so the join path is never left with neither (design.md D6).

- [x] 3a.1 Add a `peers` field to `StateSnapshot` beside `host_guid`, following the same convention — omitted from the payload when empty, ignored when absent on receipt, so a peer predating the field cannot blank a joiner's table.
- [x] 3a.2 Populate it in `send_state_snapshot` and `export_state` (the latter so the harness can assert peer-table convergence the way it already can on `is_host`/`host_guid`). Ship `{guid: {app, capabilities}}` — **not** `last_seen`: receipt time belongs to the receiver's clock, and a sender's timestamp on the wire would need skew handling for no benefit.
- [x] 3a.3 Adopt the roster in `apply_snapshot`, stamping `last_seen` locally on adoption exactly as `_h_peer_announce` does, then re-run host election.
- [x] 3a.4 Only then: delete `reply_requested` from `PeerAnnounce`, the `if msg.reply_requested` answer branch in `_h_peer_announce`, and the parameter on `announce_peer`. One field, one branch, one argument — confirmed as the only sites by grep.
- [x] 3a.5 Rewrite `PeerAnnounce`'s docstring. Both of its current claims become false: answers no longer exist, and periodic announcement — not answering — is now what makes a quiet peer discoverable. Do not leave the old rationale next to the new behaviour.

## 4. Tests

- [x] 4.1 Unit: receiving `PEER_DEPART` removes the peer and re-runs host election; a departing *host* hands authority to a remaining peer; a departing follower leaves the host unchanged.
- [x] 4.2 Unit: a peer whose `last_seen` ages past the timeout is dropped; an idle-but-announcing peer is not (§2.5).
- [x] 4.3 Unit: a peer dropped for inactivity is restored by its next announcement, and election re-runs against the restored table — the self-healing property in design.md D4.
- [x] 4.4 Unit: `close()` puts exactly one `PEER_DEPART` on the wire, carrying this peer's GUID.
- [x] 4.5 Assert master election is untouched: a departing non-master peer triggers no master failover (design.md Non-Goals — keeping master and host separate is the thing most at risk of eroding here).
- [x] 4.6 Suite: a two-app case where the host disconnects and the survivor takes over visibility. This is the bug being fixed and nothing currently covers it.
  - `host_departure_failover` (xstudio + openrv). Needed two new runner-level actions, `disconnect_peer` and `expect_host_failover`, plus `AppSpawner.terminate_app`. Runner-level for the same reason as `wait_for_media`: the subject is a peer that is going away, so it cannot report on the outcome, and the assertion is about what the *survivors* do.
  - The peer is **terminated, not asked to disconnect** — no departure notice is sent, so the survivors must age it out. That is the crash path, the one no message can shortcut, and the only one the suite can reach without a UI action.
- [x] 4.7 Unit: a joiner adopts the roster from session state and reaches the same peer table — and the same elected host — as the sender.
- [x] 4.8 Unit: joining emits no answering announcements from existing peers, and the message count on join does not grow with peer count. Assert on messages sent, not on intent.
- [x] 4.9 Unit: a joiner that receives **no** session state (master holds no timelines — `send_state_snapshot`'s early return) still learns every peer within one heartbeat interval. This is the case that makes retiring the cascade safe, so it needs a test rather than an argument.

## 5. Settle the timings

- [x] 5.1 Tune heartbeat/timeout against the two-host suite plus a deliberately killed peer. 5 s / 15 s is a starting point, not a measured one.
  - **Measured, kept.** `host_departure_failover` (2026-08-07): xStudio killed at 21:36:32.9, OpenRV logged `not heard from in 15s — presumed gone` at 21:36:46.9 and re-elected in the same tick. End-to-end failover **14.1 s**, against a 15 s timeout — i.e. detection dominates and the election itself is free.
  - **Zero false departures.** `xstudio_selects_script_xstudio` ran 30.4 s with both peers alive: 0 `presumed gone` on either side, while heartbeats flowed throughout. The 3-missed-heartbeat margin was not close to being spent.
  - Values unchanged. The one datum that would justify shortening the timeout — a user waiting ~15 s for the view to unfreeze — is real but must be weighed against dropping a live peer, and one clean run is not enough evidence to trade margin for latency.
- [x] 5.2 When judging false departures, sample free memory and swap alongside the run. This repo has already produced two false diagnoses from swap-induced latency imitating a timing race, and a stalled poll thread is exactly what this timeout misreads as death.
  - Sampled: **swap 0.00 M used, ~23.6 GB free+inactive** across both runs. So the zero-false-departure result above is from a *healthy* machine and says nothing about behaviour under memory pressure — which is precisely the condition that would produce them. Treat a departure storm during a slow run as a symptom of the machine until the swap figure says otherwise.
- [x] 5.3 Record the chosen values and their justification wherever they land, so the next person tuning them knows what evidence produced them.
  - `PEER_HEARTBEAT_INTERVAL` / `PEER_LIVENESS_TIMEOUT` in `manager.py` carry their rationale in the constant docstrings — why silence needs a heartbeat to be meaningful, and why the margin is deliberately generous (a stalled poll thread is alive but quiet). The measurements behind them are in §5.1 above.

## 6. Follow-through

- [x] 6.1 Regenerate the protocol reference and confirm `PEER_DEPART`, `PEER_ANNOUNCE`'s cadence, and the `STATE_SNAPSHOT` roster all appear — with the best-effort caveat on departure, and with no surviving reference to answering an announcement.
- [x] 6.2 Update `session-roles` design.md D7 — its open question "what makes a driver eligible" is answered once liveness exists: present in the peer table, which now means present.
- [x] 6.3 Check `drop_peer`'s docstring, which currently says nothing calls it. That sentence is the reason this change exists and must not survive it.
