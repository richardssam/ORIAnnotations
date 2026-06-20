## 1. Protocol message

- [x] 1.1 Add `RemoveTimeline` dataclass to `python/otio_sync_core/protocol_messages.py` in the Timeline family (`SCHEMA = "TIMELINE_1.0"`, `EVENT = "REMOVE_TIMELINE"`), with `timeline_guid` and `sync_timestamp` fields, `to_payload`/`from_payload`, and `@register` — mirroring `RenameTimeline`. No OTIO payload.
- [x] 1.2 In `tests/otio_sync/test_protocol_messages.py`, add round-trip coverage (build → `to_payload` → `from_payload`) and confirm the registry resolves `("TIMELINE_1.0", "REMOVE_TIMELINE")` to `RemoveTimeline`.

## 2. Manager teardown

- [x] 2.1 Register `("TIMELINE_1.0", "REMOVE_TIMELINE"): self._h_remove_timeline` in the `self._handlers` dict in `manager.py` (next to the `ADD_TIMELINE`/`RENAME_TIMELINE` entries).
- [x] 2.2 Add a private teardown helper that, given a timeline GUID, computes the set of object GUIDs in that timeline's subtree (traverse the timeline object) and removes only those from `_object_map` — never `.clear()`.
- [x] 2.3 Add the clip-annotation cascade: find every `_clip_timelines` entry whose clip GUID is in the removed subtree, and delete both that `_clip_timelines` entry and its `_timelines` entry. (Per design D3, no cross-sequence sharing, so no refcount.)
- [x] 2.4 Implement `_h_remove_timeline(msg, data, source)`: guard `guid not in _timelines` → silent no-op returning `None`; otherwise run 2.2 + 2.3, `del _timelines[guid]`, set `active_timeline_guid = None` if it equals `guid`, and return `("remove_timeline", tl)`.
- [x] 2.5 Add `broadcast_remove_timeline(guid)` symmetric to `broadcast_add_timeline`: skip when not networked/synced, remove locally via the same teardown path, then `_send_message(RemoveTimeline(...))`.

## 3. Manager tests

- [x] 3.1 Test scoped object-map teardown: register two sequence timelines, remove one, assert `_object_map` retains all of the survivor's GUIDs and none of the removed subtree's GUIDs.
- [x] 3.2 Test clip-timeline cascade: register a sequence with ≥1 annotated clip (so a clip-annotation timeline exists), remove the sequence, assert its clip timelines are gone from both `_clip_timelines` and `_timelines`.
- [x] 3.3 Test active-timeline behavior: removing the active timeline sets `active_timeline_guid = None`; removing a non-active timeline leaves it unchanged.
- [x] 3.4 Test idempotency: removing an unknown GUID makes no state change and returns `None`.
- [x] 3.5 Test the host action: a real removal returns `("remove_timeline", tl)` carrying the removed timeline.

## 4. RV host wiring

- [x] 4.1 Add deleted-sequence detection in the RV structural poll loop (counterpart to `poll_new_sequences`): when a previously-synced sequence is absent from the node graph, switch the on-screen source to a surviving sequence, then call `broadcast_remove_timeline` with its GUID.
- [x] 4.2 Handle the inbound `remove_timeline` action in the RV plugin's action router: tear down the viewer container for the removed timeline; no-op if no container exists.

## 5. xStudio host wiring

- [x] 5.1 In `StructureSyncController`, emit `broadcast_remove_timeline` when a synced playlist/timeline deletion event is observed, after the on-screen source has moved to a surviving timeline.
- [x] 5.2 Route the inbound `remove_timeline` action through the existing `_handle_manager_event` dispatch to tear down the xStudio container.

## 6. Docs & verification

- [x] 6.1 Regenerate the protocol-message reference and confirm `REMOVE_TIMELINE` now appears under the Timeline section (the omission that motivated this change).
- [x] 6.2 Run a two-peer sync test: add two sequences, delete one on the master, assert the peer drops the timeline + its container and the survivor stays active.
