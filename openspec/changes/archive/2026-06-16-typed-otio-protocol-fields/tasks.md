## 1. Conversion helpers in protocol_messages.py

- [x] 1.1 Add private `_to_wire(obj)` and `_from_wire(d)` helpers that lazily `import opentimelineio` and mirror the exact existing format (`write_to_string(obj, "otio_json", indent=-1)` + `json.loads`; `read_from_string(json.dumps(d))`). Keep them module-private; no top-level OTIO import.
- [x] 1.2 Collection accessors use `_from_wire` directly — it already passes through non-dict (OTIO) elements, so no separate `_as_otio_element` helper was needed.

## 2. Migrate the four OTIO-bearing messages

- [x] 2.1 `AddTimeline`: type `timeline` as `Timeline | dict`; `to_payload()` coerces via `_to_wire`; add `as_otio() -> Timeline` (passthrough if already object). Keep `doc_field` doc text accurate.
- [x] 2.2 `StateSnapshot`: type `timelines` as `dict[str, Timeline | dict]`; `to_payload()` coerces each value; add `as_otio() -> dict[str, Timeline]`.
- [x] 2.3 `InsertChild`: type `child_data` as `SerializableObject | dict`; `to_payload()` coerces; add `as_otio() -> SerializableObject`.
- [x] 2.4 `ReplaceAnnotationCommands`: type `commands` as `list[SerializableObject | dict]`; `to_payload()` coerces each element; add `as_otio() -> list[SerializableObject]`.
- [x] 2.5 Confirm `from_payload()` on all four stores the raw wire form unchanged (no deserialization) — adjust only if needed.
- [x] 2.6 Leave `PartialAnnotation.events` untouched (still `list[dict]`); add a code comment noting the deliberate hot-path exclusion.

## 3. Update producers (manager.py / patcher.py)

- [x] 3.1 `manager.broadcast_add_timeline`: pass the `Timeline` object directly to `AddTimeline` (drop `_otio_to_dict`).
- [x] 3.2 `manager.send_state_snapshot`: pass the `{guid: Timeline}` map directly to `StateSnapshot` (drop the dict comprehension `_otio_to_dict`).
- [x] 3.3 `manager.broadcast_replace_annotation_commands`: pass the `otio_events` list directly to `ReplaceAnnotationCommands` (drop `[_otio_to_dict(e)…]`).
- [x] 3.4 `manager.broadcast_add_annotation` (delta-clip path): pass `delta_clip` directly to `InsertChild` (drop `_otio_to_dict`).
- [x] 3.5 `patcher.insert_child`: pass `child_obj` directly to `InsertChild` (drop `_otio_to_dict`).

## 4. Update consumers (handlers / apply_patch)

- [x] 4.1 `manager._h_add_timeline`: keep the `tl_guid not in self._timelines` guard first; call `msg.as_otio()` only after the guard passes.
- [x] 4.2 State-snapshot consumer is `apply_snapshot`, which works on the raw wire payload (`snapshot_data["timelines"]`) and already deserializes lazily per-entry (it even peeks `metadata` before deserializing). Left as-is — no message object reaches it. `StateSnapshot.as_otio()` added for symmetry/tests.
- [x] 4.3 `patcher.apply_patch` `InsertChild` branch: replace `_dict_to_otio(msg.child_data)` with `msg.as_otio()`.
- [x] 4.4 `patcher.apply_patch` `ReplaceAnnotationCommands` branch: replace per-element `_dict_to_otio` loop with `msg.as_otio()`.
- [x] 4.5 Remove now-unused `_otio_to_dict`/`_dict_to_otio` references at the migrated sites; keep the helpers only if still used elsewhere.

## 5. Tests & verification

- [x] 5.1 Round-trip test per message: build from an OTIO object → `to_payload()` → `from_payload()` → `as_otio()` reproduces an equivalent object.
- [x] 5.2 Wire byte-equivalence test: `to_payload()` output equals a captured payload from the prior pre-serialized implementation for each of the four messages.
- [x] 5.3 Lazy-deserialize test: `from_payload()` does not invoke OTIO deserialization (e.g. patch/spy `_from_wire`); `as_otio()` does.
- [x] 5.4 Pre-guard skip test: a duplicate `AddTimeline` for a known GUID is rejected without calling `as_otio()`.
- [x] 5.5 Import-isolation test: import `protocol_messages` with `opentimelineio` unavailable (simulate via import hook) and assert classes + `doc_fields()` are accessible.
- [x] 5.6 Run the existing otio_sync_core test suite to confirm no regressions in dispatch, hot paths, or interop.
