#!/usr/bin/env python
"""Task 8.6: with ORI_STRUCTURE_EVENTS=0 on peer3, confirm structure is still
detected and published by the poll alone (no event-driven fast path)."""
import time

from xstudio.connection import Connection

conn = Connection(auto_connect=False)
conn.connect_remote("127.0.0.1", 14443)
session = conn.api.session

pl_uuid, pl = session.create_playlist("Task8.6 SwitchOff Playlist")
time.sleep(2.0)

t_create = time.time()
seq_uuid, seq = pl.create_timeline("Task8.6 SwitchOff Seq")
print(f"[T_CREATE={t_create:.3f}] created sequence on peer3 (switch off)", flush=True)
time.sleep(3.0)
print("done", flush=True)
