## Why

A peer can mutate its own structure in a way that **changes an object's sync
GUID**, and then keep broadcasting child patches against the new GUID without
ever telling anyone the object exists. Every one of those patches names a parent
no peer holds, and every one is dropped in silence. The sending peer believes it
published the media; the receiving peers believe they are synced; nobody is told
otherwise.

Measured on the two peers of `openrv_hosts_selection` (diagnostic instrumentation,
§1 of this change):

```
master   12:00:54  Add: insert_child done (object_map has track: True)
master   12:00:54  init tracks for defaultSequence: ['Media', 'Annotations']
master             insert_child broadcasting: parent=fd37603f … index=0
master             insert_child broadcasting: parent=a6482222 … index=1..7
follower 12:00:58  DIAG drop: INSERT_CHILD parent a6482222 not in object_map
                   (5 objects held)                              ×7
```

The master's media track is re-initialised after the first media add and comes
back with a different GUID (`fd37603f` → `a6482222`). The follower holds the
pre-re-init structure — 5 objects — so all seven subsequent insertions are
orphaned. Its node graph stays empty (0 RV source nodes against the master's 16)
while it reports `STATE_SYNCED`.

**Two earlier explanations were tested and ruled out**, and are recorded so they
are not re-adopted:

- *`SyncManager._h_add_timeline`'s duplicate-GUID guard discards a populated
  timeline.* Instrumented; the branch never fired.
- *The `STATE_JOINING` delta buffer drops the messages on its
  `sync_timestamp > snapshot_timestamp` comparison.* Instrumented; nothing was
  buffered and nothing was dropped as stale. (The comparison does read
  `sync_timestamp` at the wrong envelope level and is therefore always `0` —
  worth fixing on its own, but it is not what caused this.)

Only the third explanation is supported by evidence, and it is the one the
symptom pointed at least obviously: the problem is not on the receiving side at
all.

**Pre-existing, and not caused by `host-owned-visibility`.** That change only
made it observable, by reporting a view it could not mirror instead of silently
staying put. It went unnoticed because no existing test asks a peer to *display*
media added after it joined: the RV↔RV tests assert OTIO structure, which is
equally empty on both sides and therefore agrees.

## What Changes

- A structural mutation that changes an object's sync GUID SHALL publish the new
  structure before, or together with, any patch that addresses it. A peer must
  never broadcast a patch naming an object its peers cannot have.
- A patch whose target cannot be resolved SHALL be reported rather than dropped
  silently. This is what let the defect survive: eight messages sent, none
  applied, no signal anywhere.
- Prefer making the media track's identity **stable** across re-initialisation
  over re-announcing it afterwards. A GUID that changes under a live session is
  the underlying fault; re-announcing treats the symptom.
- Fix the delta-buffer replay comparison, which reads `sync_timestamp` from the
  wrong envelope level and so evaluates to `0` for every buffered message.
  Independent of the above, and currently latent.

## Capabilities

### Modified Capabilities
- `otio-sync-core`: structural patches cannot address objects peers were never
  given; unresolvable patches are reported; delta replay compares the timestamp
  it intends to.
- `openrv-sync-plugin`: track identity survives re-initialisation, so media
  added after a peer joins reach that peer and become viewable.

## Impact

- `rvplugin/ori_sync/sequence_sync.py`: track re-initialisation and the GUID it
  assigns — the origin of the defect.
- `python/otio_sync_core/patcher.py`: reporting an unresolvable parent.
- `python/otio_sync_core/manager.py`: the delta-buffer replay comparison.
- `sync_test/sync_tests.yaml`: unblocks `openrv_hosts_selection`, currently
  `known_broken` against this change.

## Naming

This was opened as `fix-orphaned-structure-patches`, when the working theory
was that OpenRV failed to materialise media it had been told about. The evidence
moved the fault to the *sending* peer and into shared core, so the name now
describes neither the cause nor the fix. Rename before implementing —
`fix-orphaned-structure-patches` fits what this actually does.
