## 1. SyncManager: insert_child support

- [x] 1.1 Add `insert_child(parent_uuid, child_obj, index=-1)` method to `SyncManager` that mutates the local OTIO object, registers the child, and broadcasts the delta
- [x] 1.2 Update `apply_patch` to handle `action: "insert_child"` — deserialise `child_json`, insert into parent, register child, return the inserted object
- [x] 1.3 Update `receive_and_apply_all` to return a list of `(action, result)` tuples instead of just a count, so callers know what was inserted

## 2. OpenRV Plugin: bootstrap and menu

- [x] 2.1 Add `SYNC_DEMO_TRACK_UUID` constant and bootstrap logic — create `Timeline → Track`, stamp the Track with the well-known UUID, and register with `SyncManager` on plugin `__init__`
- [x] 2.2 Add "OTIO Sync" menu definition with "Add Clip to Timeline..." and "Sync Status" items
- [x] 2.3 Implement `do_add_clip` handler — call `rv.commands.openMediaFileDialog`, create an `otio.schema.Clip`, call `SyncManager.insert_child`, then `rv.commands.addSource(path)`
- [x] 2.4 Implement `do_show_status` handler — print session ID, object map size, and network broadcast address to the RV console

## 3. OpenRV Plugin: receiver-side loading

- [x] 3.1 Update `poll_network` to use the new `receive_and_apply_all` return value and call `rv.commands.addSource(path)` for each inserted clip received from a remote peer
