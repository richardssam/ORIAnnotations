## Why

An investigation into suspected RV main-thread network blocking found the opposite of the assumption: `RabbitMQNetwork` already runs dedicated consumer and publisher threads, and `send_payload` is a non-blocking queue put (the stale xStudio docstring claiming otherwise has been corrected directly). But the investigation surfaced three real defects in the send path that will bite hardest in the large sessions `session-roles` Phase 2 targets:

1. **Eager debug serialization on the caller's thread**: `send_payload` and the consumer callback build `json.dumps(payload, indent=2)` log strings unconditionally — the f-string is evaluated before `_log` decides to discard it. Every scrubbed frame pays a full pretty-print of its payload on the host's main thread (twice, counting the wire encode), in both hosts, even with logging off.
2. **Unbounded send queue with a stale flood on reconnect**: if the broker drops, `_send_queue` accumulates without limit (a minute of scrubbing = hundreds of frame messages); on reconnect the publisher replays the entire backlog, driving every peer through stale history. Additionally, the message in flight when a publish fails is silently lost — one message dropped per disconnect, which for a structural message means divergent peers.
3. **Bounded UI freezes in RV's connect/disconnect path**: `wait_until_ready(timeout=5.0)` against an unreachable broker and `network.stop()`'s two 2-second thread joins run on RV's Qt main thread. xStudio already moved connection onto a worker thread; RV did not.

## What Changes

- `RabbitMQNetwork` logging becomes lazy: payload serialization for debug output happens only when the `otio_sync` logger is actually enabled for debug (guard or callable-deferred formatting). No wire-format change.
- The send queue gains a message-class-aware backlog policy applied on publisher reconnect (and optionally at enqueue time): **ephemeral** messages (playback, display — latest state wins) are coalesced to the most recent per class, while **durable** messages (structure, annotations, session/discovery) are preserved in order. The message in flight during a publish failure is re-queued, not dropped.
- RV's session connect and disconnect move off the Qt main thread onto a worker thread, mirroring xStudio's `_session_connect_worker` pattern (menu callbacks return immediately; completion/failure surfaces via the existing log/warning paths).
- No protocol changes: message formats, exchange topology, and handshake are untouched.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `otio-sync-core`: new requirements on the network backend — send-side debug logging SHALL NOT serialize payloads when debug logging is disabled; the publisher SHALL apply the ephemeral/durable backlog policy on reconnect; a failed publish SHALL NOT silently drop the in-flight message.
- `openrv-sync-plugin`: session connect/disconnect SHALL NOT block the RV UI thread beyond menu-dialog interaction; broker-unreachable outcomes SHALL surface asynchronously.

## Impact

- **Code**: `python/otio_sync_core/rabbitmq_network.py` (lazy logging, queue policy, in-flight re-queue), `rvplugin/ori_sync/plugin.py` (worker-thread connect/disconnect). Both hosts benefit from the core changes with no plugin edits.
- **Protocol**: none.
- **Interaction with other changes**: independent of the encapsulation changes and `fix-discovery-thread-safety`. Complements `session-roles` — the ephemeral/durable classification here should use the same message-category table Phase 1 introduces (navigation ≈ ephemeral, structure ≈ durable), so whichever lands second reuses the first's table rather than defining a parallel one.
- **Testing**: existing two-client `sync_test/` suite unchanged; new coverage for broker-outage behavior (kill/restart broker mid-scrub: peers must converge on the latest state without replaying the backlog, and no structural message may be lost). RV verification requires `reinstall.csh` before in-app testing.
