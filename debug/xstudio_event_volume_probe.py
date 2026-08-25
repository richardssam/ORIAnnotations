"""Tally xStudio broadcast volume by sender and message shape.

Answers "which event group is noisiest, and would quieting it help?" — the
question that matters because `Connection.request_receive` doubles as the event
pump: any message it dequeues that is not the response it is waiting for is
dispatched via `handle_broadcast` *inline on the calling thread*. So a bounded
read's latency is a function of unrelated event volume, and reducing that volume
is a lever the plugin controls, unlike the upstream thread-safety fix.

Install from the plugin's __init__ (see ori_sync_plugin) and it prints a ranked
summary on an interval and at teardown.
"""
import collections
import threading
import time


def install(conn, log, interval=8.0):
    counts = collections.Counter()
    by_thread = collections.Counter()
    lock = threading.Lock()
    state = {"t0": time.monotonic(), "last": time.monotonic(), "total": 0}

    orig = conn.handle_broadcast

    def shape(event):
        try:
            parts = [type(e).__name__ for e in event[:3]]
            return "/".join(parts)
        except Exception:
            return "?"

    def handle_broadcast(event):
        try:
            with lock:
                counts[shape(event)] += 1
                by_thread[threading.current_thread().name] += 1
                state["total"] += 1
                now = time.monotonic()
                due = now - state["last"] >= interval
                if due:
                    state["last"] = now
            if due:
                dump(log)
        except Exception:
            pass
        return orig(event)

    def dump(log):
        with lock:
            elapsed = time.monotonic() - state["t0"]
            total = state["total"]
            top = counts.most_common(12)
            threads = by_thread.most_common(6)
        log(f"[EVENTVOL] {total} broadcasts in {elapsed:.1f}s ({total/max(elapsed,1e-9):.1f}/s)")
        for name, n in top:
            log(f"[EVENTVOL]   {n:6d}  {100.0*n/max(total,1):5.1f}%  {name}")
        log(f"[EVENTVOL]   by thread: {', '.join(f'{t}={n}' for t, n in threads)}")

    conn.handle_broadcast = handle_broadcast
    return dump


def install_plugin_fanout(plugin, log, interval=8.0):
    """Tally the plugin's OWN event traffic: event-group fan-out + playhead attrs.

    `Connection.handle_broadcast` turned out to see almost none of this — under
    the per-subscription-listener build the event-group callbacks are delivered
    by their own listener threads and never pass through it. This wraps the two
    places every xStudio event actually reaches this plugin:

      * `_dispatch_event_group` — one call per event per joined group
      * `playhead_attribute_changed` — the ~60 Hz scrub path

    Counts by (label, message shape) so a noisy *group* and a noisy *message
    type within a group* are distinguishable — the second is what you can
    usually do something about.
    """
    import collections, threading, time
    counts = collections.Counter()
    threads = collections.Counter()
    lock = threading.Lock()
    state = {"t0": time.monotonic(), "last": time.monotonic(), "total": 0}

    def shape(event):
        try:
            return "/".join(type(e).__name__ for e in event[:2])
        except Exception:
            return "?"

    def bump(tag):
        with lock:
            counts[tag] += 1
            threads[threading.current_thread().name] += 1
            state["total"] += 1
            now = time.monotonic()
            due = now - state["last"] >= interval
            if due:
                state["last"] = now
                elapsed = now - state["t0"]
                total = state["total"]
                top = counts.most_common(14)
                th = threads.most_common(8)
        if due:
            log(f"[FANOUT] {total} plugin events in {elapsed:.1f}s "
                f"({total/max(elapsed,1e-9):.1f}/s)")
            for name, n in top:
                log(f"[FANOUT]   {n:6d}  {100.0*n/max(total,1):5.1f}%  {name}")
            log(f"[FANOUT]   by thread: {', '.join(f'{t}={n}' for t, n in th)}")

    orig_disp = plugin._dispatch_event_group
    def _dispatch_event_group(key, *a, **k):
        event = a[0] if a else None
        try:
            labels = [l for l, _ in plugin._event_group_subs.get(key, {}).get("callbacks", [])]
        except Exception:
            labels = []
        bump(f"group[{','.join(labels) or key[:16]}] {shape(event)}")
        return orig_disp(key, *a, **k)
    plugin._dispatch_event_group = _dispatch_event_group

    orig_attr = plugin.playhead_attribute_changed
    def playhead_attribute_changed(attr, role):
        bump("playhead_attribute_changed")
        return orig_attr(attr, role)
    plugin.playhead_attribute_changed = playhead_attribute_changed


def install_playhead_census(plugin, playhead_cls, log, interval=8.0):
    """Count Playhead constructions and live listener threads over time.

    Under the per-subscription-listener build, constructing a `Playhead`
    subscribes it to that playhead's attribute-events group, and each
    subscription gets its own listener thread that is never left. So wrapper
    churn converts directly into a growing population of threads all blocking
    on the one shared `dequeue_message_with_timeout` — which is a per-
    subscription cost, not a per-event one.

    The plugin's own comment predicted exactly this ("Playhead construction
    stops being free"); this measures whether it happened.
    """
    import threading, time
    state = {"ctors": 0, "t0": time.monotonic(), "last": time.monotonic()}
    orig_init = playhead_cls.__init__

    def __init__(self, *a, **k):
        state["ctors"] += 1
        return orig_init(self, *a, **k)
    playhead_cls.__init__ = __init__

    def census():
        now = time.monotonic()
        if now - state["last"] < interval:
            return
        state["last"] = now
        names = [t.name for t in threading.enumerate()]
        dummies = [n for n in names if n.startswith("Dummy")]
        log(f"[CENSUS] t={now - state['t0']:5.1f}s  Playhead ctors={state['ctors']:4d}"
            f"  live threads={len(names):3d}  listener(Dummy) threads={len(dummies):3d}")
    return census


def install_subscription_census(conn, log):
    """Count subscriptions vs DISTINCT groups subscribed to.

    Decides whether "one listener per group" would be cheaper than the PR's
    "one listener per subscription". The PR spawns a listener actor per call to
    add_message_callback; a per-group scheme would spawn one per distinct group
    actor and fan out in Python. The saving is exactly the duplicate rate
    measured here, so if every subscription targets a distinct group the two
    schemes cost the same and this idea is worthless.
    """
    import collections, threading
    per_group = collections.Counter()
    lock = threading.Lock()
    real = conn.link
    orig = real.add_message_callback

    class _LinkProxy:
        # conn.link is a pybind object whose attributes cannot be set, so the
        # interception has to happen one level up: every caller reaches it as
        # `self.connection.link`, and that IS a plain Python attribute.
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_inner"), name)

        def add_message_callback(self, *a, **k):
            return add_message_callback(*a, **k)

    def add_message_callback(*a, **k):
        try:
            grp = a[0] if a else None
            with lock:
                per_group[str(grp)] += 1
                total = sum(per_group.values())
                distinct = len(per_group)
                dupes = total - distinct
            log(f"[SUBS] subscriptions={total} distinct_groups={distinct} "
                f"duplicates={dupes}  (per-subscription listeners={total}, "
                f"per-group listeners={distinct})")
            if per_group[str(grp)] > 1:
                import traceback
                frames = [f for f in traceback.format_stack()[:-1]
                          if "ORIAnnotations" in f or "site-packages/xstudio" in f]
                log("[SUBS] DUPLICATE subscription to a group already joined:\n"
                    + "".join(frames[-4:]))
        except Exception:
            pass
        return orig(*a, **k)

    conn.link = _LinkProxy(real)
