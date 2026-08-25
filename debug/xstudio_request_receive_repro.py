#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Minimal A/B repro: does synchronous ``request_receive`` time out under
``xstudio/pr/python-per-subscription-listeners``?

Standalone — imports nothing from the ORI sync plugin, so a result here is a
statement about xStudio's Python API alone.

Background
----------
Measured 2026-08-25 with identical plugin code and scenario, only the xStudio
build differing, `xstudio_selects_script_xstudio` went 9/10 pass to 0/10 with
`RuntimeError: Dequeue timeout` counts of 0 (develop) vs 12-13 (PR). The failing
call was `ModuleAttribute.name` inside `ModuleBase.__init__` — a plain
request/response with no event group involved.

Findings so far (PR build, 2026-08-25)
--------------------------------------
Serial use is clean; **concurrency is the trigger**::

    Phase 1  serial, bare request_receive   0.0002s   0 timeouts
    Phase 2  serial, Playhead ctor          0.0057s   0 timeouts
    Phase 3  40 wrappers held (listeners)   0.0046s   0 timeouts  (flat)
    Phase 4  4 threads constructing         p50 0.015s BUT
                                            mean 15.8s, max 100.05s, 2 timeouts

`max=100.05s` is exactly `Connection.default_timeout_ms` (100 s): a response
did not arrive until the full timeout elapsed. So a synchronous request issued
while another thread has one in flight on the same `Connection` can stall for
the entire timeout.

This rules out the two earlier hypotheses: request/response is not broken
(phase 1), and listener actors do not accumulate harmfully (phase 3, flat).

A/B RESULT (2026-08-25) — phase 4, 4 threads x 10 constructions
---------------------------------------------------------------
    build     mean      p50        max      timeouts   wall
    PR        15.81s    0.0147s    100.05s   2         200.2s
    develop    5.28s    0.0142s    200.01s   2         200.2s

**Both builds stall. The contention is PRE-EXISTING, not introduced by the PR.**
develop is if anything worse on max (200s = two stacked 100s timeouts). Serial
use is clean on both; only concurrency breaks.

