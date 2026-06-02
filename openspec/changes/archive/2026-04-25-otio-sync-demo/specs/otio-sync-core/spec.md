## ADDED Requirements

### Requirement: SyncManager supports insert_child mutations
The `SyncManager` SHALL provide an `insert_child(parent_uuid, child_obj, index=-1)` method that inserts `child_obj` into the children of the object identified by `parent_uuid`, registers the new child in `_object_map`, and broadcasts an `insert_child` delta payload containing the parent UUID, the insertion index, and the full OTIO-JSON serialisation of the child.

#### Scenario: Insert child broadcasts a delta
- **WHEN** `SyncManager.insert_child(parent_uuid, clip, index=-1)` is called and `_is_syncing` is False
- **THEN** a UDP payload with `action: "insert_child"`, `parent_uuid`, `index`, and `child_json` SHALL be broadcast on the sync port

---

### Requirement: apply_patch handles insert_child action
The `SyncManager.apply_patch` method SHALL, when given a payload with `action: "insert_child"`, deserialise `child_json` using `otio.adapters.read_from_string`, insert the child into the parent's `children` list at the specified index, call `_ensure_guid_and_map` on the new child to register it, set `_is_syncing = True` during the operation, and return the inserted child object so the caller can take further action (e.g. loading the media path in RV).

#### Scenario: Patch applied silently
- **WHEN** `apply_patch` is called with an `insert_child` payload while `_is_syncing` is False
- **THEN** the child SHALL be inserted into the correct parent container and registered in `_object_map` without re-broadcasting a delta

#### Scenario: Inserted child is returned
- **WHEN** `apply_patch` processes an `insert_child` action successfully
- **THEN** it SHALL return the deserialised child object so callers can inspect its `source_range` or media reference path
