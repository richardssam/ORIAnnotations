## Why

`SyncManager.drop_peer()` exists, forgets a peer and re-runs host election — and **nothing calls it.** Its own docstring says why:

> The protocol has no departure message today, so nothing calls this on a remote peer disconnecting — a departed host therefore keeps the role until it (or a preferred peer) announces again.

So `_peers` is append-only in practice. A peer that quits, crashes, or loses the network stays in the table forever, and two things follow from that:

**1. A live hole in host failover.** Host election reads `_peers`. When the host quits, its entry survives, so it stays elected — and because only the host may broadcast visibility, **the session's view freezes with no peer able to change it and nothing reporting why.** That is shipped behaviour today, knowingly deferred by `host-owned-visibility` when it introduced the host role.

**2. `session-roles` D7 is unimplementable.** Its "Become controller" escape hatch is gated on *no eligible driver in the peer table*, a condition that can never become true once a driver has announced. The escape hatch would be greyed out exactly when it is needed.

No other change owns this: `network-send-robustness` states "No protocol changes", and the `ori-session-management` spec has no requirement mentioning departure, disconnection, or liveness.

## What Changes

- A peer SHALL signal its departure on clean disconnect, via a new `PEER_DEPART` message sent from **core** (`SyncManager.close()`), not from each plugin's separately-written disconnect path.
- A peer that stops being heard from SHALL be aged out, so departures nobody announced — crash, `kill -9`, network loss — are still detected.
- `PEER_ANNOUNCE` gains a periodic re-announcement so there is something to age against. Today it is sent on join and as a one-shot answer, with no cadence at all.
- Both paths SHALL converge on the existing `drop_peer()` operation, which already owns the transition and re-elects the host.
- `STATE_SNAPSHOT` SHALL carry the **peer roster** alongside the `host_guid` it already carries, so a joiner learns the peer set from the message it already requests — and the `reply_requested` answer cascade is **retired**, removing the one O(N) burst in the peer protocol.
- Departure SHALL NOT be conflated with master failover, which has its own working mechanism (`WHO_IS_MASTER` timeout) and is left alone.

### Why the roster belongs here rather than in a separate change

Adding heartbeats and retiring the cascade are the same decision seen from two sides. The cascade exists *because* there was no other way for a joiner to learn peers that had long since gone quiet — that is exactly what `PeerAnnounce`'s docstring says it is for. Once every peer re-announces on a cadence, a joiner learns quiet peers anyway, and the cascade is redundant work that this change would otherwise leave behind.

The alternative considered and rejected was **full master aggregation**: peers announce, the master alone maintains the table and broadcasts a roster. It does not reduce steady-state traffic (the master still needs a heartbeat from every peer to know liveness, so the roster is one message *on top* of N), and it would couple visibility authority to master liveness — precisely what `host-owned-visibility` D2 rejected, because a master re-election would then silently change who controls the view. It also converts an incremental, order-insensitive protocol ("add this peer") into versioned state, where a stale roster silently resurrects a departed peer.

Carrying the roster in the snapshot captures the join-time saving without either cost.

## Capabilities

### Modified Capabilities

- `otio-sync-core`: peer departure signalling, peer liveness and aging, `drop_peer` as the single departure transition for both paths, and the peer roster in `STATE_SNAPSHOT` replacing the join-time answer cascade.
- `session-visibility-authority`: a departed host no longer holds visibility authority indefinitely — host election re-runs on departure, closing the frozen-view failure above.
- `protocol-message-docs`: the `PEER_DEPART` message, `PEER_ANNOUNCE`'s new periodic cadence and retired `reply_requested` field, and the `STATE_SNAPSHOT` roster field.

## Impact

- `python/otio_sync_core/protocol_messages.py`: new `PeerDepart`; `PeerAnnounce` loses `reply_requested` and its "no cadence" rationale, both of which this change revises; `StateSnapshot` gains a `peers` field beside `host_guid`.
- `python/otio_sync_core/manager.py`: `_h_peer_depart` handler; `last_seen` on peer-table entries; a periodic `announce_peer()`; an aging check in `tick()` beside the existing master-failover and state-request timeouts; `close()` sends `PEER_DEPART` before stopping the network; `send_state_snapshot`/`export_state`/`apply_snapshot` carry and adopt the roster.
- **Both plugins: no changes.** Departure is emitted from `SyncManager.close()`, which RV's `disconnect_from_session` and xStudio's `disconnect` both already call. Putting it there is deliberate — the two disconnect paths are separately written and are exactly where hand-replicated protocol behaviour has drifted before.
- **Protocol**: additive and backwards compatible. A peer that does not understand `PEER_DEPART` ignores it and ages the departed peer out instead; a peer that never re-announces is aged out by everyone else, which is the correct outcome for a peer running code this old.
- **Ordering hazard**: `close()` must let the publisher flush `PEER_DEPART` before `network.stop()`. `network-send-robustness` documents that a failed publish currently drops the in-flight message — if that lands first, this benefits; if not, a lost departure degrades to aging, which is the designed fallback rather than a failure.
- **Snapshot gap**: `send_state_snapshot` returns early when the master holds no timelines, so a joiner into an empty session receives no snapshot and therefore no roster. It still learns every peer from the next heartbeat round. This is the designed fallback, and it is why retiring the cascade is safe *only* alongside the heartbeat — see design.md D6.

### Unblocks

- `session-roles` D7 — the driverless-session gate becomes computable.
- Host failover generally, which is why this is worth doing on its own merits rather than as a `session-roles` prerequisite.