Conclusion: the xStudio Python ``Connection`` is not thread-safe for concurrent
``request_receive``, and never was. The sync plugin issues requests from its
poll thread *and* from xStudio callback threads, which its own threading
invariant already forbids ("event handlers SHALL only mutate cheap local state
or enqueue onto ``_cmd_queue``"). The PR does not cause this; it changes
dispatch timing enough to turn an intermittent collision into a reliable one
(suite 9/10 -> 0/10).

The fix is plugin-side: get every ``request_receive`` onto one thread.

Phase 6 (2026-08-25, develop): 5 subscriptions on one group, 45 callback
invocations, **1 distinct thread, peak overlap 1 — SERIALISED**.

That is the answer to "if the defect is pre-existing, why is the PR worse?".
On develop every subscription on a connection shares ONE listener actor, so
Python callbacks cannot overlap each other. Concurrency is therefore only
2-way: the poll thread versus that single callback thread. Per-subscription
listeners remove that accidental serialisation, so callbacks can also overlap
EACH OTHER — N-way concurrency against a connection that tolerates none.

So the PR does not introduce the defect, but it does legitimately make it far
more likely to fire. Both statements are true; "exonerated" is too strong.

Phase 6 A/B (2026-08-25) — 5 subscriptions on one group
--------------------------------------------------------
    build     invocations   threads   peak overlap
    develop   45            1         1   SERIALISED
    PR        80            5         5   CONCURRENT

Confirmed. One shared listener serialises Python callbacks; per-subscription
listeners let all five run at once. So concurrency against the connection goes
from 2-way on develop (poll thread vs the single callback thread) to N-way
under the PR. The defect is pre-existing but the PR makes it far more likely to
fire — 9/10 -> 0/10 on the suite.

Usage
-----
Against an already-running xStudio (find the port in its log, "API enabled on
host:PORT")::

    <xstudio-python> debug/xstudio_request_receive_repro.py --port 14441

Or let it launch its own instance and clean up afterwards::

    <xstudio-python> debug/xstudio_request_receive_repro.py --launch \\
        --xstudio-bin /Users/sam/git/xstudio/build/xSTUDIO.app/Contents/MacOS/xstudio.bin

`<xstudio-python>` is xStudio's bundled interpreter, e.g.
``/Users/sam/git/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3``.

Run it once per build and compare.

**Identifying the build — do not trust the source tree.** A PR-built binary
under a develop checkout (and the reverse) has happened repeatedly; `git branch`
and the source file both lie about what is actually running. Compare
`build/xSTUDIO.app/Contents/MacOS/xstudio.bin`'s mtime against when each build
was made.

The `parent_atom` warning signature (present on develop, absent under the PR) is
only valid for runs with the **ori_sync plugin loaded** — it is the plugin's
subscription pattern that provokes those warnings. This script loads no plugin,
so it always reports zero and the signature must NOT be used here.

Exit status is 0 whatever the timings — this measures, it does not assert.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time


def _percentile(values, pct):
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def _summarise(label, timings, timeouts, extra=""):
    if timings:
        print(
            f"  {label:<34} n={len(timings):<4} "
            f"mean={sum(timings) / len(timings):7.4f}s "
            f"p50={_percentile(timings, 50):7.4f}s "
            f"max={max(timings):7.4f}s  timeouts={timeouts}{extra}"
        )
    else:
        print(f"  {label:<34} n=0  (all {timeouts} attempts failed){extra}")


def _launch_xstudio(xstudio_bin):
    """Start an isolated xStudio and return (process, api_port, log_path)."""
    log_path = os.path.join(tempfile.mkdtemp(prefix="rr_repro_"), "xstudio.log")
    log_file = open(log_path, "w")
    # -n: never reuse an already-running instance, so the measurement is not
    # taken against whatever session happens to be open.
    proc = subprocess.Popen(
        [xstudio_bin, "-n"], stdout=log_file, stderr=subprocess.STDOUT
    )
    port = None
    deadline = time.time() + 20.0
    while time.time() < deadline and port is None:
        time.sleep(0.2)
        try:
            with open(log_path) as fh:
                m = re.search(r"API enabled on [^:]+:(\d+)", fh.read())
                if m:
                    port = int(m.group(1))
        except OSError:
            pass
    if port is None:
        proc.terminate()
        raise RuntimeError(f"xStudio did not report an API port; see {log_path}")
    # The port is logged before the API is actually serving.
    time.sleep(3.0)
    return proc, port, log_path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=14441,
                    help="xStudio API port (ignored with --launch)")
    ap.add_argument("--iterations", type=int, default=40)
    ap.add_argument("--launch", action="store_true",
                    help="launch an isolated xStudio and tear it down afterwards")
    ap.add_argument("--xstudio-bin",
                    default="/Users/sam/git/xstudio/build/xSTUDIO.app/Contents/MacOS/xstudio.bin")
    ap.add_argument("--hold", action="store_true",
                    help="run phase 3: keep wrapper references so listeners accumulate")
    ap.add_argument("--threads", type=int, default=1,
                    help="run phase 4: N threads constructing concurrently")
    ap.add_argument("--in-callback", action="store_true",
                    help="run phase 5: request_receive from inside an event callback")
    ap.add_argument("--concurrency-probe", action="store_true",
                    help="run phase 6: do callbacks on one connection overlap?")
    ap.add_argument("--subscriptions", type=int, default=5,
                    help="phase 6: how many callbacks to attach to one group")
    args = ap.parse_args()

    try:
        from xstudio.connection import Connection
        from xstudio.core import (get_global_playhead_events_atom,
                                  viewport_playhead_atom, active_viewport_atom,
                                  viewport_atom)
        from xstudio.api.session.playhead import Playhead
    except ImportError as exc:
        print(f"FATAL: run this with xStudio's bundled python — {exc}")
        return 2

    proc = log_path = None
    port = args.port
    if args.launch:
        print(f"Launching {args.xstudio_bin} ...")
        proc, port, log_path = _launch_xstudio(args.xstudio_bin)
        print(f"  API port {port}, log {log_path}")

    try:
        conn = Connection(auto_connect=False)
        conn.connect_remote("127.0.0.1", port)
        print(f"Connected on port {port}; {args.iterations} iterations per phase.\n")

        # Resolve a playhead actor once, cheaply, so phases 2/3 measure only
        # wrapper construction and not the lookup.
        gphev = conn.request_receive(conn.remote(), get_global_playhead_events_atom())[0]
        ph_actor = None
        try:
            vp = conn.request_receive(gphev, active_viewport_atom())[0]
            ph_actor = conn.request_receive(vp, viewport_playhead_atom())[0]
        except Exception:
            pass
        if not ph_actor:
            try:
                vps = conn.request_receive(gphev, viewport_atom())[0]
                if vps:
                    ph_actor = conn.request_receive(vps[0], viewport_playhead_atom())[0]
            except Exception:
                pass

        # ── Phase 1: bare request/response, no subscriptions ─────────────
        # Distinguishes "request_receive itself is broken" from anything
        # subscription-related. If this shows timeouts, the fault cannot be
        # about listener actors.
        timings, timeouts = [], 0
        for _ in range(args.iterations):
            t0 = time.monotonic()
            try:
                conn.request_receive(conn.remote(), get_global_playhead_events_atom())
                timings.append(time.monotonic() - t0)
            except Exception:
                timeouts += 1
        print("Phase 1 — bare request_receive (no subscriptions):")
        _summarise("get_global_playhead_events_atom", timings, timeouts)

        if ph_actor is None:
            print("\n  No playhead actor reachable — phases 2/3 skipped.")
            print("  (Open a session with media, or use --launch and add media.)")
            return 0

        # ── Phase 2: construct and discard ───────────────────────────────
        # Each construction subscribes (ModuleBase.setup_message_handler) and
        # performs ~46 attribute round-trips. Discarded immediately.
        timings, timeouts = [], 0
        for _ in range(args.iterations):
            t0 = time.monotonic()
            try:
                Playhead(conn, ph_actor)
                timings.append(time.monotonic() - t0)
            except Exception:
                timeouts += 1
        print("\nPhase 2 — Playhead(...) constructed and discarded:")
        _summarise("Playhead ctor", timings, timeouts)

        # ── Phase 3: accumulate ──────────────────────────────────────────
        # Holding references keeps every listener alive. A latency curve that
        # climbs with the number held is the volume-triggered explanation; a
        # flat one rules it out.
        if args.hold:
            held, timings, timeouts = [], [], 0
            print("\nPhase 3 — Playhead(...) held (listeners accumulate):")
            for i in range(args.iterations):
                t0 = time.monotonic()
                try:
                    held.append(Playhead(conn, ph_actor))
                    timings.append(time.monotonic() - t0)
                except Exception:
                    timeouts += 1
                if (i + 1) % 10 == 0:
                    recent = timings[-10:]
                    if recent:
                        print(f"    after {i + 1:>3} held: "
                              f"last-10 mean={sum(recent) / len(recent):7.4f}s "
                              f"timeouts so far={timeouts}")
            _summarise("Playhead ctor (held)", timings, timeouts,
                       extra=f"  held={len(held)}")

        # ── Phase 4: concurrent callers ──────────────────────────────────
        # The live plugin has a poll thread and xStudio callback threads both
        # issuing synchronous requests on the same connection. Serial phases
        # cannot show contention between them.
        if args.threads > 1:
            import threading
            results = {}

            def _hammer(idx):
                local_t, local_to = [], 0
                for _ in range(args.iterations):
                    t0 = time.monotonic()
                    try:
                        Playhead(conn, ph_actor)
                        local_t.append(time.monotonic() - t0)
                    except Exception:
                        local_to += 1
                results[idx] = (local_t, local_to)

            # Header first, and a bounded join: a deadlock here is a RESULT, so
            # it must be reported rather than inherited by this script.
            print(f"\nPhase 4 — {args.threads} threads constructing concurrently:")
            workers = [threading.Thread(target=_hammer, args=(i,), name=f"hammer-{i}",
                                        daemon=True)
                       for i in range(args.threads)]
            t0 = time.monotonic()
            for w in workers:
                w.start()
            budget = max(60.0, 2.0 * args.threads * args.iterations)
            for w in workers:
                w.join(timeout=max(1.0, budget - (time.monotonic() - t0)))
            wall = time.monotonic() - t0
            stuck = [w.name for w in workers if w.is_alive()]
            all_t = [v for tl, _ in results.values() for v in tl]
            all_to = sum(to for _, to in results.values())
            _summarise("Playhead ctor (concurrent)", all_t, all_to,
                       extra=f"  wall={wall:.2f}s")
            print(f"    completed {len(results)}/{args.threads} threads;"
                  f" expected {args.threads * args.iterations} constructions,"
                  f" got {len(all_t) + all_to}")
            if stuck:
                print(f"    *** DEADLOCK: {len(stuck)} thread(s) never returned"
                      f" after {budget:.0f}s: {', '.join(stuck)}")
                print("    *** A synchronous request never completed. This is the"
                      " finding — concurrent callers on one connection.")

        # ── Phase 5: request_receive from inside an event callback ───────
        # This is what the sync plugin actually does (_adopt_playhead ->
        # _carry_over_playback_mode -> refresh_playhead_attributes runs on an
        # xStudio callback thread and issues synchronous requests). The
        # plugin's own threading invariant forbids it; under one shared
        # listener it worked anyway. With a listener per subscription, a
        # request issued from inside that listener's callback may have no one
        # left to pump its response.
        if args.in_callback:
            import threading
            from xstudio.core import get_event_group_atom
            done = threading.Event()
            outcome = {"calls": 0, "timeouts": 0, "max": 0.0}

            def _cb(*_args, **_kwargs):
                t0 = time.monotonic()
                try:
                    conn.request_receive(conn.remote(),
                                         get_global_playhead_events_atom())
                    outcome["calls"] += 1
                    outcome["max"] = max(outcome["max"], time.monotonic() - t0)
                except Exception as exc:
                    outcome["timeouts"] += 1
                    outcome["err"] = repr(exc)
                done.set()

            print("\nPhase 5 — request_receive from inside an event callback:")
            try:
                sess = conn.api.session
                grp = conn.request_receive(sess.remote, get_event_group_atom(), True)[0]
                conn.link.add_message_callback(grp, _cb)
                # Provoke session events until the callback has fired.
                for i in range(args.iterations):
                    try:
                        sess.create_playlist(f"repro_{i}")
                    except Exception:
                        pass
                    if done.wait(0.2):
                        break
                if outcome["calls"] or outcome["timeouts"]:
                    print(f"    calls_ok={outcome['calls']} "
                          f"timeouts={outcome['timeouts']} "
                          f"max={outcome['max']:.4f}s "
                          f"{outcome.get('err', '')}")
                else:
                    print("    callback never fired — inconclusive, not a pass")
            except Exception as exc:
                print(f"    could not set up: {exc!r}")

        # ── Phase 6: are callbacks serialised or concurrent? ─────────────
        # The decisive question for "why is the PR worse if the defect is
        # pre-existing?". On develop every subscription on a connection shares
        # ONE listener actor, so callbacks necessarily enter Python one at a
        # time. Per-subscription listeners can run them at once. If so, the PR
        # does not add a defect — it removes an accidental serialisation that
        # was masking the connection's lack of thread-safety.
        #
        # Measures actual overlap, not thread names alone: a callback that
        # sleeps briefly lets genuine concurrency show up as overlapping
        # intervals.
        if args.concurrency_probe:
            import threading
            from xstudio.core import get_event_group_atom
            lock = threading.Lock()
            spans, threads_seen = [], set()

            def _make_cb(_i):
                def _cb(*_a, **_kw):
                    t0 = time.monotonic()
                    threads_seen.add(threading.current_thread().name)
                    time.sleep(0.05)  # widen the window so overlap is visible
                    with lock:
                        spans.append((t0, time.monotonic()))
                return _cb

            print(f"\nPhase 6 — {args.subscriptions} subscriptions on one group,"
                  " callback overlap:")
            try:
                sess = conn.api.session
                # No trailing True: that form is Module-specific
                # (ModuleBase.setup_message_handler). The generic idiom is
                # subscribe_to_event_group's plain get_event_group_atom().
                grp = conn.request_receive(sess.remote, get_event_group_atom())[0]
                if not grp:
                    raise RuntimeError("session actor has no event group")
                for i in range(args.subscriptions):
                    conn.link.add_message_callback(grp, _make_cb(i))
                for i in range(args.iterations):
                    try:
                        sess.create_playlist(f"probe_{i}")
                    except Exception:
                        pass
                    time.sleep(0.05)
                time.sleep(2.0)  # let the tail drain

                # Max number of callback intervals overlapping at any instant.
                peak = 0
                for s, _e in spans:
                    peak = max(peak, sum(1 for s2, e2 in spans if s2 <= s < e2))
                print(f"    callback invocations : {len(spans)}")
                print(f"    distinct threads     : {len(threads_seen)}"
                      f" {sorted(threads_seen)}")
                print(f"    peak overlap         : {peak}")
                if peak > 1:
                    print("    *** CONCURRENT: callbacks run at the same time."
                          " Any request_receive inside one can collide.")
                elif spans:
                    print("    *** SERIALISED: callbacks never overlap."
                          " A shared listener is absorbing the concurrency.")
                else:
                    print("    no callbacks fired — inconclusive, not a pass")
            except Exception as exc:
                print(f"    could not set up: {exc!r}")

        print("\nInterpretation:")
        print("  Phase 1 timeouts        -> request/response itself is affected"
              " (upstream bug, unrelated to subscriptions).")
        print("  Phase 1 clean, 2/3 slow -> cost/contention is in wrapper"
              " construction + subscription.")
        print("  Phase 3 climbing        -> accumulating listeners are the"
              " trigger (volume-dependent).")
        print("  Phase 3 flat            -> accumulation ruled out.")
        print("  Phase 4 timeouts        -> concurrent callers on one connection"
              " contend (matches poll thread + xStudio callback threads).")
        print("  Phase 6 peak overlap >1 -> callbacks are concurrent; a shared"
              " listener is no longer serialising them, so the connection's"
              " lack of thread-safety is now reachable from event handlers.")
        print("  Phase 5 timeouts        -> a synchronous request issued from"
              " inside an event callback cannot complete; the plugin does"
              " exactly this and its own threading invariant forbids it.")
        return 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
            print(f"\nxStudio terminated (log kept at {log_path})")


if __name__ == "__main__":
    sys.exit(main())
