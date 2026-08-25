# Temporary instrumentation: log every request_receive that is NOT on the poll thread.
import threading, traceback
def install(conn, log):
    orig = conn.request_receive
    orig_t = conn.request_receive_timeout
    seen = set()
    def _note(tag):
        t = threading.current_thread().name
        if t == "ori_sync_poll" or t == "MainThread":
            return
        st = "".join(traceback.format_stack()[-6:-2])
        key = (t.split("-")[0], st[-400:])
        if key in seen:
            return
        seen.add(key)
        log(f"[THREADPROBE] {tag} on thread {t!r}\n{st}")
    def rr(*a, **k):
        _note("request_receive"); return orig(*a, **k)
    def rrt(*a, **k):
        _note("request_receive_timeout"); return orig_t(*a, **k)
    conn.request_receive = rr
    conn.request_receive_timeout = rrt

# ── how this was used, and what it found ──────────────────────────────────────
#
# Installed temporarily in ori_sync_plugin.__init__ (right after
# subscribe_to_global_playhead_events) during the poll-thread-owns-actor-io
# verification, to answer "who is still issuing actor I/O off the poll thread?"
#
# Result, 2026-08-25, PR build (pr/python-per-subscription-listeners):
#
#   7 hits, ALL from 'Thread-5 (_search_worker)' in xStudio's own bundled
#   plugin-python/filesystem_browser_python/filesystem_browser.py, calling
#   .value() on its module attributes.
#   ZERO hits from any ORIAnnotations module.
#
# The decisive part is that this patch replaces the method on *our* plugin's
# `self.connection` instance. Intercepting another plugin's calls therefore
# proves the two plugins share one Connection object — so "the poll thread is
# the only thread doing actor I/O" is a property no single plugin can
# establish on its own.
