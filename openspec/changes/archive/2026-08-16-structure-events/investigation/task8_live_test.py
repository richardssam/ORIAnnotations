#!/usr/bin/env python
"""Task 8.2: live two-peer test — create a sequence inside an existing
playlist on peer1 (the proposal's failure case), and measure discovery-to-
broadcast latency by reading the ori_sync plugin's own log timestamps.

Connects to peer1's xStudio API (port 14441) as a separate client — acting as
"the interactive user" — while peer1's own already-running ori_sync plugin
(loaded via XSTUDIO_PYTHON_PLUGIN_PATH) does the real discovery/broadcast work
under test.
"""
import re
import sys
import time

from xstudio.connection import Connection

PEER1_LOG = "/private/tmp/claude-501/-Users-sam-git-ORIAnnotations/85e047d7-11c5-4633-9277-7c0721afad00/scratchpad/peer1_plugin.log"
PEER2_LOG = "/private/tmp/claude-501/-Users-sam-git-ORIAnnotations/85e047d7-11c5-4633-9277-7c0721afad00/scratchpad/peer2_plugin.log"


def log(msg):
    print(f"[driver {time.monotonic():.3f}] {msg}", flush=True)


def main():
    conn = Connection(auto_connect=False)
    conn.connect_remote("127.0.0.1", 14441)
    session = conn.api.session
    log("connected to peer1 API (14441)")

    pl_uuid, pl = session.create_playlist("Task8 Existing Playlist")
    log(f"created playlist {pl.name!r} uuid={pl_uuid}")
    time.sleep(2.0)  # let it settle/publish as its own structural change first

    t_create = time.time()
    seq_uuid, seq = pl.create_timeline("Task8 New Sequence")
    log(f"[T_CREATE={t_create:.3f}] created sequence {seq.name!r} inside existing playlist")

    # Poll peer1's plugin log for its own broadcast, and peer2's for receipt.
    deadline = time.time() + 15.0
    found_peer1_broadcast = None
    found_peer2_recv = None
    while time.time() < deadline and (found_peer1_broadcast is None or found_peer2_recv is None):
        time.sleep(0.1)
        if found_peer1_broadcast is None:
            try:
                with open(PEER1_LOG) as f:
                    text = f.read()
                if "Task8 New Sequence" in text and "broadcast" in text:
                    for line in text.splitlines():
                        if "Task8 New Sequence" in line and "broadcast" in line:
                            found_peer1_broadcast = line
                            break
            except FileNotFoundError:
                pass
        if found_peer2_recv is None:
            try:
                with open(PEER2_LOG) as f:
                    text = f.read()
                if "ADD_TIMELINE" in text and "Task8 New Sequence" in text:
                    for line in text.splitlines():
                        if "ADD_TIMELINE" in line:
                            found_peer2_recv = line
                # ADD_TIMELINE lines may not carry the name; fall back to any
                # ADD_TIMELINE line that appeared after t_create.
            except FileNotFoundError:
                pass

    log(f"peer1 broadcast line: {found_peer1_broadcast!r}")
    log(f"peer2 receipt line:   {found_peer2_recv!r}")

    if found_peer1_broadcast is None:
        log("FAIL: peer1 never broadcast the new sequence within 15s")
        sys.exit(1)

    log("done")


if __name__ == "__main__":
    main()
