# sync-source-view-playback

## Why

When a peer views a single clip (source view rather than sequence view), its playback state does not reach the other peers. Scrub or stop in source view and the others keep playing; nothing in either log reports an error.

The `xstudio_selects` sync test has been failing on exactly this and was misread as a recording defect. It is not: the recording is coherent, contains no deletions, and asks for nothing impossible.

In source view the sender broadcasts `PLAYBACK_SETTINGS_1.0` with `timeline_guid` set to the **clip timeline** for the viewed clip. xStudio's receiver compares that against the locally-viewed timeline, finds no match, logs `RECV playback state: mismatched timeline_guid — ignoring (not playing)`, and drops the message.

The important part is that the guid is not unknowable. `SyncManager.get_or_create_clip_timeline` derives it as `uuid5(NAMESPACE_OID, "clip_timeline:<clip_guid>")`, precisely so that "all peers independently compute the same GUID … without any coordination message". Both guids in the failing recording check out against that formula:

| selected clip | derived clip timeline | appears in recording |
| --- | --- | --- |
| `1905e6762e13…` | `be65c6d3-f050-50dc-…` | yes, the play at t=30.0 |
| `90063626d29c…` | `28f72b56-5f91-5328-…` | yes, the stop at t=36.2 |

So the receiver already holds everything needed to resolve the guid — it has the clip guid from the preceding `SELECTION_1.0` message, and the same derivation function. It simply never tries, and treats a resolvable clip timeline as an unknown one.

## What Changes

- A receiver SHALL resolve an incoming `timeline_guid` that is a clip timeline for a clip it knows, and follow that playback instead of discarding it.
- Resolution SHALL use the existing deterministic derivation, so no new field, message, or coordination step is added to the protocol.
- A `timeline_guid` that resolves to no known clip and no known timeline SHALL still be ignored, as now.
- The frame in a source-view message is clip-local; applying it SHALL land on the same image the sender is showing, in both hosts.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `xstudio-clip-selection-sync`: it already governs source-mode selection over `PLAYBACK_SETTINGS_1.0`, but says nothing about playback state that arrives *addressed to* the isolated clip. Isolating a clip is specified to seek and loop both peers; playback within that isolated view must be followable too, or the two peers diverge the moment either scrubs.

## Impact

- `xstudio_plugin/ori_sync/playback_sync.py` — the mismatch guard in the playback receive path.
- `rvplugin/ori_sync/playback_sync.py` — `_apply_playback` has no timeline guard at all, yet RV also failed to follow the stop in the same run. Its half of the failure is **not yet diagnosed** and may be a different cause; it needs its own investigation before any fix is written.
- `sync_test/recordings/xstudio_selects.jsonl` — the test that has been failing on this. No change needed to the recording; it should start passing once both hosts follow source-view playback.
- No protocol, schema, or message-shape change.
