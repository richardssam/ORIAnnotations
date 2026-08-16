#!/usr/bin/env python
"""Task 8.4: bulk case — several playlists and sequences created together on
peer1; confirm on peer1's own plugin log that no container is broadcast more
than once and every one is broadcast."""
import time

from xstudio.connection import Connection

conn = Connection(auto_connect=False)
conn.connect_remote("127.0.0.1", 14441)
session = conn.api.session

names = []
for i in range(4):
    pl_uuid, pl = session.create_playlist(f"Task8.4 Playlist {i}")
    seq_uuid, seq = pl.create_timeline(f"Task8.4 Seq {i}")
    names.append(seq.name)
    print(f"created playlist/seq pair {i}: {pl.name!r} / {seq.name!r}", flush=True)

print("waiting 5s for settle...", flush=True)
time.sleep(5.0)
print("done — check peer1_plugin.log for duplicate broadcasts", flush=True)
