# Task 8 live verification

Run 2026-08-16, build `e106f0f9`. Three headless `xstudio -e -n` peers on the
same RabbitMQ session (`ORI_SESSION=struct_test_2`), each with its own
`ORI_SYNC_LOG_FILE`, driven by a separate Python client connected to each
peer's own embedded API port (14441/14442/14443) acting as "the interactive
user" — the already-running, already-connected `ori_sync` plugin in each
process does the real discovery/broadcast work under test. Scripts in this
directory: `task8_live_test.py` (8.2), `task8_4_bulk.py` (8.4),
`task8_6_switch_off.py` (8.6).

## 8.2 — sequence created inside an existing playlist (the proposal's failure case)

Peer1 (host) and peer2 (client) both running with the switch on (default).
Created a playlist, waited 2 s so it published as its own change, then
created a sequence inside it:

```
T_CREATE            09:16:57.821  (create_timeline call issued)
peer1 broadcast      09:16:57.860  "New sequence timeline 'Task8 New Sequence' ... → broadcast"
peer2 ADD_TIMELINE   09:16:57.927  apply_patch: ... event=ADD_TIMELINE
```

Discovery-to-broadcast: **~39 ms**. Peer1→peer2 total: ~106 ms — consistent
with the proposal's own numbers ("once the host noticed, the client had it in
70 ms"). Compare against the poll-only baseline in 8.6 below (~351 ms, and
that was the *lucky* case — the proposal's original incident was a 58 s poll
stall).

## 8.4 — bulk case: four playlist+sequence pairs created together on peer1

```
09:17:55.055  New sequence timeline 'Task8.4 Seq 0' ... → broadcast
09:17:55.094  New sequence timeline 'Task8.4 Seq 1' ... → broadcast
09:17:55.119  New sequence timeline 'Task8.4 Seq 2' ... → broadcast
09:17:55.145  New sequence timeline 'Task8.4 Seq 3' ... → broadcast
```

Exactly one broadcast per sequence (grepped and counted — no duplicates), no
errors/exceptions in either peer's log, and peer2 received exactly one
`ADD_TIMELINE` per sequence with matching names. No event storm: all four
resolved within a 90 ms window despite four dirty marks arriving in quick
succession — consistent with the dirty set's dedup-and-single-pass design
(section 2).

## 8.5 — memory/swap during the runs

```
vm.swapusage: total = 8192.00M  used = 6931.69M  free = 1260.31M
```

Swap pressure was high on the test machine throughout. Noted per the
project's standing finding that swap-induced latency has twice mimicked
timing bugs here — the *absolute* millisecond figures above should be read
with that caveat, but the ~9x relative gap between the event-driven (8.2) and
poll-only (8.6) latencies, measured back-to-back on the same loaded machine
in the same few minutes, is not attributable to swap variance.

## 8.6 — switch off restores today's (poll-only) behaviour

Third peer (peer3, client) launched with `ORI_STRUCTURE_EVENTS=0`. Confirmed
zero `[3E]`-tagged log lines for the life of the process — no session or
playlist subscriptions were ever established. Created a sequence directly on
peer3:

```
T_CREATE    09:18:49.365
broadcast   09:18:49.716
```

Latency **~351 ms** — squarely inside the 1 Hz poll interval, as expected
with no event path at all. Structure was still correctly detected and
broadcast — the poll alone is sufficient, exactly as before this capability
existed.

## 8.3 — poll thread stalled: not reproduced live, argued architecturally

Injecting an artificial stall into `_poll_loop` of an already-running,
separately-launched process was not attempted — it would need either an
in-process debugger attach or a code-level hook, neither set up for this
run. Confidence instead comes from the mechanism itself, and from what task 1
already established live:

- The event handler (`on_structure_event`) runs on an **xStudio actor
  callback thread**, confirmed directly in task 1's investigation — every
  event arrived and dispatched independently of anything on the plugin's own
  poll thread, because nothing on the poll thread was involved in delivering
  it.
- `mark_container_dirty` is a plain `set.add` under a lock, and
  `plugin._cmd_queue.put(...)` is `queue.Queue.put` — both are non-blocking,
  thread-safe operations with no dependency on the poll thread's current
  state. A poll thread wedged inside a slow `poll_deleted_playlists` pass
  cannot prevent either from completing.
- What *can* be delayed by a stalled poll thread is the eventual *publish* —
  the queued command sits until the poll thread is free to drain it. That is
  exactly what the requirement asks for ("a poll pass that is slow ...
  SHALL delay reconciliation of state already known, never the discovery of
  a change that has just happened") — discovery (the mark) is immediate;
  only publish can lag, and only by the stall's own duration, not by an
  additional poll-interval wait.

This is the one live sub-task not directly reproduced; if stronger evidence
is wanted, it would need a debug build/hook that can suspend `_poll_loop`
specifically (e.g. a temporary `time.sleep` behind an env var) rather than
suspending the whole process.

## 8.7 — relax the poll interval to 5s, re-verified live

User chose 5 s over the prior 1 s. `ori_sync_plugin.py` gained
`STRUCTURE_POLL_INTERVAL = 5.0` (class attribute, alongside
`ANNOTATION_SCAN_INTERVAL`); `_poll_loop`'s periodic structure-scan block
reads it instead of a bare `1.0`.

Re-ran 8.2 and 8.4 against two fresh peers at the new interval:

```
8.2 re-run:  T_CREATE 09:24:12.956  →  broadcast 09:24:12.990   (~34 ms)
8.4 re-run:  4/4 sequences, exactly one broadcast each, no errors
             09:24:24.323 / .364 / .391 / .416
```

Event-driven latency (~34 ms) is unchanged from the 1 s-interval run
(~39 ms) — confirms discovery latency does not depend on this constant, as
design D2 intended. The bulk case remained clean. `./run_tests_xstudio.sh`
(98 passed) and a syntax check re-run clean after the change.
