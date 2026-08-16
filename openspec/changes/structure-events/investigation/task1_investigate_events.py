#!/usr/bin/env python
"""
OpenSpec structure-events, task 1: prove the events arrive.

Runs against the xStudio Python API embedded engine (no live GUI/session
needed — Connection(auto_connect=True) starts an in-process actor system).
Joins the session's event group and a playlist's event group, subscribes
BEFORE any driving action, and logs every message received, unfiltered, so
the log itself is the evidence for 1.3-1.8.

Run with the repo's embedded interpreter:
  /Users/sam/git/xstudio/build/vcpkg_installed/arm-osx/tools/python3/python3 \
      task1_investigate_events.py
"""
import time
import traceback

from xstudio.connection import Connection
from xstudio.core import get_event_group_atom
from xstudio.core import (
    add_playlist_atom,
    create_timeline_atom,
    rename_container_atom,
    remove_container_atom,
)

LOG = []


def log(msg):
    line = f"[{time.monotonic():.3f}] {msg}"
    print(line, flush=True)
    LOG.append(line)


def join_group(conn, actor_obj, label):
    """Mirror ori_sync_plugin.join_event_group without needing a plugin instance."""
    event_group = conn.request_receive(actor_obj.remote, get_event_group_atom())[0]
    if not event_group:
        log(f"[{label}] actor has no event group")
        return None

    def cb(event):
        try:
            payload = event.value if hasattr(event, "value") else event
        except Exception:
            payload = event
        log(f"[{label}] RAW EVENT: type={type(payload)} repr={payload!r}")

    sub_id = conn.link.add_message_callback(event_group, cb)
    log(f"[{label}] joined group={event_group} sub_id={sub_id}")
    return sub_id


def main():
    conn = Connection(auto_connect=True)
    session = conn.api.session
    log(f"connected, session={session}")

    # 1.2/1.3 — join the SESSION group before creating anything.
    join_group(conn, session, "SESSION")

    log("--- creating playlist 'Task1 Playlist' ---")
    pl_uuid, pl = session.create_playlist("Task1 Playlist")
    time.sleep(0.5)
    log(f"playlist created: uuid={pl_uuid} pl.uuid={pl.uuid} name={pl.name}")

    # 1.4 — now join the PLAYLIST group and create a sequence inside it.
    join_group(conn, pl, "PLAYLIST")

    log("--- creating sequence 'Task1 Sequence' inside playlist ---")
    seq_uuid, seq = pl.create_timeline("Task1 Sequence")
    time.sleep(0.5)
    log(f"sequence created: uuid={seq_uuid} seq.uuid={seq.uuid} name={seq.name}")

    # 1.5a — rename the SEQUENCE (child of playlist) via the playlist's own
    # rename_container, to see which group (if any) carries it.
    log("--- renaming the SEQUENCE via pl.rename_container (child-of-playlist route) ---")
    ok = pl.rename_container("Task1 Sequence Renamed", seq_uuid)
    log(f"pl.rename_container -> {ok}")
    time.sleep(0.5)

    # 1.5b — rename the PLAYLIST itself via session.rename_container (the
    # citation in design.md is session_actor.cpp, i.e. top-level containers).
    log("--- renaming the PLAYLIST via session.rename_container (session-level route) ---")
    ok = session.rename_container("Task1 Playlist Renamed", pl_uuid)
    log(f"session.rename_container -> {ok}")
    time.sleep(0.5)

    # 1.6a — remove the SEQUENCE (child of playlist) via pl.remove_container.
    log("--- removing the SEQUENCE via pl.remove_container (child-of-playlist route) ---")
    ok = pl.remove_container(seq_uuid)
    log(f"pl.remove_container -> {ok}")
    time.sleep(0.5)

    # 1.7 — let sibling traffic accumulate a bit.
    log("--- idling 2s to observe any further sibling-group traffic ---")
    time.sleep(2.0)

    # 1.6b — remove the PLAYLIST itself via session.remove_container.
    log("--- removing the PLAYLIST via session.remove_container (session-level route) ---")
    ok = session.remove_container(pl_uuid)
    log(f"session.remove_container -> {ok}")
    time.sleep(0.5)

    log("done")
    conn.disconnect()


if __name__ == "__main__":
    main()
